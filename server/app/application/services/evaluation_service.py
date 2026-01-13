from __future__ import annotations
"""
server/app/application/services/evaluation_service.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Service d’évaluation des seuils et gestion des alertes/incidents.

Pipeline (version refacto) :
    1. Charger les thresholds actifs d’une machine (ThresholdNew)
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
- Un threshold inactif (is_active=False) est ignoré (filtré dans ThresholdNewRepository.for_machine()).
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

    Args:
        machine_id (UUID | str)

    Returns:
        int : nombre total d'alertes créées ou mises à jour.

    Comportement :
        - Ignore les MetricInstance pausées
        - Ignore les MetricInstance sans sample (NO_DATA ne déclenche pas d'alerte de seuil)
        - Évalue uniquement les seuils (thresholds_new) :
            * ouvre/maintient les alertes et incidents de seuil en cas de violation
            * résout les alertes/incidents de seuil quand il n’y a plus de violation
            * envoie une notification à l’ouverture ET à la résolution du défaut

    ⚠️ IMPORTANT :
        La logique de fraîcheur des métriques (NO-DATA, machine UP/DOWN,
        incidents "Machine not sending data", etc.) est gérée exclusivement
        dans metric_freshness_service.check_metrics_no_data().
        Ici, on ne touche qu'aux incidents de type "Threshold breach on ...".
    """
    from datetime import datetime, timezone


    # Lazy imports pour éviter les cycles
    from app.infrastructure.persistence.database.session import open_session
    
    from app.infrastructure.persistence.database.models.sample import Sample
    from app.infrastructure.persistence.database.models.machine import Machine
    from app.infrastructure.persistence.database.models.alert import Alert

    from app.infrastructure.persistence.repositories.alert_repository import AlertRepository
    from app.infrastructure.persistence.repositories.client_settings_repository import ClientSettingsRepository
    from app.infrastructure.persistence.repositories.incident_repository import IncidentRepository
    from app.infrastructure.persistence.repositories.threshold_new_repository import ThresholdNewRepository

    # -------------------------------------------------------------------
    # 1) Validation / normalisation de l'UUID
    # -------------------------------------------------------------------
    try:
        machine_uuid = (
            machine_id if isinstance(machine_id, uuid.UUID) else uuid.UUID(str(machine_id))
        )
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
    # alert_ids pour lesquels on doit appeler notify_alert() (nouveaux défauts)
    alerts_to_notify: list[str] = []
    # payloads de notifications de résolution de seuil à envoyer après commit
    threshold_resolutions_to_notify: list[dict[str, Any]] = []

    with open_session() as session:
        trepo = ThresholdNewRepository(session)
        arepo = AlertRepository(session)
        irepo = IncidentRepository(session)

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
        # 3) Charger toutes les paires (ThresholdNew, MetricInstance) ACTIVES
        #    (trepo.for_machine filtre déjà sur ThresholdNew.is_active == True)
        # -------------------------------------------------------------------

        # -------------------------------------------------------------------
        # Cache du seuil de staleness (fresh-only pour les thresholds)
        # -------------------------------------------------------------------
        csrepo = ClientSettingsRepository(session)
        staleness_threshold_sec = csrepo.get_effective_metric_staleness_seconds(client_id)
        
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
                # Pas de données → NO_DATA ne déclenche pas de seuil.
                # La détection NO-DATA / incidents globaux est gérée ailleurs.
                logger.debug(
                    "evaluate_machine: skip threshold '%s' on metric_instance '%s' (machine_id=%s) "
                    "because there is no sample",
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
                # Normalise UTC (au cas où ts est naïf)
                if sample_ts.tzinfo is None:
                    sample_ts = sample_ts.replace(tzinfo=timezone.utc)
                else:
                    sample_ts = sample_ts.astimezone(timezone.utc)

                sample_age_sec = (now - sample_ts).total_seconds()

                if sample_age_sec > staleness_threshold_sec:
                    logger.debug(
                        "evaluate_machine: skip threshold '%s' on metric_instance '%s' (machine_id=%s) "
                        "because sample is stale (sample_ts=%s, age=%.1fs, threshold=%ds)",
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
            #
            #    On ne dépend plus de metric.type (qui n’existe plus sur MetricInstance).
            #    Règle :
            #        - si num_value non None    → type = numeric
            #        - sinon si bool_value non None → type = boolean
            #        - sinon → type = string (str_value)
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
                # fallback string (type 'string' ou données textuelles)
                mtype = "string"
                value = last_sample.str_value

            # Si pour une raison quelconque la valeur est None, on ne déclenche rien
            if value is None:
                logger.debug(
                    "evaluate_machine: skip threshold '%s' on metric_instance '%s' (machine_id=%s) "
                    "because last sample value is None (type=%s)",
                    th.name,
                    metric_instance.name_effective,
                    str(machine_uuid),
                    mtype,
                )
                continue

            # ----------------------------------------------------------------
            # 6) Vérification via policy engine
            #    match_condition(metric_type, condition, current_value, threshold)
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

                # Notification SEULEMENT lors de la création (premier défaut)
                # et pour certaines sévérités.
                if created and th.severity in {"warning", "error", "critical"}:
                    alerts_to_notify.append(str(alert.id))

                # ------------------------------------------------------------
                # 6.b) Incident : ouverture si nécessaire pour ce threshold/metric_instance
                # ------------------------------------------------------------
                if client_id:
                    incident_title = _threshold_incident_title(machine.hostname, metric_instance.name_effective)

                    breach_incident, breach_created = irepo.open_breach_incident(
                        client_id=client_id,
                        machine_id=machine_uuid,
                        metric_instance_id=metric_instance.id,
                        title=incident_title,
                        severity=th.severity,
                        description=msg,
                    )

                    # (pour plus tard) breach_incident.id peut être utilisé si tu veux
                    # préfixer aussi les notifs d'alerte (notify_alert) avec incident_id.

                total_alerts += 1

            else:
                # ------------------------------------------------------------
                # 7) Pas de violation → résolution éventuelle
                #
                # Objectifs :
                #   - Résoudre l'alerte FIRING du triplet (threshold, machine, metric_instance)
                #   - Résoudre l'incident correspondant SI (et seulement si) on a réellement
                #     résolu une alerte FIRING
                #   - Si on vient réellement d'une situation FIRING → planifier une notif
                #     de résolution après le commit.
                # ------------------------------------------------------------

                # 7.0) On regarde s'il existait une alerte FIRING pour ce triplet
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

                # 7.a) Résoudre l'alerte active pour CE triplet seulement si elle est FIRING
                resolved_alert_count = 0
                if existing_alert:
                    resolved_alert_count = arepo.resolve_open_for_threshold_instance(
                        threshold_id=th.id,
                        machine_id=machine_uuid,
                        metric_instance_id=metric_instance.id,
                        now=now,
                    )

                # 7.b) Résoudre l'incident seulement si on a vraiment résolu l'alerte FIRING
                if client_id and resolved_alert_count:
                    resolved_incident = irepo.resolve_open_breach_incident(
                        client_id=client_id,
                        machine_id=machine_uuid,
                        metric_instance_id=metric_instance.id,
                    )

                # 7.c) Notif uniquement si on avait FIRING et qu'on a effectivement résolu
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
    # 9) Notifications d’alertes (post-commit, hors transaction)
    #
    #    - alerts_to_notify           → notify_alert(alert_id) (défaut de seuil)
    #    - threshold_resolutions_to_notify → notify(payload, resolved=True)
    #                                       (rétablissement de seuil)
    # -----------------------------------------------------------------------
    if alerts_to_notify or threshold_resolutions_to_notify:
        try:
            from app.workers.tasks.notification_tasks import notify_alert
            from app.workers.tasks.notification_tasks import notify as notify_task

            # Notifications de mise en défaut (avec cooldown géré par notify_alert)
            for alert_id in alerts_to_notify:
                notify_alert.delay(alert_id)

            # Notifications de résolution de seuil (resolved=True → pas de cooldown)
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
                    "resolved": True,  # → bypass du cooldown global dans notify()
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
