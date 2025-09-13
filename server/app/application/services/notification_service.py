from __future__ import annotations
"""server/app/application/services/notification_service.py
~~~~~~~~~~~~~~~~~~~~~~~~
Notifications (stub Slack/email).
"""
from app.infrastructure.persistence.database.session import get_sync_session
from app.infrastructure.persistence.database.models.client_settings import ClientSettings
from app.core.config import settings
import logging
import httpx




log = logging.getLogger(__name__)

def notify_slack(text: str, *, webhook: str | None = None) -> bool:
    url = webhook or settings.SLACK_WEBHOOK
    if not url:
        log.info("Slack webhook non configuré : %s", text)
        return False
    try:
        httpx.post(url, json={"text": text}, timeout=5.0)
        return True
    except Exception as exc:
        log.warning("Slack send failed: %s", exc)
        return False

def notify_email(subject: str, body: str, *, to: str | None) -> bool:
    if not to:
        log.info("Email non configuré : %s — %s", subject, body)
        return False
    log.info("Email -> %s : %s — %s", to, subject, body)
    return True

def notify_client(client_id, subject: str, text: str) -> dict:
    with get_sync_session() as session:
        s = session.query(ClientSettings).filter_by(client_id=client_id).one_or_none()
        results = {"email": False, "slack": False}
        if s and s.notification_email:
            results["email"] = notify_email(subject, text, to=s.notification_email)
        if s and s.slack_webhook_url:
            results["slack"] = notify_slack(text, webhook=s.slack_webhook_url)
        return results
