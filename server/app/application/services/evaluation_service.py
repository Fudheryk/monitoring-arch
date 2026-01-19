from __future__ import annotations
"""
server/app/application/services/evaluation_service.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Service d’évaluation des seuils et gestion des alertes/incidents.

Pipeline (version refacto) :
    1. Charger les thresholds actifs d’une machine (Threshold)
    2. Pour chaque threshold + metric_instance :
         - vérifier si la métrique est alertable (pas en pause, alerting activé)
         - lire le dernier sample (Sample.metric_instance_id)
         - déduire le type et la valeur depuis le sample
         - appliquer match_condition()
         - si violation -> créer/maintenir alerte FIRING
         - sinon        -> résoudre les alertes actives pour ce threshold
    3. Ouvrir incidents si nécessaire
    4. Commit
    5. Déclencher notifications (post-commit)

Notes importantes :
- Une MetricInstance SANS SAMPLE n’est pas considérée comme en alerte ni OK → statut NO_DATA géré au niveau API.
- Un threshold inactif (is_active=False) est ignoré (filtré dans ThresholdRepository.for_machine()).
- Les alerts ne sont créées QUE si :
    * seuil violé
    * metric_instance.is_alerting_enabled == True
    * metric_instance.is_paused == False
"""

import uuid
import logging
from typing import Any

from sqlalchemy import select, desc

from app.domain.policies import match_condition

from app.core.config import settings


logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_threshold_config_value(th) -> Any:
    if th.value_num is not None:
        return th.value_num
    if th.value_bool is not None:
        return th.value_bool
    return th.value_str  # peut être None si données incohérentes


def _threshold_incident_title(hostname: str, metric_name: str) -> str:
    return f"Machine {hostname} : Seuil dépassé sur {metric_name}"

# ---------------------------------------------------------------------------
# Back-compat : ancien wrapper utilisé par de vieux tests
# ---------------------------------------------------------------------------
def _match(condition: str, metric_type: str, sample_value: Any, threshold) -> bool:
    """Compatibilité historique : délègue à match_condition."""
    return match_condition(metric_type, condition, sample_value, threshold)


