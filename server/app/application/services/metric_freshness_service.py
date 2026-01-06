from __future__ import annotations
"""
server/app/application/services/metric_freshness_service.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Détection des métriques sans données récentes (NO DATA), version refacto
pour le nouveau modèle MetricInstance.

NOUVELLE LOGIQUE (corrigée) :

1. On analyse toutes les métriques actives et non pausées d'une machine
   (MetricInstance.is_alerting_enabled=True & is_paused=False).
2. On NE CRÉE AUCUN INCIDENT dans la boucle principale.
   -> On collecte seulement les infos dans des buffers.
3. Après analyse complète d'une machine :
   - Si TOUTES les métriques candidates sont stale -> MACHINE DOWN
        => 1 seul incident CRITICAL par machine
        => on résout tous les incidents de type "Metric no data"
   - Sinon -> PARTIAL STALE
        => un incident ERROR par métrique stale
        => on résout les incidents des métriques redevenues fraîches
4. Notifications :
   - Machine-down -> rattachée à l'incident machine unique
   - Partial-stale -> rattachées aux incidents métriques correspondants

AJOUTS :
- MONITORING_STARTED_AT : instant de démarrage du service de monitoring.
- STARTUP_GRACE_SECONDS : période de grâce au démarrage pendant laquelle
  on ne déclenche AUCUN incident NO DATA (check entièrement ignoré).
- Beaucoup de logs logger.debug + quelques logger.info pour suivre le flux.

⚠️ Important (refacto) :
- On travaille maintenant sur `MetricInstance` au lieu de `Metric`.
- On utilise `metric_instance.name_effective` comme nom de métrique
  dans les logs, incidents et notifications.
"""

import logging
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Dict, Any, Iterable
from sqlalchemy.orm import Session

from app.core.config import settings
from app.infrastructure.persistence.database.session import open_session
from app.infrastructure.persistence.database.models.metric_instance import MetricInstance
from app.infrastructure.persistence.database.models.machine import Machine
from app.infrastructure.persistence.database.models.incident import IncidentType
from app.infrastructure.persistence.repositories.client_settings_repository import (
    ClientSettingsRepository,
)
from app.infrastructure.persistence.repositories.incident_repository import (
    IncidentRepository,
)
from app.workers.tasks.notification_tasks import (
    notify as notify_task,
    reset_alert_cooldown_for_machine,
)


logger = logging.getLogger(__name__)


# Instant de démarrage du processus / service de monitoring
MONITORING_STARTED_AT = datetime.now(timezone.utc)

# Période de grâce après démarrage :
# tant que uptime < STARTUP_GRACE_SECONDS, check_metrics_no_data() ne fait RIEN.
STARTUP_GRACE_SECONDS = settings.MONITORING_STARTUP_GRACE_SECONDS

METRIC_NO_DATA_TITLE_PREFIX = "Métrique donnée manquante : "


def _as_utc(dt_val: datetime | None) -> datetime | None:
    """Normalise un datetime en UTC (timezone-aware)."""
    if dt_val is None:
        return None
    if dt_val.tzinfo is None:
        return dt_val.replace(tzinfo=timezone.utc)
    return dt_val.astimezone(timezone.utc)

def _server_tzinfo():
    return ZoneInfo(getattr(settings, "SERVER_TIMEZONE", "UTC"))

def _fmt_server_tz(dt_val: datetime | None) -> str:
    if not dt_val:
        return "inconnue"
    dt_utc = _as_utc(dt_val)
    return dt_utc.astimezone(_server_tzinfo()).isoformat()

def is_metric_instance_fresh(
    metric_instance: MetricInstance,
    threshold_sec: int,
    now: datetime | None = None,
) -> bool:
    """
    Détermine si une MetricInstance a des données fraîches.

    Logique:
      - On ne compte que le temps écoulé depuis MONITORING_STARTED_AT
      - age = now - max(metric_instance.updated_at, MONITORING_STARTED_AT)
      - fresh si age <= threshold_sec
    """
    if now is None:
        now = datetime.now(timezone.utc)

    updated_at_utc = _as_utc(metric_instance.updated_at)

    if updated_at_utc is None:
        effective_since = MONITORING_STARTED_AT
    else:
        effective_since = max(updated_at_utc, MONITORING_STARTED_AT)

    age_sec = (now - effective_since).total_seconds()
    return age_sec <= threshold_sec


