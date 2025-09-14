from __future__ import annotations
"""server/app/workers/tasks/notification_tasks.py
Tâche Celery pour notifications avec :
- Validation du payload (Pydantic)
- Retry/backoff, logging structuré
- Journalisation en base (notification_log)
- Envoi Slack via webhook
- ✅ Cooldown (reminder) unique basé sur UNE variable d'env: settings.ALERT_REMINDER_MINUTES
"""

from typing import Dict, Any
import uuid
import datetime as dt
from datetime import timedelta

from celery import shared_task
from celery.utils.log import get_task_logger
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.workers.celery_app import celery
from app.infrastructure.notifications.providers.slack_provider import SlackProvider
from app.core.config import settings
from app.infrastructure.persistence.database.session import get_sync_session
from app.infrastructure.persistence.database.models.notification_log import NotificationLog

logger = get_task_logger(__name__)


# ---------------------------------------------------------------------------
# Reminder/cooldown: source de vérité unique
#   - Priorité : override explicite (argument) > ENV (settings.ALERT_REMINDER_MINUTES) > défaut 15
#   - On garde cette fonction GLOBALE (réutilisable/testable), pas de version imbriquée.
# ---------------------------------------------------------------------------
def get_remind_minutes(override: int | None) -> int:
    if isinstance(override, int) and override > 0:
        return override
    try:
        env_val = int(getattr(settings, "ALERT_REMINDER_MINUTES", 15))
        return max(1, env_val)
    except Exception:
        return 15


def _as_utc(d: dt.datetime | None) -> dt.datetime | None:
    """Retourne un datetime timezone-aware en UTC (tolère None)."""
    if d is None:
        return None
    if d.tzinfo is None:
        return d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc)


def _fallback_channel() -> str:
    """Canal Slack par défaut si rien n’est fourni."""
    return settings.SLACK_DEFAULT_CHANNEL or "#notif-webhook"


class NotificationPayload(BaseModel):
    """Modèle de validation pour le payload de notification."""
    title: str
    text: str
    kind: str = "info"  # info, warning, error
    message: str = ""
    severity: str = "warning"
    channel: str = Field(
        default_factory=_fallback_channel,
        description="Canal Slack par défaut",
    )
    context: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    username: str = "MonitoringBot"
    icon_emoji: str = ":bell:"
    client_id: uuid.UUID = Field(
        default=uuid.UUID("00000000-0000-0000-0000-000000000000"),
        description="ID du client (UUID)",
    )
    incident_id: uuid.UUID | None = None
    alert_id: uuid.UUID | None = None

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str):
        if v not in ("info", "warning", "error", "critical"):
            raise ValueError("Severity must be info/warning/error/critical")
        return v

    @field_validator("channel", mode="before")
    @classmethod
    def set_default_channel(cls, v):
        v = (v or "").strip()
        return v or _fallback_channel()


def _coerce_uuid(val: Any, default_zero: bool = False) -> uuid.UUID:
    """Sécurise le passage en UUID (utile dans le except de notify())."""
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val))
    except Exception:
        return (
            uuid.UUID("00000000-0000-0000-0000-000000000000")
            if default_zero
            else uuid.uuid4()
        )


def log_notification_to_db(
    client_id: uuid.UUID,
    provider: str,
    recipient: str,
    status: str,
    message: str | None = None,
    error_message: str | None = None,
    incident_id: uuid.UUID | None = None,
    alert_id: uuid.UUID | None = None,
) -> None:
    """
    Journalise une notification dans la base de données.
    - NOTE: on renseigne sent_at uniquement pour 'success'
    """
    try:
        with get_sync_session() as session:
            log_entry = NotificationLog(
                client_id=client_id,
                incident_id=incident_id,
                alert_id=alert_id,
                provider=provider,
                recipient=recipient,
                status=status,
                message=message,
                error_message=error_message,
                sent_at=dt.datetime.now(dt.timezone.utc)
                if status == "success"
                else None,
                created_at=dt.datetime.now(dt.timezone.utc),
            )
            session.add(log_entry)
            session.commit()
            logger.info(
                "Notification logged to database",
                extra={
                    "notification_id": str(log_entry.id),
                    "status": status,
                    "provider": provider,
                },
            )
    except Exception as e:
        logger.error(
            "Failed to log notification to database",
            extra={"error": str(e)},
            exc_info=True,
        )


