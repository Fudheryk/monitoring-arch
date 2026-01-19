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

# ---------------------------------------------------------------------------
# ✅ Source de vérité unique : providers "techniques" à EXCLURE des calculs métier
# (grouping window, "due reminder", etc.).
#
# Raison :
# - ces lignes existent pour l'audit / le debug / des marqueurs de pipeline
# - elles ne doivent pas "compter" comme des notifications utilisateur réelles
# ---------------------------------------------------------------------------
TECH_NOTIFICATION_PROVIDERS: tuple[str, ...] = (
    "grace",       # marqueur de grâce (replanification)
    "group_open",  # marqueur technique de regroupement (si utilisé)
    "cooldown",    # log "skipped_cooldown" / marqueur de cadence
)


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
    Retourne le timestamp de la dernière notification "RÉELLE" envoyée pour ce client.

    Définition "réelle" (best practice) :
      - status='success'
      - sent_at non NULL
      - exclut les providers techniques (audit/markers) qui ne doivent PAS impacter
        la cadence métier (grouping window, reminders, etc.)

    Providers techniques exclus :
      - grace
      - group_open
      - cooldown

    Utilisé notamment par :
      - http_monitor_service (fenêtre de regroupement)
      - runners de reminders (gating "due")

    Args:
        client_id: UUID du client

    Returns:
        datetime (UTC) ou None
    """
    # ✅ Source de vérité : constante partagée du module
    # (que tu as ajoutée : TECH_NOTIFICATION_PROVIDERS = ("grace","group_open","cooldown"))
    with open_session() as s:
        return s.scalar(
            select(NotificationLog.sent_at)
            .where(
                NotificationLog.client_id == client_id,
                NotificationLog.status == "success",
                NotificationLog.sent_at.is_not(None),
                NotificationLog.provider.notin_(TECH_NOTIFICATION_PROVIDERS),
            )
            .order_by(NotificationLog.sent_at.desc())
            .limit(1)
        )