def _iter_candidate_metrics(s: Session, *, batch_size: int = 2000) -> Iterable[tuple]:
    """
    ⚡ Version optimisée :
    - ne charge pas les objets ORM complets (MetricInstance/Machine)
    - ne matérialise pas toute la liste (pas de .all())
    - stream via yield_per(batch_size)

    Yields tuples:
      (mi_id, mi_name, mi_updated_at, machine_id, hostname, client_id, machine_status)
    """
    return (
        s.query(
            MetricInstance.id,
            MetricInstance.name_effective,
            MetricInstance.updated_at,
            Machine.id,
            Machine.hostname,
            Machine.client_id,
            Machine.status,
        )
        .join(Machine, MetricInstance.machine_id == Machine.id)
        .filter(
            MetricInstance.is_alerting_enabled.is_(True),
            MetricInstance.is_paused.is_(False),
        )
        .yield_per(batch_size)
    )


def _get_threshold(
    client_id: uuid.UUID,
    csrepo: "ClientSettingsRepository",
    thresholds_cache: Dict[uuid.UUID, int],
) -> int:
    if client_id not in thresholds_cache:
        thresholds_cache[client_id] = csrepo.get_effective_metric_staleness_seconds(client_id)
        logger.debug(
            "metric_freshness: loaded staleness threshold for client_id=%s -> %ds",
            client_id,
            thresholds_cache[client_id],
        )
    return thresholds_cache[client_id]


def _analyze_candidate_row_columns(
    *,
    now: datetime,
    mi_id: uuid.UUID,
    mi_name: str,
    mi_updated_at: datetime | None,
    machine_id: uuid.UUID,
    hostname: str,
    client_id: uuid.UUID,
    machine_status: str | None,
    threshold_sec: int,
    stale_by_machine: Dict[uuid.UUID, Dict[uuid.UUID, list[Dict[str, Any]]]],
    resolved_by_machine: Dict[uuid.UUID, Dict[uuid.UUID, list[Dict[str, Any]]]],
    total_candidates_by_machine: Dict[uuid.UUID, int],
    machines_cache: Dict[uuid.UUID, Any],
) -> bool:
    """
    Analyse 1 row "colonnes" candidate.
    Remplit les buffers et retourne True si STALE, False sinon.
    """
    # Cache machine "léger"
    machine = machines_cache.get(machine_id)
    if machine is None:
        machine = type("MachineLite", (), {})()
        machine.id = machine_id
        machine.hostname = hostname
        machine.client_id = client_id
        machine.status = machine_status
        machines_cache[machine_id] = machine

    total_candidates_by_machine[machine_id] = total_candidates_by_machine.get(machine_id, 0) + 1

    updated_at_utc = _as_utc(mi_updated_at)
    effective_since = max(updated_at_utc or MONITORING_STARTED_AT, MONITORING_STARTED_AT)
    age_sec = (now - effective_since).total_seconds()
    is_stale = age_sec > threshold_sec

    logger.debug(
        "metric_freshness: metric='%s' machine='%s' client=%s age=%.1fs (since=%s) "
        "threshold=%ds -> stale=%s",
        mi_name,
        hostname,
        client_id,
        age_sec,
        effective_since.isoformat(),
        threshold_sec,
        is_stale,
    )

    if is_stale:
        stale_by_machine.setdefault(client_id, {}).setdefault(machine_id, []).append(
            {
                "metric_name": mi_name,
                "metric_instance_id": mi_id,
                "hostname": hostname,
                "age_sec": int(age_sec),
                "threshold_sec": int(threshold_sec),
                "updated_iso": _fmt_server_tz(mi_updated_at),
            }
        )
        return True

    resolved_by_machine.setdefault(client_id, {}).setdefault(machine_id, []).append(
        {
            "metric_name": mi_name,
            "metric_instance_id": mi_id,
            "hostname": hostname,
            "updated_iso": _fmt_server_tz(mi_updated_at),
        }
    )
    return False


def _build_all_pairs(
    stale_by_machine: Dict[uuid.UUID, Dict[uuid.UUID, list[Dict[str, Any]]]],
    resolved_by_machine: Dict[uuid.UUID, Dict[uuid.UUID, list[Dict[str, Any]]]],
) -> set[tuple[uuid.UUID, uuid.UUID]]:
    pairs: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for cid, machines in stale_by_machine.items():
        for mid in machines.keys():
            pairs.add((cid, mid))
    for cid, machines in resolved_by_machine.items():
        for mid in machines.keys():
            pairs.add((cid, mid))
    return pairs