@celery.task(
    name="tasks.notify",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,  # 30s, 60s, 120s
    retry_kwargs={"max_retries": 3},
    acks_late=True,
    queue="notify",
)
def notify(self, payload: Dict[str, Any]) -> bool:
    """
    Tâche d'envoi de notification (Slack).

    IMPORTANT :
    - Erreurs de validation (payload invalide) → pas de retry (retour False).
    - Webhook manquant → pas de retry (retour False).
    - Erreurs réseau Slack → retry automatique (laisse remonter l’Exception).
    """
    try:
        # 1) Validation (avec fallback de channel)
        payload = {**payload}
        payload.setdefault("channel", _fallback_channel())
        validated = NotificationPayload(**payload)

    except ValidationError as e:
        # Pas de retry sur un payload invalide : c’est non-transitoire.
        log_notification_to_db(
            client_id=_coerce_uuid(payload.get("client_id"), default_zero=True),
            provider="slack",
            recipient=str(payload.get("channel") or _fallback_channel()),
            status="failed",
            message=str(payload.get("text") or ""),
            error_message="payload_validation_error",
            incident_id=payload.get("incident_id"),
            alert_id=payload.get("alert_id"),
        )
        logger.error("Notification payload invalid", extra={"errors": e.errors()})
        return False

    # 2) Configuration Slack
    if not settings.SLACK_WEBHOOK:
        # Pas de webhook → on n’essaie même pas : pas de retry.
        log_notification_to_db(
            client_id=validated.client_id,
            provider="slack",
            recipient=validated.channel,
            status="failed",
            message=f"{validated.title}: {validated.text}",
            error_message="slack_webhook_not_configured",
            incident_id=validated.incident_id,
            alert_id=validated.alert_id,
        )
        logger.error("Slack webhook not configured. Set SLACK_WEBHOOK in the environment.")
        return False

    # 3) Journaliser l'intention (pending)
    log_notification_to_db(
        client_id=validated.client_id,
        provider="slack",
        recipient=validated.channel,
        status="pending",
        message=f"{validated.title}: {validated.text}",
        incident_id=validated.incident_id,
        alert_id=validated.alert_id,
    )

    # 4) Envoi via provider (les erreurs réseau déclencheront un retry)
    slack_params = {
        "title": validated.title,
        "text": validated.text,
        "severity": validated.severity,
        "channel": validated.channel or _fallback_channel(),
        "username": validated.username,
        "icon_emoji": validated.icon_emoji,
        "context": validated.context or None,
    }

    try:
        provider = SlackProvider()  # settings.SLACK_WEBHOOK déjà garanti
        success = provider.send(**slack_params)
    except Exception as e:
        # Erreur transitoire (réseau, webhook non joignable, etc.) → retry
        log_notification_to_db(
            client_id=validated.client_id,
            provider="slack",
            recipient=validated.channel,
            status="failed",
            message=f"{validated.title}: {validated.text}",
            error_message=str(e),
            incident_id=validated.incident_id,
            alert_id=validated.alert_id,
        )
        logger.error("Notification error", extra={"error": str(e)}, exc_info=True)
        raise self.retry(exc=e)

    if not success:
        # Échec applicatif (HTTP non-200) → on loggue et laisse retry (Exception)
        log_notification_to_db(
            client_id=validated.client_id,
            provider="slack",
            recipient=validated.channel,
            status="failed",
            message=f"{validated.title}: {validated.text}",
            error_message="slack_api_request_failed",
            incident_id=validated.incident_id,
            alert_id=validated.alert_id,
        )
        logger.warning("Notification failed (Slack API returned non-ok)")
        raise Exception("Slack API request failed")

    # 5) succès → log 'success'
    log_notification_to_db(
        client_id=validated.client_id,
        provider="slack",
        recipient=validated.channel,
        status="success",
        message=f"{validated.title}: {validated.text}",
        incident_id=validated.incident_id,
        alert_id=validated.alert_id,
    )

    logger.info(
        "Notification sent successfully",
        extra={"channel": validated.channel, "severity": validated.severity, "title": validated.title},
    )
    return True


