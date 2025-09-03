from __future__ import annotations
"""server/app/workers/tasks/notification_tasks.py
Tâche Celery pour notifications avec :
- Gestion avancée des erreurs
- Validation du payload
- Logging structuré
- Configuration centralisée
- Journalisation en base de données
- Support Slack avec webhook configuré
- ✅ Rappel périodique paramétrable (cooldown) pour éviter le spam
"""
from typing import Dict, Any
import uuid
import datetime as dt
from datetime import timedelta
from celery import shared_task
from celery.utils.log import get_task_logger
from pydantic import BaseModel, validator, Field
from app.workers.celery_app import celery
from app.infrastructure.notifications.providers.slack_provider import SlackProvider
from app.core.config import settings
from app.infrastructure.persistence.database.session import get_sync_session
from app.infrastructure.persistence.database.models.notification_log import NotificationLog

logger = get_task_logger(__name__)

# === Utils de configuration locale ===
def _default_remind_minutes() -> int:
    """
    Valeur par défaut pour l'intervalle de rappel (en minutes).
    - Essaie d'abord settings.ALERT_REMIND_MINUTES
    - Puis settings.NOTIF_REMIND_MINUTES
    - Sinon fallback à 60 min
    """
    for attr in ("ALERT_REMIND_MINUTES", "NOTIF_REMIND_MINUTES"):
        try:
            v = int(getattr(settings, attr))  # peut lever AttributeError/ValueError
            if v > 0:
                return v
        except Exception:
            pass
    return 60


class NotificationPayload(BaseModel):
    """Modèle de validation pour le payload de notification"""
    title: str
    text: str
    kind: str = "info"  # info, warning, error
    message: str = ""
    severity: str = "warning"
    channel: str = Field(default=settings.SLACK_DEFAULT_CHANNEL, description="Canal Slack par défaut: #notif-webhook")
    context: Dict[str, Any] = {}
    metadata: Dict[str, Any] = {}
    username: str = "MonitoringBot"
    icon_emoji: str = ":bell:"
    client_id: uuid.UUID = Field(default_factory=lambda: uuid.UUID('00000000-0000-0000-0000-000000000000'), description="ID du client requis")
    incident_id: uuid.UUID | None = Field(default=None, description="ID de l'incident associé")
    alert_id: uuid.UUID | None = Field(default=None, description="ID de l'alerte associée")

    @validator('severity')
    def validate_severity(cls, v):
        """Valide que la sévérité est une valeur autorisée"""
        if v not in ("info", "warning", "error", "critical"):
            # On accepte aussi "critical" car c'est utilisé côté alertes
            raise ValueError("Severity must be info/warning/error/critical")
        return v
    
    @validator('channel')
    def set_default_channel(cls, v):
        """Assure que le canal par défaut est utilisé si non spécifié"""
        return v or settings.SLACK_DEFAULT_CHANNEL