def _process_machine_decisions(
    *,
    s,
    irepo: "IncidentRepository",
    client_id: uuid.UUID,
    machine_id: uuid.UUID,
    machine: Any,
    total_candidates: int,
    stale_items: list[Dict[str, Any]],
    fresh_items: list[Dict[str, Any]],
    machines_avec_notif_restore: set[tuple[uuid.UUID, uuid.UUID]],
) -> None:
    """
    Applique les décisions NO-DATA pour une machine donnée, à partir des buffers
    stale_items / fresh_items construits pendant le scan.

    Règles importantes (anti-flapping / anti-spam) :
    - On s'appuie sur les helpers *atomiques* du repository :
        open_nodata_machine_incident / open_nodata_metric_incident
        resolve_open_nodata_machine_incident / resolve_open_nodata_metric_incident
      => pas de "lookup python" via list_open_incidents() pour décider si on crée.
    - Notifications NO-DATA : uniquement lors de la création (created=True).
      Les reminders sont gérés ailleurs (cooldown / scheduler), pas ici.
    - Une résolution de métrique (NO_DATA_METRIC) ne doit JAMAIS fermer un BREACH :
      c'est garanti par resolve_open_nodata_metric_incident().
    """
    hostname = machine.hostname

    stale_metric_names = {it["metric_name"] for it in stale_items}
    fresh_metric_names = {it["metric_name"] for it in fresh_items}

    stale_count = len(stale_items)
    fresh_count = len(fresh_items)

    has_stale = stale_count > 0
    has_fresh = fresh_count > 0
    has_candidates = total_candidates > 0

    stale_ids = {it["metric_instance_id"] for it in stale_items}
    all_stale = has_candidates and len(stale_ids) >= total_candidates

    # ---------------------------------------------------------------------
    # CAS A : MACHINE DOWN (toutes les métriques candidates sont stale)
    # ---------------------------------------------------------------------
    if all_stale:
        # Statut machine
        if getattr(machine, "status", None) != "DOWN":
            machine.status = "DOWN"

        # On évite une double signalisation :
        # - en mode machine-down, les incidents NO_DATA_METRIC n'ont plus de sens => on les résout tous.
        irepo.resolve_all_metric_nodata_incidents(client_id, machine_id)
        s.flush()

        # Incident machine unique (dédupliqué atomiquement côté DB)
        machine_incident, created = irepo.open_nodata_machine_incident(
            client_id=client_id,
            machine_id=machine_id,
            title=f"Machine {hostname} : pas de donnée envoyée",
            severity="critical",
            description=(
                "Les métriques non-pausées n'ont pas de données récentes. "
                "La machine ne communique probablement pas."
            ),
        )
        s.flush()

        # Notification uniquement lors de la création (sinon reminders ailleurs)
        if created:
            max_age = max(it["age_sec"] for it in stale_items) if stale_items else 0
            threshold = stale_items[0]["threshold_sec"] if stale_items else 0

            text = (
                f"Machine: {hostname}\n"
                "Toutes les métriques actives non mises en pause sont sans données récentes.\n"
                f"Dernière activité connue: {max_age}s (seuil {threshold}s)."
            )
            payload = {
                "title": f"🚨 [{hostname}] : machine ne communique plus",
                "text": text,
                "severity": "critical",
                "client_id": str(client_id),
                "incident_id": str(machine_incident.id),
                "alert_id": None,
            }
            notify_task.apply_async(kwargs={"payload": payload}, queue="notify")

            logger.info(
                "metric_freshness: created new NO_DATA_MACHINE incident id=%s for machine_id=%s",
                machine_incident.id,
                machine_id,
            )
        else:
            logger.debug(
                "metric_freshness: NO_DATA_MACHINE incident already open (id=%s) for machine_id=%s",
                machine_incident.id,
                machine_id,
            )

        # Machine-down = décision terminale pour cette machine
        return

    # ---------------------------------------------------------------------
    # CAS B : machine avec au moins une métrique fresh
    #         => on peut résoudre l'incident machine NO_DATA_MACHINE s'il existe.
    # ---------------------------------------------------------------------
    if has_candidates and has_fresh:
        inc_machine = irepo.resolve_open_nodata_machine_incident(
            client_id=client_id,
            machine_id=machine_id,
        )

        # Si on a résolu un incident machine, on envoie une notif "restored"
        if inc_machine:
            if getattr(machine, "status", None) != "UP":
                machine.status = "UP"

            # TEMPORAIRE (TEST) :
            # Le reset du cooldown des alertes de seuil est désactivé volontairement.
            # Objectif : tester le comportement "1 seul incident OPEN + reminders"
            # sans réinitialisation artificielle du cooldown lors d'un restore machine.
            #
            # À réévaluer / réactiver après validation du comportement de notifications.
            
            # NOTE (temporary disable for testing):
            # Threshold alert cooldown reset is intentionally disabled to validate
            # incident flapping fixes and reminder-only notification behavior.
            # Re-enable once incident lifecycle is fully validated.
            
            # try:
            #     reset_alert_cooldown_for_machine(client_id, machine_id)
            # except Exception:
            #     logger.exception(
            #         "metric_freshness: failed to reset threshold alert cooldown "
            #         "for client_id=%s, machine_id=%s",
            #         client_id,
            #         machine_id,
            #     )

            if has_stale:
                # Machine up mais dégradée (quelques métriques encore stale)
                degraded = ", ".join(sorted(stale_metric_names))
                text = (
                    f"Machine: {hostname}\n"
                    "La machine envoie à nouveau des données, "
                    f"mais les métriques suivantes sont toujours en panne : {degraded}."
                )
                title = f"✅ {hostname} : machine opérationnelle (partielle)"
                severity = "warning"
            else:
                # Machine up et OK
                text = f"Machine: {hostname}\nLa machine envoie à nouveau des données récentes."
                title = f"✅ {hostname} : machine opérationnelle à nouveau"
                severity = "info"

            payload = {
                "title": title,
                "text": text,
                "severity": severity,
                "client_id": str(client_id),
                "incident_id": str(inc_machine.id),
                "alert_id": None,
                "resolved": True,
            }
            notify_task.apply_async(kwargs={"payload": payload}, queue="notify")

            # Marqueur : évite d'envoyer en plus une notif "metric restored" dans la même passe
            machines_avec_notif_restore.add((client_id, machine_id))

    # ---------------------------------------------------------------------
    # CAS C : PARTIAL STALE (au moins une stale, mais pas toutes)
    #         => incident par métrique stale (NO_DATA_METRIC), dédupliqué DB.
    # ---------------------------------------------------------------------
    if has_stale:
        for it in stale_items:
            title = f"{hostname} - {METRIC_NO_DATA_TITLE_PREFIX}{it['metric_name']}"

            # Incident métrique dédupliqué atomiquement : (client_id, machine_id, metric_instance_id, type)
            incident, created = irepo.open_nodata_metric_incident(
                client_id=client_id,
                machine_id=machine_id,
                metric_instance_id=it["metric_instance_id"],
                title=title,
                severity="error",
                description=(
                    f"La métrique '{it['metric_name']}' sur la machine '{hostname}' "
                    f"n'a pas reçu de données depuis {it['age_sec']}s "
                    f"(seuil {it['threshold_sec']}s)."
                ),
            )
            s.flush()

            # Notification uniquement lors de la création (pas de spam à chaque scan)
            if created:
                text = (
                    f"Machine: {hostname}\n"
                    f"Métrique: {it['metric_name']}\n"
                    f"Dernière mise à jour: {it['updated_iso']}\n"
                    f"Âge: {it['age_sec']}s (seuil {it['threshold_sec']}s)"
                )
                payload = {
                    "title": f"🚨 [{hostname}] : métrique {it['metric_name']} donnée manquante",
                    "text": text,
                    "severity": "error",
                    "client_id": str(client_id),
                    "incident_id": str(incident.id),
                    "alert_id": None,
                }
                notify_task.apply_async(kwargs={"payload": payload}, queue="notify")

                logger.info(
                    "metric_freshness: created new NO_DATA_METRIC incident for metric '%s' on machine_id=%s",
                    it["metric_name"],
                    machine_id,
                )
            else:
                logger.debug(
                    "metric_freshness: NO_DATA_METRIC incident already open (id=%s) for metric '%s' on machine_id=%s",
                    incident.id,
                    it["metric_name"],
                    machine_id,
                )

    # ---------------------------------------------------------------------
    # CAS D : Résolution des incidents métriques redevenues fraîches
    #         => on ne résout QUE le type NO_DATA_METRIC.
    # ---------------------------------------------------------------------
    if has_fresh:
        for it in fresh_items:
            inc = irepo.resolve_open_nodata_metric_incident(
                client_id=client_id,
                machine_id=machine_id,
                metric_instance_id=it["metric_instance_id"],
            )
            if not inc:
                continue

            s.flush()

            # Si on vient déjà d'envoyer une notif "machine restored", on évite une notif "metric restored"
            if (client_id, machine_id) in machines_avec_notif_restore:
                continue

            text = (
                f"La métrique '{it['metric_name']}' sur la machine '{hostname}' "
                "a de nouveau des données récentes.\n"
                f"Dernière mise à jour: {it['updated_iso']}"
            )
            payload = {
                "title": f"✅ [{hostname}] : donnée de la métrique '{it['metric_name']}' restaurée",
                "text": text,
                "severity": "info",
                "client_id": str(client_id),
                "incident_id": str(inc.id),
                "alert_id": None,
                "resolved": True,
            }
            notify_task.apply_async(kwargs={"payload": payload}, queue="notify")