@celery.task(
    name="notify_alert",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_kwargs={"max_retries": 3},
    acks_late=True,
    queue="notify",
)
def notify_alert(self, alert_id: str, *, remind_after_minutes: int | None = None) -> None:
    """
    Notifie une alerte par Slack avec cooldown.
    Envoi si :
      - aucune "success" encore envoyée pour cette alerte, OU
      - la dernière "success" date de plus que le cooldown.

    Cooldown = get_remind_minutes(remind_after_minutes) minutes.
    """
    if not alert_id:
        logger.warning("notify_alert appelé sans alert_id")
        return

    try:
        alert_uuid = uuid.UUID(str(alert_id))
    except Exception:
        logger.warning("notify_alert appelé avec un alert_id invalide: %r", alert_id)
        return

    # Imports locaux pour éviter les cycles
    from sqlalchemy import select
    from app.infrastructure.persistence.database.models.alert import Alert
    from app.infrastructure.persistence.database.models.machine import Machine
    from app.infrastructure.persistence.database.models.metric import Metric
    from app.infrastructure.persistence.database.models.threshold import Threshold  # noqa: F401
    from app.workers.tasks.notification_tasks import notify as notify_task

    remind_minutes = get_remind_minutes(remind_after_minutes)
    cooldown = timedelta(minutes=remind_minutes)

    logger.info(
        "notify_alert cooldown",
        extra={"alert_id": str(alert_uuid), "remind_minutes": remind_minutes},
    )

    try:
        with get_sync_session() as session:
            alert = session.get(Alert, alert_uuid)
            if not alert:
                logger.warning("Alerte %s non trouvée", alert_uuid)
                return

            if (alert.status or "").upper() != "FIRING":
                logger.info(f"Alerte {alert_id} ignorée (status={alert.status})")
                return

            machine = session.get(Machine, alert.machine_id) if alert.machine_id else None
            metric = session.get(Metric, alert.metric_id) if alert.metric_id else None

            # Anti-spam cooldown: dernière réussite pour CETTE alerte (sent_at)
            last_success_ts = session.scalar(
                select(NotificationLog.sent_at)
                .where(
                    NotificationLog.alert_id == alert.id,
                    NotificationLog.status == "success",
                    NotificationLog.provider == "slack",
                )
                .order_by(NotificationLog.sent_at.desc())
                .limit(1)
            )

            last_success_ts = _as_utc(last_success_ts)
            now_utc = dt.datetime.now(dt.timezone.utc)
            if last_success_ts and (now_utc - last_success_ts) < cooldown:
                logger.info(
                    "Notification skip (cooldown actif)",
                    extra={
                        "alert_id": str(alert.id),
                        "elapsed_seconds": int((now_utc - last_success_ts).total_seconds()),
                        "cooldown_seconds": int(cooldown.total_seconds()),
                        "remind_after_minutes": remind_minutes,
                    },
                )
                return

            # Message
            metric_name = getattr(metric, "name", "unknown_metric")
            base_msg = alert.message or f"Threshold breach on {metric_name}"
            text = f"{base_msg} - Valeur: {alert.current_value}"

            sev_raw = (alert.severity or "warning").lower()
            sev = "error" if sev_raw == "critical" else sev_raw  # map vers info|warning|error

            client_id = getattr(machine, "client_id", uuid.UUID("00000000-0000-0000-0000-000000000000"))
            if not isinstance(client_id, uuid.UUID):
                client_id = _coerce_uuid(client_id, default_zero=True)

            payload = {
                "title": f"🚨 Alerte {sev.upper()}",
                "text": text,
                "severity": sev,
                "channel": _fallback_channel(),
                "client_id": client_id,
                "alert_id": alert.id,
                "incident_id": None,
            }

            # Enqueue la sous-tâche notify (ne pas appeler notify() direct)
            notify_task.apply_async(kwargs={"payload": payload}, queue="notify")
            logger.info(
                "Notification enqueued",
                extra={"alert_id": str(alert.id), "remind_after_minutes": remind_minutes},
            )

    except Exception as e:
        logger.error(f"Erreur notification alerte {alert_id}: {e}", exc_info=True)
        raise self.retry(exc=e)


@shared_task(name="tasks.test_notification")
def test_notification():
    """
    Tâche de test pour vérifier la config des notifications.
    - Enfile une notification d'info vers le canal par défaut.
    """
    logger.info("Starting test notification task")

    if not settings.SLACK_WEBHOOK:
        error_msg = "SLACK_WEBHOOK not configured in environment"
        logger.error(error_msg)
        return {"status": "error", "message": error_msg}

    test_payload = {
        "title": "Test Notification",
        "text": "Ceci est un test de notification depuis le système de monitoring",
        "severity": "info",
        "channel": settings.SLACK_DEFAULT_CHANNEL,
        "client_id": uuid.UUID("00000000-0000-0000-0000-000000000000"),
        "incident_id": None,
        "alert_id": None,
    }

    logger.info(
        "Test payload prepared", extra={"payload": {**test_payload, "text": "[omitted]"}}
    )

    try:
        # IMPORTANT: on passe par Celery (apply_async) pour respecter la signature bind=True
        res = notify.apply_async(kwargs={"payload": test_payload}, queue="notify")
        logger.info("Test notification enqueued", extra={"task_id": res.id})
        return {"status": "enqueued", "task_id": res.id}
    except Exception as e:
        logger.error("Test notification failed", extra={"error": str(e)}, exc_info=True)
        return {"status": "error", "message": str(e)}
