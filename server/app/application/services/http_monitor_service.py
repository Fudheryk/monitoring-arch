from __future__ import annotations
"""server/app/application/services/http_monitor_service.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Service de monitoring HTTP :

- sélectionne les cibles « dues » (actives ET intervalle écoulé)
- exécute la requête HTTP avec méthode/timeout (via un *wrapper* patchable `http_get`)
- met à jour les champs last_* (status_code, latency, erreur)
- ouvre/résout des incidents via le repository dédié

Notes importantes :
- 👉 On utilise **open_session** (et pas get_db), car ce service n'est pas un endpoint FastAPI.
- On expose **http_get(...)** au niveau module pour permettre aux tests E2E de le monkeypatcher
  (ils l’attendaient déjà : cf. test_e2e_incident_lifecycle).
"""

import uuid
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Any  # 👈 ajout de Any

import httpx
from sqlalchemy import select

from app.core.config import settings

from app.infrastructure.persistence.database.session import open_session
from app.infrastructure.persistence.database.models.http_target import HttpTarget
from app.infrastructure.persistence.database.models.incident import Incident
from app.infrastructure.persistence.database.models.incident import IncidentType
from app.infrastructure.persistence.database.models.notification_log import NotificationLog

from app.infrastructure.persistence.repositories.incident_repository import IncidentRepository
from app.infrastructure.persistence.repositories.client_settings_repository import ClientSettingsRepository
from app.infrastructure.persistence.repositories.notification_repository import NotificationRepository

from app.workers.tasks.notification_tasks import notify as notify_task, get_remind_seconds
from app.application.services.notification_service import get_last_notification_sent_at

# 👇 partage du même instant de démarrage que metric_freshness / evaluation
from app.application.services.metric_freshness_service import MONITORING_STARTED_AT

logger = logging.getLogger(__name__)

DEFAULT_METHOD = "GET"
INCIDENT_TITLE_PREFIX = "HTTP check failed: "

# Grâce globale de démarrage, pilotée par config.py
STARTUP_GRACE_SECONDS = settings.STARTUP_GRACE_SECONDS

__all__ = [
    "check_http_targets",
    "check_one_target",
    "http_get",  # ← exposé pour les tests E2E (monkeypatch)
]


# ──────────────────────────────────────────────────────────────────────────────
# Utilitaires internes
# ──────────────────────────────────────────────────────────────────────────────

def _as_utc(d: datetime | None) -> datetime | None:
    """Retourne d en timezone UTC 'aware' (tolère None)."""
    if d is None:
        return None
    if d.tzinfo is None:
        return d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def _should_check(t: HttpTarget, now: datetime) -> bool:
    """Une cible est « due » si active et si l’intervalle depuis last_check_at est écoulé."""
    if not t.is_active:
        return False
    last = _as_utc(t.last_check_at)
    if last is None:
        return True
    interval = timedelta(seconds=t.check_interval_seconds or 300)
    now_utc = _as_utc(now) or now
    return (now_utc - last) >= interval


def _update_result(t: HttpTarget, status: Optional[int], elapsed_ms: Optional[int], err: Optional[str]) -> None:
    now = datetime.now(timezone.utc)

    # Sécurité app-side : ne jamais laisser status à None
    status = 0 if status is None else status
    # Tronquer systématiquement le message d’erreur si présent
    err = (err[:500] if err is not None else None)

    prev_status = t.last_status_code
    # Si tu veux conserver la sémantique "valeur inconnue" pour le tout premier run:
    prev_up = None if prev_status is None else t.is_status_accepted(prev_status)
    curr_up = (err is None) and t.is_status_accepted(status)

    print(
        "[MONITOR]", t.url,
        "prev_status=", prev_status, "status=", status,
        "prev_up=", prev_up, "curr_up=", curr_up, "at", now.isoformat()
    )

    # Gestion des flips d’état (prend en compte l’état initial inconnu)
    if prev_up is None and curr_up is not None:
        print("[MONITOR] init state change -> set last_state_change_at")
        t.last_state_change_at = now
    elif prev_up is not None and prev_up != curr_up:
        print("[MONITOR] flip -> set last_state_change_at")
        t.last_state_change_at = now

    t.last_check_at = now
    t.last_status_code = status
    t.last_response_time_ms = elapsed_ms
    t.last_error_message = err


