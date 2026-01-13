# server/app/application/services/notification_service.py
from __future__ import annotations
"""
Notifications (Slack/email) — helpers synchrones (hors Celery).
Sert surtout pour des notifications ad-hoc (ex: admin), le pipeline standard passe par la task Celery.
"""
import logging
import httpx
import uuid
from datetime import datetime

from sqlalchemy import select

from app.infrastructure.persistence.database.session import open_session
from app.infrastructure.persistence.repositories.client_settings_repository import ClientSettingsRepository
from app.infrastructure.persistence.database.models.notification_log import NotificationLog

log = logging.getLogger(__name__)


def notify_slack(text: str, *, client_id: uuid.UUID | None = None, webhook: str | None = None) -> bool:
    """
    Envoie un message Slack de manière SYNCHRONE (hors Celery).

    ⚠️ Pour le pipeline normal de prod, préférer la task Celery `notify`.
    Ici c'est volontairement un helper "one-shot" (admin, debug, etc.).
    """
    url = webhook
    if not url and client_id is not None:
        try:
            with open_session() as s:
                url = ClientSettingsRepository(s).get_effective_slack_webhook(client_id)
        except Exception:
            url = None

    if not url:
        log.info("Slack webhook non configuré : %s", text)
        return False

    try:
        httpx.post(url, json={"text": text}, timeout=5.0)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("Slack send failed: %s", exc)
        return False


def notify_email(subject: str, body: str, *, to: str | None) -> bool:
    """
    Envoi SYNCHRONE d'un email simple (log uniquement).
    Pour la prod, utiliser le provider + task Celery.
    """
    if not to:
        log.info("Email non configuré : %s — %s", subject, body)
        return False
    log.info("Email -> %s : %s — %s", to, subject, body)
    return True


def notify_client(client_id: uuid.UUID, subject: str, text: str) -> dict:
    """
    Notifie synchronement selon la configuration du client.
    Helper ad-hoc, à ne pas utiliser dans les pipelines de prod.
    """
    with open_session() as s:
        repo = ClientSettingsRepository(s)
        results = {"email": False, "slack": False}

        email = repo.get_effective_notification_email(client_id)
        if email:
            results["email"] = notify_email(subject, text, to=email)

        slack_ok = notify_slack(text, client_id=client_id)
        results["slack"] = slack_ok

        return results


def get_last_notification_sent_at(client_id: uuid.UUID) -> datetime | None:
    """
    Retourne le timestamp de la dernière notification RÉELLE envoyée pour ce client
    (groupée ou individuelle). Exclut les marqueurs/grace techniques.

    Utilisé notamment par le monitoring HTTP pour décider si on est dans la
    fenêtre de regroupement d'incidents.
    """
    with open_session() as s:
        return s.scalar(
            select(NotificationLog.sent_at)
            .where(
                NotificationLog.client_id == client_id,
                NotificationLog.status == "success",
                ~NotificationLog.provider.in_(["grace", "group_open"]),  # exclusion technique
            )
            .order_by(NotificationLog.sent_at.desc())
            .limit(1)
        )