def check_metrics_no_data() -> int:
    now = datetime.now(timezone.utc)
    uptime_sec = (now - MONITORING_STARTED_AT).total_seconds()

    logger.debug(
        "metric_freshness: check_metrics_no_data() called at %s (uptime=%.1fs, grace=%ds)",
        now.isoformat(),
        uptime_sec,
        STARTUP_GRACE_SECONDS,
    )

    if uptime_sec < STARTUP_GRACE_SECONDS:
        logger.info(
            "metric_freshness: skipping NO-DATA check (startup grace active, uptime=%.1fs < %ds)",
            uptime_sec,
            STARTUP_GRACE_SECONDS,
        )
        return 0

    stale_count = 0
    thresholds_cache: Dict[uuid.UUID, int] = {}
    stale_by_machine: Dict[uuid.UUID, Dict[uuid.UUID, list[Dict[str, Any]]]] = {}
    resolved_by_machine: Dict[uuid.UUID, Dict[uuid.UUID, list[Dict[str, Any]]]] = {}
    total_candidates_by_machine: Dict[uuid.UUID, int] = {}
    machines_cache: Dict[uuid.UUID, Any] = {}

    with open_session() as s:
        csrepo = ClientSettingsRepository(s)
        irepo = IncidentRepository(s)

        logger.info("metric_freshness: starting candidate scan (optimized columns + yield_per)")
        logger.debug("metric_freshness: MONITORING_STARTED_AT=%s", MONITORING_STARTED_AT.isoformat())

        # Phase 1 (streaming)
        for (
            mi_id,
            mi_name,
            mi_updated_at,
            machine_id,
            hostname,
            client_id,
            machine_status,
        ) in _iter_candidate_metrics(s, batch_size=2000):
            threshold_sec = _get_threshold(client_id, csrepo, thresholds_cache)

            if _analyze_candidate_row_columns(
                now=now,
                mi_id=mi_id,
                mi_name=mi_name,
                mi_updated_at=mi_updated_at,
                machine_id=machine_id,
                hostname=hostname,
                client_id=client_id,
                machine_status=machine_status,
                threshold_sec=threshold_sec,
                stale_by_machine=stale_by_machine,
                resolved_by_machine=resolved_by_machine,
                total_candidates_by_machine=total_candidates_by_machine,
                machines_cache=machines_cache,
            ):
                stale_count += 1

        # Phase 2
        machines_avec_notif_restore: set[tuple[uuid.UUID, uuid.UUID]] = set()
        all_pairs = _build_all_pairs(stale_by_machine, resolved_by_machine)

        for client_id, machine_id in sorted(all_pairs, key=lambda x: (str(x[0]), str(x[1]))):
            machine = machines_cache.get(machine_id)
            if not machine:
                logger.warning(
                    "metric_freshness: missing Machine object for machine_id=%s (client_id=%s)",
                    machine_id,
                    client_id,
                )
                continue

            _process_machine_decisions(
                s=s,
                irepo=irepo,
                client_id=client_id,
                machine_id=machine_id,
                machine=machine,
                total_candidates=total_candidates_by_machine.get(machine_id, 0),
                stale_items=stale_by_machine.get(client_id, {}).get(machine_id, []),
                fresh_items=resolved_by_machine.get(client_id, {}).get(machine_id, []),
                machines_avec_notif_restore=machines_avec_notif_restore,
            )

        # Phase 3 (bonus)
        open_machine_incidents = irepo.list_open_machine_nodata_incidents()
        for inc in open_machine_incidents:
            mid = inc.machine_id
            cid = inc.client_id
            if (cid, mid) in all_pairs:
                continue

            logger.info(
                "metric_freshness: resolving obsolete 'Machine not sending data' "
                "incident id=%s for machine_id=%s (plus aucune métrique candidate)",
                inc.id,
                mid,
            )
            irepo.resolve(inc)

        s.commit()

    logger.info("metric_freshness: %d métrique(s) stale détectées", stale_count)
    return stale_count