def log_notification_to_db(
    client_id: uuid.UUID,
    provider: str,
    recipient: str,
    status: str,
    message: str | None = None,
    error_message: str | None = None,
    incident_id: uuid.UUID | None = None,
    alert_id: uuid.UUID | None = None
) -> None:
    """
    Journalise une notification dans la base de données
    
    Args:
        client_id: ID du client (requis)
        provider: Fournisseur de notification (slack, email, etc.)
        recipient: Destinataire (email, channel Slack, etc.)
        status: Statut de la notification (success, failed, pending)
        message: Message de la notification
        error_message: Message d'erreur en cas d'échec
        incident_id: ID de l'incident associé
        alert_id: ID de l'alerte associée
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
                sent_at=dt.datetime.now(dt.timezone.utc) if status == 'success' else None,
                created_at=dt.datetime.now(dt.timezone.utc)
            )
            session.add(log_entry)
            session.commit()
            logger.info("Notification logged to database", extra={
                "notification_id": str(log_entry.id),
                "status": status,
                "provider": provider
            })
    except Exception as e:
        logger.error("Failed to log notification to database", 
                    extra={"error": str(e)}, exc_info=True)


@celery.task(
    name="tasks.notify",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,  # 30s, 60s, 120s
    retry_kwargs={'max_retries': 3},
    acks_late=True,
    queue="notify"
)
def notify(self, payload: Dict[str, Any]) -> bool:
    """
    Tâche de notification avec :
    - Validation du payload
    - Retry automatique
    - Logging structuré
    - Journalisation en base de données
    - Utilisation du webhook Slack configuré dans .env
    
    Exemple d'appel :
    notify.delay({
        "title": "Incident DB",
        "text": "Latence élevée sur le serveur de base de données",
        "severity": "error",
        "channel": "#alerts-prod",
        "client_id": "client-uuid",
        "incident_id": "incident-uuid",
        "alert_id": "alert-uuid"
    })
    """
    try:
        # Validation du payload
        validated = NotificationPayload(**payload)
        
        # Vérification de la configuration Slack
        if not settings.SLACK_WEBHOOK:
            error_msg = "Slack webhook not configured. Set SLACK_WEBHOOK in .env"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        # Journalisation avant envoi
        log_notification_to_db(
            client_id=validated.client_id,
            provider='slack',
            recipient=validated.channel,
            status='pending',
            message=f"{validated.title}: {validated.text}",
            incident_id=validated.incident_id,
            alert_id=validated.alert_id
        )
        
        # Préparer les paramètres pour SlackProvider selon sa signature exacte
        slack_params = {
            'title': validated.title,
            'text': validated.text,
            'severity': validated.severity,
            'channel': validated.channel,
            'username': validated.username,
            'icon_emoji': validated.icon_emoji,
            'context': validated.context or None
        }
        
        # Envoi avec SlackProvider
        provider = SlackProvider()
        success = provider.send(**slack_params)
        
        if not success:
            # Journalisation de l'échec
            log_notification_to_db(
                client_id=validated.client_id,
                provider='slack',
                recipient=validated.channel,
                status='failed',
                message=f"{validated.title}: {validated.text}",
                error_message="Slack API request failed",
                incident_id=validated.incident_id,
                alert_id=validated.alert_id
            )
            
            logger.warning("Notification failed", extra={
                "payload": {k: v for k, v in payload.items() if k != 'text'},
                "retries": self.request.retries,
                "slack_webhook": settings.SLACK_WEBHOOK[:20] + "..."  # Log partiel pour sécurité
            })
            raise Exception("Slack API request failed")
        
        # Journalisation du succès
        log_notification_to_db(
            client_id=validated.client_id,
            provider='slack',
            recipient=validated.channel,
            status='success',
            message=f"{validated.title}: {validated.text}",
            incident_id=validated.incident_id,
            alert_id=validated.alert_id
        )
        
        logger.info("Notification sent successfully", extra={
            "channel": validated.channel,
            "severity": validated.severity,
            "title": validated.title
        })
            
        return True
        
    except Exception as e:
        # Journalisation de l'erreur
        log_notification_to_db(
            client_id=payload.get('client_id', uuid.UUID('00000000-0000-0000-0000-000000000000')),
            provider='slack',
            recipient=payload.get('channel', settings.SLACK_DEFAULT_CHANNEL),
            status='failed',
            message=payload.get('text', ''),
            error_message=str(e),
            incident_id=payload.get('incident_id'),
            alert_id=payload.get('alert_id')
        )
        
        logger.error("Notification error", 
            extra={
                "error": str(e),
                "payload": {k: v for k, v in payload.items() if k != 'text'},  # Éviter de logger tout le texte
                "retries": self.request.retries
            },
            exc_info=True
        )
        raise self.retry(exc=e)

@shared_task(
    name="notify_alert",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_kwargs={'max_retries': 3},
    acks_late=True,
    queue="notify"
)
def notify_alert(self, alert_id: str, *, remind_after_minutes: int | None = None) -> None:
    """
    Tâche optimisée pour notifier une alerte spécifique.
    Ne reçoit que l'ID de l'alerte pour découplage total.

    ✅ Rappel (cooldown) paramétrable
    - On n'envoie PAS à chaque évaluation.
    - On envoie si AUCUNE notification "success" n'a encore été envoyée (création),
      sinon uniquement si la dernière "success" a plus de `remind_after_minutes`.
    - `remind_after_minutes` est optionnel ; si None → valeur par défaut (ALERT_REMINDER_MINUTES, déf. 15).
    """
    import logging
    logger = logging.getLogger(__name__)

    if not alert_id:
        logger.warning("notify_alert appelé sans alert_id")
        return

    from app.infrastructure.persistence.database.session import get_sync_session
    from app.infrastructure.persistence.database.models.alert import Alert
    from app.infrastructure.persistence.database.models.machine import Machine
    from app.infrastructure.persistence.database.models.metric import Metric
    from app.infrastructure.persistence.database.models.threshold import Threshold
    from app.workers.tasks.notification_tasks import notify  # réutilise la tâche notify()

    from datetime import timedelta
    from sqlalchemy import select
    from app.infrastructure.persistence.database.models.notification_log import NotificationLog
    import datetime as dt

    # Détermine l'intervalle de rappel (minutes)
    def _default_remind_minutes() -> int:
        try:
            return int(getattr(settings, "ALERT_REMINDER_MINUTES", 15))
        except Exception:
            return 15

    remind_minutes = (
        remind_after_minutes
        if (isinstance(remind_after_minutes, int) and remind_after_minutes > 0)
        else _default_remind_minutes()
    )
    cooldown = timedelta(minutes=remind_minutes)

    try:
        with get_sync_session() as session:
            # Récupération de l'alerte
            alert = session.get(Alert, alert_id)
            if not alert:
                logger.warning(f"Alerte {alert_id} non trouvée")
                return

            # 🔎 Charger explicitement via *_id (aucune relation ORM n'est définie sur Alert)
            machine = session.get(Machine, alert.machine_id) if alert.machine_id else None
            metric  = session.get(Metric,  alert.metric_id)  if alert.metric_id  else None
            threshold = session.get(Threshold, alert.threshold_id) if alert.threshold_id else None

            # ✅ POLITIQUE: filtrer les sévérités notifiables
            sev_raw = (alert.severity or "warning").lower()
            if sev_raw not in {"warning", "critical", "error"}:
                logger.info(f"Alerte {alert_id} ignorée (severity: {alert.severity})")
                return

            # 🔁 Contrôle de rappel anti-spam (un seul bloc)
            # On prend la dernière notif "success" pour CETTE alerte
            last_success_ts = session.scalar(
                select(NotificationLog.created_at)
                .where(
                    NotificationLog.alert_id == alert.id,
                    NotificationLog.status == "success",
                )
                .order_by(NotificationLog.created_at.desc())
                .limit(1)
            )

            if last_success_ts:
                elapsed = dt.datetime.now(dt.timezone.utc) - last_success_ts
                if elapsed < cooldown:
                    logger.info(
                        "Notification skip (cooldown actif)",
                        extra={
                            "alert_id": str(alert.id),
                            "elapsed_seconds": int(elapsed.total_seconds()),
                            "cooldown_seconds": int(cooldown.total_seconds()),
                            "remind_after_minutes": remind_minutes,
                        },
                    )
                    return
            # Sinon: aucune notif précédente -> envoi autorisé

            # Construction du message (fallbacks si metric/machine absents)
            metric_name = getattr(metric, "name", "unknown_metric")
            text = f"{alert.message or f'Threshold breach on {metric_name}'} - Valeur: {alert.current_value}"

            # Mappe la sévérité pour respecter le schéma de NotificationPayload (info|warning|error)
            sev = "error" if sev_raw == "critical" else sev_raw

            payload = {
                'title': f'🚨 Alerte {sev.upper()}',
                'text': text,
                'severity': sev,
                'channel': settings.SLACK_DEFAULT_CHANNEL,
                'client_id': str(getattr(machine, "client_id", "00000000-0000-0000-0000-000000000000")),
                'alert_id': str(alert.id),
                'incident_id': None
            }

            # 📤 Enqueue la sous-tâche notify via Celery (ne pas appeler notify() en direct)
            notify.apply_async(kwargs={'payload': payload}, queue='notify')
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
    Tâche de test pour vérifier la configuration des notifications
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
        "client_id": uuid.UUID('00000000-0000-0000-0000-000000000000'),  # UUID par défaut
        "incident_id": None,
        "alert_id": None
    }
    
    logger.info("Test payload prepared", extra={"payload": test_payload})
    
    try:
        # Appeler directement la fonction notify (pas .delay)
        result = notify(test_payload)
        logger.info("Test notification completed", extra={"result": result})
        return {"status": "success", "result": result}
    except Exception as e:
        logger.error("Test notification failed", extra={"error": str(e)}, exc_info=True)
        return {"status": "error", "message": str(e)}