# ---------------------------------------------------------------------------
# Service principal
# ---------------------------------------------------------------------------
def evaluate_machine(machine_id) -> int:
    """
    Évalue tous les thresholds d’une machine (couche SEUILS uniquement).

    ✅ Ajout (grace metrics) :
      - La "période de grâce" sert à CONFIRMER la persistance AVANT d'envoyer la
        1ère notification de défaut de seuil (notify_alert).
      - On NE duplique PAS la logique d'envoi : on continue d'envoyer via notify_alert(),
        mais on gate l'enqueue initial dans evaluation_service pour éviter :
          * bruit (enqueue immédiat alors qu'on sait qu'on veut attendre)
          * incohérence "incident ouvert mais aucune notif" si tu choisis d'attendre aussi l'incident

    Politique retenue ici (cohérente avec “grace = confirmer”) :
      1) On crée/maintient l'Alert FIRING immédiatement (pour avoir triggered_at stable)
      2) On attend (grace) avant :
           - d'ENQUEUE la première notif notify_alert
           - et (optionnel) d'ouvrir l'incident BREACH
         => ici on applique AUSSI la grace à l'incident, pour aligner UX/UI.
         Si tu veux garder l'incident immédiat, lis le commentaire "OPTION".

    ⚠️ IMPORTANT :
      - La gestion du cooldown/reminder reste dans notify_alert (par alert_id).
      - Les notifications de résolution (resolved=True) partent immédiatement (pas de grace).
    """
    from datetime import datetime, timezone, timedelta

    # Lazy imports pour éviter les cycles
    from app.infrastructure.persistence.database.session import open_session

    from app.infrastructure.persistence.database.models.sample import Sample
    from app.infrastructure.persistence.database.models.machine import Machine
    from app.infrastructure.persistence.database.models.alert import Alert

    from app.infrastructure.persistence.repositories.alert_repository import AlertRepository
    from app.infrastructure.persistence.repositories.client_settings_repository import ClientSettingsRepository
    from app.infrastructure.persistence.repositories.incident_repository import IncidentRepository
    from app.infrastructure.persistence.repositories.threshold_repository import ThresholdRepository

    # -------------------------------------------------------------------
    # 1) Validation / normalisation de l'UUID
    # -------------------------------------------------------------------
    try:
        machine_uuid = machine_id if isinstance(machine_id, uuid.UUID) else uuid.UUID(str(machine_id))
    except Exception:
        logger.warning(
            "evaluate_machine() ignoré : machine_id invalide",
            extra={"machine_id": machine_id},
        )
        return 0

    now = datetime.now(timezone.utc)
    logger.debug(
        "evaluate_machine: start at %s for machine_id=%s",
        now.isoformat(),
        str(machine_uuid),
    )

    total_alerts = 0

    # alert_ids pour lesquels on doit appeler notify_alert() (nouveaux défauts CONFIRMÉS)
    alerts_to_notify: list[str] = []

    # payloads de notifications de résolution de seuil à envoyer après commit
    threshold_resolutions_to_notify: list[dict[str, Any]] = []

    with open_session() as session:
        trepo = ThresholdRepository(session)
        arepo = AlertRepository(session)
        irepo = IncidentRepository(session)
        csrepo = ClientSettingsRepository(session)

        # -------------------------------------------------------------------
        # 2) Charger la machine
        # -------------------------------------------------------------------
        machine = session.get(Machine, machine_uuid)
        if not machine:
            logger.warning(
                "evaluate_machine() ignoré : machine inexistante",
                extra={"machine_id": str(machine_uuid)},
            )
            return 0

        client_id = machine.client_id

        # -------------------------------------------------------------------
        # 3) Cache staleness + GRACE (source: client_settings.grace_period_seconds)
        # -------------------------------------------------------------------
        staleness_threshold_sec = csrepo.get_effective_metric_staleness_seconds(client_id)

        # ✅ Grace metrics : confirmation persistance avant première notif (et incident)
        try:
            grace_seconds = int(csrepo.get_effective_grace_period_seconds(client_id) or 0)
            grace_seconds = max(0, grace_seconds)
        except Exception:
            # fallback safe
            grace_seconds = 0

        grace_delta = timedelta(seconds=grace_seconds)

        pairs = trepo.for_machine(machine_uuid)

        for th, metric_instance in pairs:
            # 3.a) Skip si alerting désactivé à la source
            if not getattr(metric_instance, "is_alerting_enabled", True):
                continue

            # 3.b) Skip si métrique en pause
            if getattr(metric_instance, "is_paused", False):
                continue

            # ----------------------------------------------------------------
            # 4) Charger le dernier sample pour cette MetricInstance
            # ----------------------------------------------------------------
            last_sample = session.scalar(
                select(Sample)
                .where(Sample.metric_instance_id == metric_instance.id)
                .order_by(desc(Sample.ts), desc(Sample.seq))
                .limit(1)
            )

            if not last_sample:
                logger.debug(
                    "evaluate_machine: skip threshold '%s' on metric_instance '%s' (machine_id=%s) because there is no sample",
                    th.name,
                    metric_instance.name_effective,
                    str(machine_uuid),
                )
                continue

            # ----------------------------------------------------------------
            # 4.5) Check freshness : skip si le sample est stale
            # ----------------------------------------------------------------
            sample_ts = last_sample.ts
            if sample_ts is not None:
                if sample_ts.tzinfo is None:
                    sample_ts = sample_ts.replace(tzinfo=timezone.utc)
                else:
                    sample_ts = sample_ts.astimezone(timezone.utc)

                sample_age_sec = (now - sample_ts).total_seconds()
                if sample_age_sec > staleness_threshold_sec:
                    logger.debug(
                        "evaluate_machine: skip threshold '%s' on metric_instance '%s' (machine_id=%s) because sample is stale (sample_ts=%s, age=%.1fs, threshold=%ds)",
                        th.name,
                        metric_instance.name_effective,
                        str(machine_uuid),
                        sample_ts.isoformat(),
                        sample_age_sec,
                        staleness_threshold_sec,
                    )
                    continue

            # ----------------------------------------------------------------
            # 5) Déduire le type & la valeur depuis le sample
            # ----------------------------------------------------------------
            mtype: str
            value: Any

            if last_sample.num_value is not None:
                mtype = "numeric"
                value = last_sample.num_value
            elif last_sample.bool_value is not None:
                mtype = "boolean"
                value = last_sample.bool_value
            else:
                mtype = "string"
                value = last_sample.str_value

            if value is None:
                logger.debug(
                    "evaluate_machine: skip threshold '%s' on metric_instance '%s' (machine_id=%s) because last sample value is None (type=%s)",
                    th.name,
                    metric_instance.name_effective,
                    str(machine_uuid),
                    mtype,
                )
                continue

            # ----------------------------------------------------------------
            # 6) Vérification via policy engine
            # ----------------------------------------------------------------
            breach = match_condition(mtype, th.condition, value, th)

            if breach:
                # ------------------------------------------------------------
                # 6.a) Alerte FIRING → créer / maintenir
                # ------------------------------------------------------------
                threshold_value = get_threshold_config_value(th)

                msg = (
                    f"{metric_instance.name_effective} {th.condition} seuil "
                    f"(seuil={threshold_value})"
                )

                alert, created = arepo.create_firing(
                    threshold_id=th.id,
                    machine_id=machine_uuid,
                    metric_instance_id=metric_instance.id,
                    severity=th.severity,
                    message=msg,
                    current_value=value,
                )

                # ------------------------------------------------------------
                # ✅ 6.a.bis) GRACE metrics : confirmer persistance
                #
                # Règle :
                # - On n'enqueue la 1ère notif notify_alert QUE si :
                #     * sévérité éligible
                #     * et grace=0 OU (now - alert.triggered_at) >= grace
                #
                # Pourquoi ici ?
                # - évite d'enfiler notify_alert immédiatement alors qu'on veut attendre.
                # - on réutilise l'unique "horloge" stable : Alert.triggered_at (définie dans create_firing).
                # ------------------------------------------------------------
                ok_sev = th.severity in {"warning", "error", "critical"}

                # triggered_at est posé à la création et reste stable si FIRING continue
                started_at = getattr(alert, "triggered_at", None)
                if started_at is not None:
                    if started_at.tzinfo is None:
                        started_at = started_at.replace(tzinfo=timezone.utc)
                    else:
                        started_at = started_at.astimezone(timezone.utc)

                grace_ok = True
                if grace_seconds > 0 and started_at is not None:
                    grace_ok = (now - started_at) >= grace_delta

                # Si created=True, on est dans le "premier défaut" : on gate selon grace_ok
                if created and ok_sev:
                    if grace_ok:
                        alerts_to_notify.append(str(alert.id))
                    else:
                        logger.info(
                            "evaluate_machine: grace active → do not enqueue notify_alert yet",
                            extra={
                                "client_id": str(client_id),
                                "machine_id": str(machine_uuid),
                                "alert_id": str(alert.id),
                                "metric_instance_id": str(metric_instance.id),
                                "grace_seconds": int(grace_seconds),
                            },
                        )
                        # Rien à faire ici : notify_alert (avec sa logique) s'en chargera
                        # lors du prochain cycle quand grace_ok deviendra True.

                # ------------------------------------------------------------
                # 6.b) Incident : ouverture si nécessaire
                #
                # ✅ Par cohérence “grace = confirmer persistance”, on applique le même gate.
                # OPTION : si tu veux ouvrir l'incident immédiatement (même pendant la grace),
                #         supprime ce if grace_ok et garde seulement `if client_id: ...`.
                # ------------------------------------------------------------
                if client_id and grace_ok:
                    incident_title = _threshold_incident_title(machine.hostname, metric_instance.name_effective)

                    irepo.open_breach_incident(
                        client_id=client_id,
                        machine_id=machine_uuid,
                        metric_instance_id=metric_instance.id,
                        title=incident_title,
                        severity=th.severity,
                        description=msg,
                    )

                total_alerts += 1

            else:
                # ------------------------------------------------------------
                # 7) Pas de violation → résolution éventuelle
                # ------------------------------------------------------------
                existing_alert = session.scalar(
                    select(Alert)
                    .where(
                        Alert.threshold_id == th.id,
                        Alert.machine_id == machine_uuid,
                        Alert.metric_instance_id == metric_instance.id,
                        Alert.status == "FIRING",
                    )
                    .limit(1)
                )

                resolved_incident = None

                resolved_alert_count = 0
                if existing_alert:
                    resolved_alert_count = arepo.resolve_open_for_threshold_instance(
                        threshold_id=th.id,
                        machine_id=machine_uuid,
                        metric_instance_id=metric_instance.id,
                        now=now,
                    )

                if client_id and resolved_alert_count:
                    resolved_incident = irepo.resolve_open_breach_incident(
                        client_id=client_id,
                        machine_id=machine_uuid,
                        metric_instance_id=metric_instance.id,
                    )

                if resolved_alert_count and resolved_incident:
                    threshold_resolutions_to_notify.append(
                        {
                            "client_id": client_id,
                            "incident_id": str(resolved_incident.id),
                            "machine_name": machine.hostname,
                            "metric_name": metric_instance.name_effective,
                            "threshold_value": get_threshold_config_value(th),
                            "threshold_condition": th.condition,
                            "last_value": value,
                        }
                    )

        # -------------------------------------------------------------------
        # 8) Commit avant notifications (alerts/incidents)
        # -------------------------------------------------------------------
        session.commit()

    # -----------------------------------------------------------------------
    # 9) Notifications (post-commit)
    # -----------------------------------------------------------------------
    if alerts_to_notify or threshold_resolutions_to_notify:
        try:
            from app.workers.tasks.notification_tasks import notify_alert
            from app.workers.tasks.notification_tasks import notify as notify_task

            # Défauts confirmés (grace_ok) : notify_alert gère le cooldown/reminder par alert_id
            for aid in alerts_to_notify:
                notify_alert.delay(aid)

            # Résolutions : immediate, bypass cooldown global
            for info in threshold_resolutions_to_notify:
                payload = {
                    "title": f"✅ Machine {info['machine_name']} : Seuil {info['metric_name']} retour à la normale",
                    "text": (
                        f"Machine: {info['machine_name']}\n"
                        f"Métrique: {info['metric_name']}\n"
                        f"Seuil: {info['threshold_condition']} {info['threshold_value']}\n"
                        f"Dernière valeur observée: {info['last_value']}"
                    ),
                    "severity": "info",
                    "client_id": info["client_id"],
                    "incident_id": info["incident_id"],
                    "alert_id": None,
                    "resolved": True,
                }
                notify_task.apply_async(kwargs={"payload": payload}, queue="notify")

            logger.info(
                "evaluate_machine: alert notifications dispatchées",
                extra={
                    "count_alerts": len(alerts_to_notify),
                    "count_resolutions": len(threshold_resolutions_to_notify),
                },
            )
        except Exception as e:
            logger.error(
                "evaluate_machine: erreur lors de la planification des notifications",
                extra={"error": str(e)},
                exc_info=True,
            )

    return total_alerts