def _incident_cooldown_ok(db, client_id: uuid.UUID, incident_id: uuid.UUID, remind_seconds: int) -> bool:
    """
    Retourne True si on peut envoyer une notification "incident HTTP" maintenant.

    Best-practice (alignement global) :
      - On NE doit pas raisonner en "slack-only".
      - On s'appuie sur NotificationRepository.get_last_sent_at_any(...),
        qui est la source de vérité des cooldowns et qui exclut déjà les
        providers techniques (TECH_NOTIFICATION_PROVIDERS).

    Règle :
      - last_sent = dernière notif "réelle" envoyée pour CET incident (client_id + incident_id)
      - si last_sent est absente => OK (première notif)
      - sinon => OK uniquement si now - last_sent >= remind_seconds
    """
    # Sécurité : si config foireuse, on évite le spam (fallback conservateur).
    remind_seconds = int(remind_seconds or 0)
    if remind_seconds <= 0:
        remind_seconds = 30 * 60

    now = datetime.now(timezone.utc)

    # ✅ Source de vérité : repo (status=success, sent_at != NULL, providers techniques exclus)
    nrepo = NotificationRepository(db)
    last_sent = nrepo.get_last_sent_at_any(client_id, incident_id)

    last_sent = _as_utc(last_sent)
    if last_sent is None:
        return True

    return (now - last_sent) >= timedelta(seconds=remind_seconds)


def _enqueue_incident_notification(
    incident,
    *,
    severity: str,
    text: str,
) -> None:
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


def _enqueue_incident_notification_simple(
    *,
    incident_id: str,
    client_id: str,
    severity: str,
    text: str,
) -> None:
    """
    Version 'primitives only' — ne dépend d'aucun objet ORM attaché.
    """
    title = "🚨 Incident WARNING" if severity != "info" else "ℹ️ Incident INFO"

    notify_task.apply_async(
        kwargs={
            "payload": {
                "title": title,
                "text": text,
                "severity": severity,      # "warning" / "error" / "info"
                "channel": None,
                "client_id": client_id,    # UUID accepté par le Pydantic du worker
                "incident_id": incident_id,
                "alert_id": None,
            }
        },
        queue="notify",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Wrapper HTTP patchable par les tests (E2E attend ce symbole)
# ──────────────────────────────────────────────────────────────────────────────

def http_get(url: str, method: str = "GET", timeout: int | float = 10):
    """
    Effectue la requête HTTP et renvoie un objet possédant au minimum `.status_code`.
    Conçu pour être *monkeypatché* dans les tests E2E.
    """
    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        http2=False,
        headers={"User-Agent": "MonitoringBot/1.0"},
    ) as client:
        return client.request(method, url)


def _perform_check(t: HttpTarget) -> tuple[Optional[int], Optional[int], Optional[str]]:
    """
    Exécute la requête HTTP (via http_get) et retourne:
      (status_code | 0 sur erreur transport, response_time_ms | None, error_message | None)
    Garantit que le status n'est jamais None (0 = erreur transport).
    """
    timeout_seconds = t.timeout_seconds or 30
    method = (t.method or DEFAULT_METHOD).upper()
    started = time.perf_counter()

    try:
        resp = http_get(t.url, method=method, timeout=timeout_seconds)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        status_code = int(getattr(resp, "status_code", 0) or 0)
        return status_code, elapsed_ms, None
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return 0, elapsed_ms, str(exc)[:500]


# ──────────────────────────────────────────────────────────────────────────────
# API principale du service
# ──────────────────────────────────────────────────────────────────────────────

