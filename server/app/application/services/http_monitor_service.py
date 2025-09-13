from __future__ import annotations

"""server/app/application/services/http_monitor_service.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Service de monitoring HTTP :

- sélectionne les cibles « dues » (actives ET intervalle écoulé)
- exécute la requête HTTP avec méthode/timeout
- met à jour les champs last_* (status_code, latency, erreur)
- ouvre/résout des incidents via le repository dédié

Notes importantes :
- On utilise **get_sync_session** (et pas get_db), car ce service n'est pas un endpoint FastAPI.
  → Tes tests unitaires patchent précisément `get_sync_session`.
"""

import uuid

from datetime import datetime, timezone, timedelta
from typing import Optional
import logging
import time

import httpx
from sqlalchemy import select

from app.core.config import settings  # <-- pour savoir si SLACK_WEBHOOK est configuré
from app.infrastructure.persistence.database.session import get_sync_session
from app.infrastructure.persistence.database.models.http_target import HttpTarget
from app.infrastructure.persistence.database.models.notification_log import NotificationLog
from app.infrastructure.persistence.repositories.incident_repository import IncidentRepository
from app.workers.tasks.notification_tasks import notify as notify_task, get_remind_minutes

logger = logging.getLogger(__name__)

DEFAULT_METHOD = "GET"
INCIDENT_TITLE_PREFIX = "HTTP check failed: "


def _should_check(t: HttpTarget, now: datetime) -> bool:
    if not t.is_active:
        return False
    last = _as_utc(t.last_check_at)
    if last is None:
        return True
    interval = timedelta(seconds=t.check_interval_seconds or 300)
    return (_as_utc(now) - last) >= interval


def _perform_check(t: HttpTarget) -> tuple[Optional[int], Optional[int], Optional[str]]:
    """Exécute la requête HTTP et retourne (status_code, response_time_ms, error_message)."""
    timeout_seconds = t.timeout_seconds or 30
    method = (t.method or DEFAULT_METHOD).upper()
    started = time.perf_counter()

    try:
        with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
            resp = client.request(method, t.url)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return resp.status_code, elapsed_ms, None
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return None, elapsed_ms, str(exc)


def _update_result(t: HttpTarget, status: Optional[int], elapsed_ms: Optional[int], err: Optional[str]) -> None:
    """Met à jour les champs « last_* » sur la cible."""
    t.last_check_at = datetime.now(timezone.utc)
    t.last_status_code = status
    t.last_response_time_ms = elapsed_ms
    t.last_error_message = err


def _incident_cooldown_ok(db, incident_id, remind_minutes: int) -> bool:
    now = datetime.now(timezone.utc)
    last_sent = db.scalar(
        select(NotificationLog.sent_at)
        .where(
            NotificationLog.incident_id == incident_id,
            NotificationLog.status == "success",
            NotificationLog.provider == "slack",
        )
        .order_by(NotificationLog.sent_at.desc())
        .limit(1)
    )
    last_sent = _as_utc(last_sent)
    return (last_sent is None) or ((now - last_sent) >= timedelta(minutes=remind_minutes))


def _enqueue_incident_notification(incident, *, severity: str, text: str) -> None:
    """Enfile une notif Slack pour l’incident (passe par la tâche `notify`)."""
    payload = {
        "title": f"🚨 Incident {severity.upper()}",
        "text": text,
        "severity": severity,                     # info | warning | error | critical
        "channel": None,                          # None => canal par défaut via NotificationPayload
        "client_id": incident.client_id,          # UUID accepté par le modèle
        "incident_id": incident.id,               # pour le suivi dans NotificationLog
        "alert_id": None,
    }
    notify_task.apply_async(kwargs={"payload": payload}, queue="notify")


def _as_utc(d: datetime | None) -> datetime | None:
    """Retourne d en timezone UTC 'aware' (tolère None)."""
    if d is None:
        return None
    if d.tzinfo is None:
        return d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def check_http_targets() -> int:
    """
    Parcourt les cibles HTTP actives « dues » et retourne le nombre de checks effectués.
    """
    updated = 0
    now = datetime.now(timezone.utc)

    with get_sync_session() as s:
        irepo = IncidentRepository(s)

        targets = s.scalars(
            select(HttpTarget).where(HttpTarget.is_active.is_(True))
        ).all()

        for t in targets:
            if not _should_check(t, now):
                continue

            status, rt_ms, err = _perform_check(t)
            _update_result(t, status, rt_ms, err)
            updated += 1

            title = f"{INCIDENT_TITLE_PREFIX}{t.name}"
            is_unexpected = (
                (status is None)
                or (t.expected_status_code and status != t.expected_status_code)
            )

            remind_minutes = get_remind_minutes(None)

            if is_unexpected:
                inc, created = irepo.open(
                    client_id=t.client_id,
                    title=title,
                    severity="warning",
                    machine_id=None,
                    description=(err or f"Got {status}, expected {t.expected_status_code}"),
                )

                if created or _incident_cooldown_ok(s, inc.id, remind_minutes):
                    text = (
                        f"{t.name} — {t.url}\n"
                        f"Status: {status} (attendu: {t.expected_status_code})\n"
                        f"Latence: {rt_ms} ms\n"
                        f"Erreur: {err or '-'}"
                    )
                    _enqueue_incident_notification(inc, severity="warning", text=text)
            else:
                resolved = irepo.resolve_by_title(client_id=t.client_id, title=title)
                if resolved:
                    # ⚠️ N’envoie la notification de résolution que si un webhook est configuré
                    if getattr(settings, "SLACK_WEBHOOK", None) and getattr(settings, "NOTIFY_ON_RESOLVE", False):
                        notify_task.apply_async(
                            kwargs={"payload": {
                                "title": "✅ Incident RESOLVED",
                                "text": f"{t.name} — {t.url}\nOK: {status} (attendu: {t.expected_status_code})\nLatence: {rt_ms} ms",
                                "severity": "info",
                                "channel": None,
                                "client_id": t.client_id,
                                "incident_id": None,
                                "alert_id": None,
                            }},
                            queue="notify",
                        )

        if updated:
            s.commit()

    logger.info("HTTP monitor: %d cible(s) vérifiée(s).", updated)
    return updated


def check_one_target(target_id: str) -> dict:
    """
    Check manuel d’une seule cible — pratique pour UI/debug/tests d’intégration.
    """
    try:
        tid = target_id if isinstance(target_id, uuid.UUID) else uuid.UUID(str(target_id))
    except Exception:
        return {"ok": False, "reason": "bad_id"}

    with get_sync_session() as s:
        t = s.get(HttpTarget, tid)  # <-- utiliser l’UUID converti
        if not t:
            return {"ok": False, "reason": "not_found"}

        status, elapsed_ms, err = _perform_check(t)
        _update_result(t, status, elapsed_ms, err)
        s.commit()

        return {
            "ok": err is None and (t.expected_status_code is None or status == t.expected_status_code),
            "status": status,
            "ms": elapsed_ms,
            "error": err,
            "expected": t.expected_status_code,
        }