def check_http_targets() -> int:
    """
    Parcourt les cibles HTTP actives « dues » et retourne le nombre de checks effectués.

    Patch appliqué :
      ✅ cooldown par incident basé sur TOUS les canaux "réels" via NotificationRepository.get_last_sent_at_any
         (donc plus de slack-only).

    Le reste du comportement est inchangé.
    """
    logger.warning("HTTP monitor START v2025-11-06-commit-after-update")

    updated = 0
    now = datetime.now(timezone.utc)

    uptime_sec = (now - MONITORING_STARTED_AT).total_seconds()
    within_startup_grace = uptime_sec < STARTUP_GRACE_SECONDS

    logger.warning(
        "http_monitor: now=%s uptime=%.1fs (global_grace=%ds)",
        now.isoformat(),
        uptime_sec,
        STARTUP_GRACE_SECONDS,
    )

    # Cache regroupement client : client_id -> (enabled, window_seconds|None)
    grouping_cache: dict[uuid.UUID, tuple[bool, int | None]] = {}

    # Buffer des OUVERTURES/REMINDERS autorisés à notifier (post-boucle)
    # PRIMITIVES ONLY : str/int/bool
    open_queue: dict[uuid.UUID, list[dict[str, Any]]] = {}

    # Buffer des RÉSOLUTIONS (post-boucle)
    resolved_buffer: dict[uuid.UUID, list[dict[str, Any]]] = {}

    with open_session() as s:
        irepo = IncidentRepository(s)
        csrepo = ClientSettingsRepository(s)

        targets = s.scalars(select(HttpTarget).where(HttpTarget.is_active.is_(True))).all()
        logger.warning("DEBUG: %d target(s) actives chargées", len(targets))

        for t in targets:
            if not _should_check(t, now):
                logger.warning("DEBUG: target NOT due, skip — id=%s url=%s", t.id, t.url)
                continue

            try:
                # 1) Exécuter le check HTTP
                status, rt_ms, err = _perform_check(t)
                logger.warning(
                    "DEBUG: perform_check — id=%s url=%s status=%s rt_ms=%s err=%r",
                    t.id, t.url, status, rt_ms, err
                )

                # 2) Persist de l'état du target (toujours)
                _update_result(t, status, rt_ms, err)
                updated += 1
                s.flush()
                s.commit()
                logger.warning("DEBUG: target update committed — id=%s", t.id)

                # 3) Description lisible (debug / logs)
                if err:
                    description = err
                elif t.accepted_status_codes:
                    description = (
                        f"Code {status} non accepté (attendu: ranges {t.accepted_status_codes})"
                        if not t.is_status_accepted(status)
                        else f"Code {status} accepté"
                    )
                else:
                    description = t.get_status_message()

                is_unexpected = not t.is_status_accepted(status)
                logger.warning(
                    "DEBUG: unexpected=%s (%s) — id=%s url=%s",
                    is_unexpected, description, t.id, t.url
                )

                # 4) Cooldown seconds (source unique = get_remind_seconds)
                remind_seconds = get_remind_seconds(t.client_id)
                logger.warning("DEBUG: remind_seconds=%s for client_id=%s", remind_seconds, t.client_id)

                # 5) Regroupement (cache settings)
                if t.client_id not in grouping_cache:
                    gcfg = csrepo.get_alert_grouping_settings(t.client_id)  # {"enabled": bool, "window_seconds": int|None}
                    grouping_cache[t.client_id] = (
                        bool(gcfg.get("enabled", False)),
                        int(gcfg.get("window_seconds") or 0) or None,
                    )
                    logger.warning(
                        "DEBUG: grouping_cache set — client_id=%s enabled=%s window=%s",
                        t.client_id, grouping_cache[t.client_id][0], grouping_cache[t.client_id][1]
                    )
                grouping_enabled, grouping_window = grouping_cache[t.client_id]

                # 6) Branche incident / résolution
                if is_unexpected:
                    # 6.a) Grâce globale de démarrage : interdit les OUVERTURES uniquement
                    if within_startup_grace:
                        logger.info(
                            "http_monitor: startup grace active, NOT opening new HTTP incident "
                            "(url=%s, client_id=%s, uptime=%.1fs < %ds)",
                            t.url, t.client_id, uptime_sec, STARTUP_GRACE_SECONDS,
                        )
                        continue

                    # 6.b) Grâce par client : si la cible vient juste de passer DOWN, on attend avant d'ouvrir
                    grace_seconds = csrepo.get_effective_grace_period_seconds(t.client_id) or 0
                    logger.warning("DEBUG: grace_period_seconds=%s for client_id=%s", grace_seconds, t.client_id)

                    if grace_seconds > 0:
                        down_age = None
                        if t.last_state_change_at:
                            down_age = (datetime.now(timezone.utc) - t.last_state_change_at).total_seconds()

                        if down_age is not None and down_age < grace_seconds:
                            # Log d’audit (technique)
                            with open_session() as s_log:
                                NotificationRepository(s_log).add_log(
                                    client_id=t.client_id,
                                    provider="grace",
                                    recipient="",
                                    status="skipped_grace",
                                    message=f"Grace window active ({int(down_age)}/{grace_seconds}s) for {t.url}",
                                    incident_id=None,
                                    alert_id=None,
                                )
                                s_log.commit()

                            logger.warning(
                                "DEBUG: GRACE skip — url=%s age=%ss < grace=%ss (no incident open yet)",
                                t.url, int(down_age), grace_seconds
                            )
                            continue

                    # 6.c) Ouvrir / dédupliquer l’incident HTTP (OPEN) via dedup_key côté DB
                    title = f"{INCIDENT_TITLE_PREFIX}{t.name}"
                    inc, created = irepo.open_http_check(
                        client_id=t.client_id,
                        http_target_id=t.id,
                        title=title,
                        severity="error",
                        description=t.get_status_message(),
                    )
                    logger.warning(
                        "DEBUG: open_http_check → inc_id=%s created=%s (client_id=%s target_id=%s)",
                        getattr(inc, "id", None), created, t.client_id, t.id
                    )

                    # 6.d) Gate reminders : created OU cooldown expiré (par incident, tous canaux réels)
                    ok_to_send = _incident_cooldown_ok(s, t.client_id, inc.id, remind_seconds)
                    logger.warning(
                        "DEBUG: cooldown_gate — inc_id=%s created=%s ok_to_send=%s remind=%s",
                        inc.id, created, ok_to_send, remind_seconds
                    )

                    s.flush()
                    s.commit()

                    if created or ok_to_send:
                        text = (
                            f"{t.name} — {t.url}\n"
                            f"Status: {status}\n"
                            f"Latence: {rt_ms} ms\n"
                            f"Erreur: {err or '-'}\n"
                            f"Détail: {t.get_status_message()}"
                        )
                        open_queue.setdefault(t.client_id, []).append({
                            "incident_id": str(inc.id),
                            "client_id": str(t.client_id),
                            "severity": "warning",
                            "text": text,
                            "title": title,
                            "url": t.url,
                        })
                        logger.warning(
                            "DEBUG: queued OPEN/REMIND — client_id=%s inc_id=%s total_now=%s",
                            t.client_id, inc.id, len(open_queue[t.client_id])
                        )
                    else:
                        logger.warning("DEBUG: NO SEND (cooldown blocking) — inc_id=%s", inc.id)

                else:
                    # 7) Résolution potentielle (OK / status accepté)
                    open_incident = s.scalar(
                        select(Incident)
                        .where(
                            Incident.client_id == t.client_id,
                            Incident.http_target_id == t.id,
                            Incident.status == "OPEN",
                            Incident.incident_type == IncidentType.HTTP_FAILURE,
                        )
                        .order_by(Incident.created_at.desc())
                        .limit(1)
                    )

                    resolved = irepo.resolve_open_by_http_target(
                        client_id=t.client_id,
                        http_target_id=t.id,
                    )
                    logger.warning(
                        "DEBUG: resolve_open_by_http_target — target_id=%s resolved=%s", t.id, resolved
                    )
                    s.flush()
                    s.commit()

                    if resolved:
                        resolved_buffer.setdefault(t.client_id, []).append(
                            {
                                "name": t.name,
                                "url": t.url,
                                "status": status,
                                "ms": rt_ms,
                                "detail": t.get_status_message(),
                                "incident_id": str(open_incident.id) if open_incident else None,
                            }
                        )
                        logger.warning(
                            "DEBUG: buffered resolve — client_id=%s count=%s (this batch)",
                            t.client_id, len(resolved_buffer[t.client_id])
                        )

            except Exception:
                s.rollback()
                logger.exception("http_monitor: error while processing target %s", t.id)

        # 8) ENVOIS OUVERTURES/REMINDERS — POST-BOUCLE (depuis primitives)
        logger.warning("DEBUG: post-loop OPEN decisions — clients=%s", list(open_queue.keys()))
        for client_id, items in open_queue.items():
            grouping_enabled, grouping_window = grouping_cache.get(client_id, (False, None))
            logger.warning(
                "DEBUG: post-loop OPEN — client=%s grouping_enabled=%s window=%s items_buffered=%s",
                client_id, grouping_enabled, grouping_window, len(items)
            )

            # CAS 1 : Grouping OFF → envoi individuel
            if not grouping_enabled or not (grouping_window and grouping_window > 0):
                for item in items:
                    _enqueue_incident_notification_simple(
                        incident_id=item["incident_id"],
                        client_id=item["client_id"],
                        severity=item["severity"],
                        text=item["text"],
                    )
                logger.warning("DEBUG: post-loop OPEN — sent %d individual(s) (grouping OFF)", len(items))
                continue

            # CAS 2 : Grouping ON → fenêtre de regroupement
            last_notification = get_last_notification_sent_at(client_id)
            now_utc = datetime.now(timezone.utc)

            in_window = bool(
                last_notification and (now_utc - last_notification).total_seconds() < (grouping_window or 0)
            )
            logger.warning(
                "DEBUG: post-loop OPEN — last_notification=%s in_window=%s",
                last_notification, in_window
            )

            # Hors fenêtre → individuel
            if not in_window:
                for item in items:
                    _enqueue_incident_notification_simple(
                        incident_id=item["incident_id"],
                        client_id=item["client_id"],
                        severity=item["severity"],
                        text=item["text"],
                    )
                logger.warning("DEBUG: post-loop OPEN — sent %d individual(s) (out of window)", len(items))
                continue

            # Dans fenêtre → si plusieurs items du batch → groupé
            if len(items) >= 2:
                lines = [f"- {i.get('text', '').splitlines()[0]}" for i in items]
                group_text = "🚨 Plusieurs incidents actifs (regroupés):\n" + "\n".join(lines)

                _enqueue_incident_notification_simple(
                    incident_id=items[0]["incident_id"],  # leader technique (prefix UI + cooldown par incident côté notify)
                    client_id=items[0]["client_id"],
                    severity="warning",
                    text=group_text,
                )
                logger.warning("DEBUG: SENT grouped OPEN (batch) — client=%s items=%s", client_id, len(items))
                continue

            # Un seul item → regarder combien d'incidents OPEN existent maintenant
            try:
                with open_session() as s_probe:
                    open_now = IncidentRepository(s_probe).list_open_incidents(client_id)
                open_count = len(open_now) if open_now else 0
                logger.warning("DEBUG: post-loop OPEN — open_now_count=%s", open_count)
            except Exception:
                logger.exception("DEBUG: list_open_incidents failed; fallback to individual")
                open_now, open_count = [], 0

            if open_count >= 2:
                titles = [getattr(r, "title", "Incident") for r in open_now]
                lines = [f"- {t}" for t in titles]
                group_text = "🚨 Plusieurs incidents actifs (regroupés):\n" + "\n".join(lines)

                _enqueue_incident_notification_simple(
                    incident_id=items[0]["incident_id"],
                    client_id=items[0]["client_id"],
                    severity="warning",
                    text=group_text,
                )
                logger.warning(
                    "DEBUG: SENT grouped OPEN (current-open) — client=%s items=%s",
                    client_id, open_count,
                )
            else:
                _enqueue_incident_notification_simple(
                    incident_id=items[0]["incident_id"],
                    client_id=items[0]["client_id"],
                    severity=items[0]["severity"],
                    text=items[0]["text"],
                )
                logger.warning("DEBUG: SENT individual OPEN — client=%s", client_id)

    # 9) ENVOIS RÉSOLUTIONS — POST-BOUCLE (bypass cooldown)
    logger.warning("DEBUG: post-loop grouped RESOLVES — clients=%s", list(resolved_buffer.keys()))
    for client_id, items in resolved_buffer.items():
        grouping_enabled, _ = grouping_cache.get(client_id, (False, None))
        logger.warning(
            "DEBUG: post-loop RESOLVE — client=%s grouping_enabled=%s items=%s",
            client_id, grouping_enabled, len(items)
        )

        if not grouping_enabled:
            for it in items:
                notify_task.apply_async(
                    kwargs={
                        "payload": {
                            "title": "✅ Incident RESOLVED",
                            "text": (
                                f"{it['name']} — {it['url']}\n"
                                f"OK: {it['status']}\n"
                                f"Latence: {it['ms']} ms\n"
                                f"Détail: {it['detail']}"
                            ),
                            "severity": "info",
                            "channel": None,
                            "client_id": str(client_id),
                            "incident_id": it.get("incident_id"),
                            "alert_id": None,
                            "resolved": True,
                        }
                    },
                    queue="notify",
                )
            continue

        if len(items) >= 2:
            lines = [
                f"- {it['name']} — {it['url']} (OK {it['status']}, {it['ms']} ms, {it['detail']})"
                for it in items
            ]
            text = "✅ Incidents résolus (regroupés):\n" + "\n".join(lines)
            notify_task.apply_async(
                kwargs={
                    "payload": {
                        "title": "✅ Incidents RESOLVED (groupés)",
                        "text": text,
                        "severity": "info",
                        "channel": None,
                        "client_id": str(client_id),
                        "incident_id": next((it.get("incident_id") for it in items if it.get("incident_id")), None),
                        "alert_id": None,
                        "resolved": True,
                    }
                },
                queue="notify",
            )
            logger.warning("DEBUG: SENT grouped RESOLVE — client=%s items=%s", client_id, len(items))
        else:
            it = items[0]
            notify_task.apply_async(
                kwargs={
                    "payload": {
                        "title": "✅ Incident RESOLVED",
                        "text": (
                            f"{it['name']} — {it['url']}\n"
                            f"OK: {it['status']}\n"
                            f"Latence: {it['ms']} ms\n"
                            f"Détail: {it['detail']}"
                        ),
                        "severity": "info",
                        "channel": None,
                        "client_id": str(client_id),
                        "incident_id": it.get("incident_id"),
                        "alert_id": None,
                        "resolved": True,
                    }
                },
                queue="notify",
            )
            logger.warning("DEBUG: SENT single RESOLVE — client=%s", client_id)

    logger.warning("HTTP monitor: %d cible(s) vérifiée(s).", updated)
    return updated


def check_one_target(target_id: str) -> dict:
    """
    Check manuel d’une seule cible — pratique pour UI/debug/tests d’intégration.
    Retourne un dict avec clés:
      - ok: bool (True si statut accepté selon la config)
      - status: int
      - ms: int | None
      - error: str | None
      - accepted_status_codes: list | None
      - message: str (interprétation utilisateur)
    """
    try:
        tid = target_id if isinstance(target_id, uuid.UUID) else uuid.UUID(str(target_id))
    except Exception:
        return {"ok": False, "reason": "bad_id"}

    with open_session() as s:
        t = s.get(HttpTarget, tid)
        if not t:
            return {"ok": False, "reason": "not_found"}

        status, elapsed_ms, err = _perform_check(t)
        _update_result(t, status, elapsed_ms, err)
        s.commit()

        ok = t.is_status_accepted(status)

        if err:
            msg = err
        elif t.accepted_status_codes:
            msg = (
                f"Code {status} accepté"
                if t.is_status_accepted(status)
                else f"Code {status} non accepté (attendu: ranges {t.accepted_status_codes})"
            )
        else:
            msg = t.get_status_message()

        return {
            "ok": ok,
            "status": status,
            "ms": elapsed_ms,
            "error": err,
            "accepted_status_codes": t.accepted_status_codes,
            "message": msg,
        }
