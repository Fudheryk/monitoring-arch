from __future__ import annotations
"""server/app/workers/tasks/notification_tasks.py

Tâches Celery pour les notifications avec :

- Validation du payload (Pydantic)
- Retry/backoff + logging structuré
- Journalisation en base (notification_log)
- Envoi Slack via webhook par client (BEST-EFFORT, ne bloque jamais l'email)
- Envoi Email par client (avec retry Celery sur erreurs réseau/SMTP)
- ✅ Cooldown (reminder) basé sur client_settings.reminder_notification_seconds
  avec fallback sur settings.ALERT_REMINDER_MINUTES
"""

from typing import Dict, Any, Optional
import uuid
import datetime as dt
import re


from sqlalchemy import select

from celery.utils.log import get_task_logger
from pydantic import BaseModel, Field, ValidationError, field_validator
from smtplib import SMTPException

from app.workers.celery_app import celery
from app.infrastructure.notifications.providers.email_provider import EmailProvider
from app.infrastructure.notifications.providers.slack_provider import SlackProvider
from app.core.config import settings
from app.infrastructure.persistence.database.session import open_session
from app.infrastructure.persistence.database.models.incident import Incident, IncidentType
from app.infrastructure.persistence.repositories.client_settings_repository import ClientSettingsRepository
from app.infrastructure.persistence.repositories.notification_repository import NotificationRepository

logger = get_task_logger(__name__)


# Prefix attendu en UI : "(#001) " (ancré au début + espace)
_INC_PREFIX_RE = re.compile(r"^\(#\d+\)\s+")
 

# ---------------------------------------------------------------------------
# Cooldown / reminder : source de vérité unique (en secondes)
#   - Priorité :
#       1) client_settings.reminder_notification_seconds (>0)
#       2) settings.ALERT_REMINDER_MINUTES (minutes -> secondes)
#       3) défaut dur = 30 minutes
# ---------------------------------------------------------------------------
def get_remind_seconds(client_id: str | uuid.UUID | None) -> int:
    DEFAULT_SECONDS = 30 * 60

    def _env_seconds() -> int:
        try:
            minutes = int(getattr(settings, "ALERT_REMINDER_MINUTES", 30))
            return max(1, minutes) * 60
        except Exception:
            return DEFAULT_SECONDS

    if not client_id:
        logger.info("get_remind_seconds: no client_id → ENV fallback")
        return _env_seconds()

    # Coerce / valide l'UUID
    try:
        cid = client_id if isinstance(client_id, uuid.UUID) else uuid.UUID(str(client_id))
    except Exception:
        logger.warning("get_remind_seconds: bad client_id %r → ENV fallback", client_id)
        return _env_seconds()

    # DB → repo (source de vérité)
    try:
        with open_session() as s:
            repo = ClientSettingsRepository(s)
            seconds = repo.get_effective_reminder_seconds(cid)
            return int(seconds)
    except Exception:
        logger.warning("get_remind_seconds: DB error → ENV fallback", exc_info=True)
        return _env_seconds()


def _fallback_channel() -> str:
    """Canal Slack par défaut si rien n’est fourni."""
    return settings.SLACK_DEFAULT_CHANNEL


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

    # Context (facultatif) : données additionnelles pour enrichir le message
    context: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    username: str = "NeonMonitor Core"
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


def _coerce_uuid(val: Any, default_zero: bool = False) -> uuid.UUID | None:
    """
    Sécurise le passage en UUID pour les logs.

    - default_zero=True → retourne le zero-UUID en cas d'erreur
    - default_zero=False → retourne None en cas d'erreur
    """
    if isinstance(val, uuid.UUID):
        return val
    if val is None:
        return None if not default_zero else uuid.UUID("00000000-0000-0000-0000-000000000000")
    try:
        return uuid.UUID(str(val))
    except Exception:
        return (
            uuid.UUID("00000000-0000-0000-0000-000000000000")
            if default_zero
            else None
        )

# ---------------------------------------------------------------------------
# Helpers de logging & envoi (réutilisés dans notify)
# ---------------------------------------------------------------------------


def _fmt_incident_prefix(num: int | None) -> str:
    if not num or num <= 0:
        return ""  # si pas de numéro, on ne préfixe pas
    return f"(#{num:03d}) "


def _format_incident_display_title(inc: Incident, raw_title: str | None = None) -> str:
    """
    Construit un title "UI" sans modifier la DB.
    - raw_title : si fourni, utilisé à la place de inc.title
    """
    base = (raw_title if raw_title is not None else getattr(inc, "title", "")) or ""
    base = base.lstrip()
    prefix = _fmt_incident_prefix(getattr(inc, "incident_number", None))
    return (prefix + base) if prefix else base


def _ensure_incident_prefix(
    *,
    session,
    validated: NotificationPayload,
) -> None:
    """
    Ajoute automatiquement '(#XYZ) ' au début de validated.title (et validated.text si tu veux),
    si incident_id est présent et que le title n'est pas déjà préfixé.
    """
    if not validated.incident_id:
        return

    # Déjà préfixé ? -> rien à faire
    if _INC_PREFIX_RE.match((validated.title or "")):
        return

    inc = session.scalar(
        select(Incident).where(Incident.id == validated.incident_id).limit(1)
    )
    if not inc:
        return

    # Préfixer le title (affichage seulement)
    validated.title = _format_incident_display_title(inc, validated.title)


def _log_notification(
    nrepo: NotificationRepository,
    validated: Any,
    provider: str,
    recipient: str,
    status: str,
    error_message: Optional[str] = None,
    set_sent_at: bool = False,
) -> None:
    """Helper centralisé pour logger une notification dans notification_log."""
    nrepo.add_log(
        client_id=validated.client_id,
        provider=provider,
        recipient=recipient,
        status=status,
        message=f"{validated.title}: {validated.text}",
        error_message=error_message,
        incident_id=validated.incident_id,
        alert_id=validated.alert_id,
        set_sent_at=set_sent_at,
    )


def _validate_payload(payload: Dict[str, Any]) -> Optional[NotificationPayload]:
    """
    Valide le payload Pydantic.

    En cas d'erreur :
    - écrit un log "failed(payload_validation_error)" dans une session dédiée
    - retourne None (pas de retry → erreur de données, pas une erreur système)
    """
    try:
        payload = {**payload}
        payload.setdefault("channel", _fallback_channel())
        return NotificationPayload(**payload)
    except ValidationError as e:
        # Session dédiée pour ne pas dépendre de la transaction principale
        with open_session() as s:
            repo = NotificationRepository(s)

            # On reconstruit un "mini validated" pour logger proprement
            fake_validated = type(
                "obj",
                (object,),
                {
                    "client_id": _coerce_uuid(payload.get("client_id"), default_zero=True)
                    or uuid.UUID("00000000-0000-0000-0000-000000000000"),
                    "title": str(payload.get("title") or "[invalid-title]"),
                    "text": str(payload.get("text") or ""),
                    "incident_id": _coerce_uuid(payload.get("incident_id")),
                    "alert_id": _coerce_uuid(payload.get("alert_id")),
                },
            )

            repo.add_log(
                client_id=fake_validated.client_id,
                provider="system",  # ce n'est pas Slack encore → "system"
                recipient=str(payload.get("channel") or _fallback_channel()),
                status="failed",
                message=f"{fake_validated.title}: {fake_validated.text}",
                error_message=f"payload_validation_error: {e.errors()}",
                incident_id=fake_validated.incident_id,
                alert_id=fake_validated.alert_id,
                set_sent_at=False,
            )

        logger.error("Notification payload invalid", extra={"errors": e.errors()})
        return None


def _check_cooldown(
    nrepo: NotificationRepository,
    validated: NotificationPayload,
    payload: Dict[str, Any],
) -> bool:
    """
    Vérifie le cooldown global.

    Retourne:
    - True  → cooldown actif → on a loggé "skipped_cooldown" et on doit s'arrêter
    - False → pas de cooldown actif
    """
    # Permet à certains messages (ex: rappels groupés) de bypass le cooldown global.
    if payload.get("skip_cooldown"):
        logger.debug("_check_cooldown: skip_cooldown=True → bypass")
        return False

    is_resolved = bool(payload.get("resolved", False))

    logger.info(
        "Notification payload received",
        extra={
            "client_id": str(validated.client_id),
            "incident_id": str(validated.incident_id) if validated.incident_id else None,
            "alert_id": str(validated.alert_id) if validated.alert_id else None,
            "severity": validated.severity,
            "payload": {**payload, "text": "[omitted]"},
        },
    )

    # ⚠️ Cas spécial : alertes de seuil (notify_alert)
    # Le cooldown est déjà géré dans notify_alert (par alert_id),
    # donc on NE réapplique PAS ici un cooldown global.
    if validated.alert_id is not None:
        logger.debug(
            "_check_cooldown: skipping global cooldown for alert_id=%s "
            "(handled by notify_alert)",
            validated.alert_id,
        )
        return False

    last_sent = nrepo.get_last_sent_at_any(validated.client_id, validated.incident_id)
    remind = get_remind_seconds(validated.client_id)

    if last_sent is not None and not is_resolved:
        age = (dt.datetime.now(dt.timezone.utc) - last_sent).total_seconds()
        if age < remind:
            _log_notification(
                nrepo,
                validated,
                provider="cooldown",
                recipient="",
                status="skipped_cooldown",
            )
            logger.info(
                "Notification skipped by cooldown",
                extra={
                    "client_id": str(validated.client_id),
                    "incident_id": str(validated.incident_id),
                    "age_sec": int(age),
                    "remind_sec": int(remind),
                },
            )
            return True
    return False


def reset_alert_cooldown_for_machine(
    client_id: uuid.UUID,
    machine_id: uuid.UUID,
) -> int:
    """
    Réinitialise le cooldown des alertes de seuil pour une machine donnée.

    Concrètement :
      - On supprime les entrées de notification_log liées aux alertes (alert_id)
        de cette machine.
      - Ainsi, la prochaine notification de seuil pour ces alertes sera
        considérée comme un "premier défaut" (pas de skipped_cooldown).

    Retourne:
        int: nombre de lignes notification_log supprimées.
    """
    from sqlalchemy import select
    from app.infrastructure.persistence.database.models.alert import Alert
    from app.infrastructure.persistence.database.models.notification_log import NotificationLog

    with open_session() as s:
        # Récupère toutes les alertes liées à cette machine
        alert_ids = s.scalars(
            select(Alert.id).where(Alert.machine_id == machine_id)
        ).all()

        if not alert_ids:
            logger.debug(
                "reset_alert_cooldown_for_machine: no alerts found "
                "for client_id=%s machine_id=%s",
                client_id,
                machine_id,
            )
            return 0

        # On supprime les logs associés à ces alertes
        deleted = (
            s.query(NotificationLog)
            .filter(NotificationLog.alert_id.in_(alert_ids))
            .delete(synchronize_session=False)
        )

        s.commit()

    logger.info(
        "reset_alert_cooldown_for_machine: cleared %d notification_log rows "
        "for client_id=%s machine_id=%s",
        deleted,
        client_id,
        machine_id,
    )
    return deleted


def _send_slack_safe(
    nrepo: NotificationRepository,
    webhook: str,
    validated: NotificationPayload,
) -> bool:
    """
    Envoie la notification Slack en mode BEST-EFFORT.

    - Ne lève JAMAIS d'exception (pour ne pas bloquer l'email).
    - Loggue systématiquement en base (success/failed) avec détails.
    - Retourne True si succès, False sinon.
    """
    status_code: Optional[int] = None
    response_text: Optional[str] = None
    error_detail: Optional[str] = None

    try:
        provider = SlackProvider(webhook=webhook)

        result = provider.send(
            title=validated.title,
            text=validated.text,
            severity=validated.severity,
            channel=validated.channel or _fallback_channel(),
            username=validated.username,
            icon_emoji=validated.icon_emoji,
            context=validated.context or None,
        )

        # Support ancien + nouveau format de retour :
        # - bool
        # - (bool, status_code, response_text)
        if isinstance(result, tuple) and len(result) >= 3:
            success, status_code, response_text = result[0], result[1], result[2]
        else:
            success = bool(result)

        if not success:
            # Construction d'un message d'erreur détaillé
            error_parts = ["slack_api_request_failed"]
            if status_code is not None:
                error_parts.append(f"status={status_code}")
            if response_text:
                trimmed = response_text[:500]  # tronquer pour éviter des logs énormes
                error_parts.append(f"body={trimmed}")
            error_detail = " | ".join(error_parts)

        _log_notification(
            nrepo,
            validated,
            provider="slack",
            recipient=validated.channel or _fallback_channel(),
            status="success" if success else "failed",
            error_message=error_detail,
            set_sent_at=success,
        )

        if success:
            logger.info(
                "Slack notification sent",
                extra={
                    "channel": validated.channel,
                    "severity": validated.severity,
                    "title": validated.title,
                    "status_code": status_code,
                },
            )
        else:
            logger.warning(
                "Slack notification failed",
                extra={
                    "status_code": status_code,
                    "response": response_text[:200] if response_text else None,
                    "channel": validated.channel,
                    "error_detail": error_detail,
                },
            )

        return success

    except Exception as e:
        # Capture de toute exception Slack en BEST-EFFORT (ne bloque pas l'email)
        error_detail = f"{type(e).__name__}: {str(e)}"

        _log_notification(
            nrepo,
            validated,
            provider="slack",
            recipient=validated.channel or _fallback_channel(),
            status="failed",
            error_message=error_detail,
        )

        logger.error(
            "Slack notification exception (best-effort, email will still be attempted)",
            extra={
                "error_type": type(e).__name__,
                "error_message": str(e),
                "channel": validated.channel,
                "webhook_configured": bool(webhook),
            },
            exc_info=True,
        )

        # ⚠️ Ne jamais raise ici → email sera toujours tenté.
        return False


def _send_email(
    to_email: str,
    validated: NotificationPayload,
) -> bool:
    """
    Envoie l'email.

    Design :
    - Utilise une session DB DÉDIÉE pour les logs (succès ou erreur),
      afin de garantir leur persistance même si la tâche part en retry.
    - Laisse remonter les exceptions réseau/SMTP pour que Celery
      gère le retry via autoretry_for.
    - Loggue et retourne False en cas d'erreur non-retriable.
    """
    try:
        subject = f"[{validated.severity.upper()}] {validated.title}"
        body = f"{validated.text}\n\nEnvoyé depuis Monitoring System"

        success = EmailProvider().send(to=to_email, subject=subject, body=body)

        # Session dédiée pour logger le résultat (succès/échec "soft")
        with open_session() as s:
            repo = NotificationRepository(s)
            _log_notification(
                repo,
                validated,
                provider="email",
                recipient=to_email,
                status="success" if success else "failed",
                error_message=None if success else "email_send_returned_false",
                set_sent_at=success,
            )

        if success:
            logger.info(
                "Email notification sent",
                extra={"recipient": to_email, "subject": subject},
            )
        else:
            logger.warning(
                "Email notification failed (send returned False)",
                extra={"recipient": to_email},
            )

        return success

    except (SMTPException, ConnectionError, TimeoutError) as e:
        # Erreurs réseau / SMTP → on log et on laisse l'exception remonter
        # pour que Celery gère le retry via autoretry_for.
        with open_session() as s:
            repo = NotificationRepository(s)
            _log_notification(
                repo,
                validated,
                provider="email",
                recipient=to_email,
                status="failed",
                error_message=f"{type(e).__name__}: {str(e)}",
            )

        logger.error(
            "Email notification error (will be retried by Celery)",
            extra={
                "error_type": type(e).__name__,
                "error": str(e),
                "recipient": to_email,
            },
            exc_info=True,
        )

        # Laisser remonter pour que Celery autoretry_for fasse son job
        raise

    except Exception as e:
        # Autres erreurs → log mais pas de retry
        with open_session() as s:
            repo = NotificationRepository(s)
            _log_notification(
                repo,
                validated,
                provider="email",
                recipient=to_email,
                status="failed",
                error_message=f"{type(e).__name__}: {str(e)}",
            )

        logger.error(
            "Email notification unexpected error (no retry)",
            extra={
                "error_type": type(e).__name__,
                "error": str(e),
                "recipient": to_email,
            },
            exc_info=True,
        )

        return False


# ---------------------------------------------------------------------------
# Grace period (confirmation persistance) — source de vérité unique (secondes)
#   - Priorité :
#       1) client_settings.grace_period_seconds (>=0)
#       2) fallback dur = 0 (pas de grâce)
# ---------------------------------------------------------------------------
def get_grace_seconds(client_id: str | uuid.UUID | None) -> int:
    """
    Retourne la période de grâce (en secondes) pour confirmer qu'une alerte est persistante
    avant d'envoyer la première notification (notify_alert).

    Source de vérité : ClientSettingsRepository.get_effective_grace_period_seconds().
    """
    if not client_id:
        return 0

    try:
        cid = client_id if isinstance(client_id, uuid.UUID) else uuid.UUID(str(client_id))
    except Exception:
        logger.warning("get_grace_seconds: bad client_id %r → fallback 0", client_id)
        return 0

    try:
        with open_session() as s:
            repo = ClientSettingsRepository(s)
            seconds = repo.get_effective_grace_period_seconds(cid)
            return max(0, int(seconds or 0))
    except Exception:
        logger.warning("get_grace_seconds: DB error → fallback 0", exc_info=True)
        return 0


# ---------------------------------------------------------------------------
# Tâche principale : notify
# ---------------------------------------------------------------------------

@celery.task(
    name="tasks.notify",
    bind=True,
    # Retry automatique uniquement sur erreurs email réseau/SMTP.
    autoretry_for=(SMTPException, ConnectionError, TimeoutError),
    retry_backoff=30,
    retry_kwargs={"max_retries": 3},
    acks_late=True,
    queue="notify",
)
def notify(self, payload: Dict[str, Any]) -> bool:
    """
    Tâche d'envoi de notification (Slack + Email) pilotée par les settings du client.

    Politique générale :

    - Validation du payload :
        * En cas d'erreur Pydantic → log 'failed(payload_validation_error)' dans une
          session dédiée, pas de retry, retour False.

    - Cooldown :
        * Cooldown global basé sur client_settings.reminder_notification_seconds
          (fallback ENV ALERT_REMINDER_MINUTES).
        * Si un rappel a déjà été envoyé récemment, on log un seul
          "skipped_cooldown" et on s'arrête sans tenter Slack ni email.

    - Slack : BEST-EFFORT
        * Toute erreur (HTTP non-2xx, exception provider) → log détaillé
          (status=failed) avec type d'erreur, code HTTP, body…
        * AUCUNE exception levée vers la tâche (email toujours tenté).
        * AUCUN retry Celery sur Slack.

    - Email : RÉSILIENT
        * Pas d'adresse → "skipped_no_recipient" (session dédiée).
        * Erreurs SMTP / réseau → log dans une session dédiée + retry Celery
          via autoretry_for.
        * Autres erreurs → log dans une session dédiée, pas de retry.

    - Sessions :
        * Session principale : cooldown + lookup settings client + logs Slack.
        * Sessions dédiées : validation invalide, logs email, skip email, etc.
          pour garantir la persistance même en cas de retry.

    Retour :
        True si au moins un canal (Slack ou email) a réussi, False sinon.
    """

    # 1) Validation payload (session dédiée dans _validate_payload)
    validated = _validate_payload(payload)
    if not validated:
        return False

    to_email: str | None = None

    # 2) Session principale : cooldown + settings client + Slack
    with open_session() as s:
        nrepo = NotificationRepository(s)

        # 2a) Cooldown global (log dans session principale, pas de retry)
        if _check_cooldown(nrepo, validated, payload):
            # Commit explicite du log cooldown avant de sortir
            s.commit()
            return False

        _ensure_incident_prefix(session=s, validated=validated)

        # 2b) Récupération des settings client (webhook Slack + email)
        try:
            csrepo = ClientSettingsRepository(s)
            webhook = csrepo.get_effective_slack_webhook(validated.client_id)
            to_email = csrepo.get_effective_notification_email(validated.client_id)
            # ✅ Récupère le channel configuré (si présent)
            try:
                cs = csrepo.get_by_client_id(validated.client_id)
                slack_channel_name = (getattr(cs, "slack_channel_name", None) or "").strip()
            except Exception:
                slack_channel_name = ""
        except Exception as e:
            logger.warning(
                "ClientSettings lookup failed",
                extra={"client_id": str(validated.client_id), "error": str(e)},
                exc_info=True,
            )
            webhook = None
            to_email = None
            slack_channel_name = ""

        # ✅ Appliquer le channel client si:
        # - un webhook Slack est configuré
        # - et le payload n'a pas explicitement fourni "channel"
        # (on respecte un override explicite dans le payload)
        if webhook:
            payload_channel_raw = (payload.get("channel") or "").strip()
            if not payload_channel_raw and slack_channel_name:
                if not slack_channel_name.startswith("#"):
                    slack_channel_name = f"#{slack_channel_name}"
                validated.channel = slack_channel_name

        # Court-circuit si aucun canal configuré
        if not webhook and not to_email:
            _log_notification(
                nrepo,
                validated,
                provider="system",
                recipient="",
                status="skipped_no_channels",
            )
            s.commit()
            logger.warning(
                "No notification channels configured",
                extra={"client_id": str(validated.client_id)},
            )
            return False

        # 2c) Slack : best-effort, logs dans la session principale
        slack_sent = False
        if webhook:
            slack_sent = _send_slack_safe(nrepo, webhook, validated)
        else:
            _log_notification(
                nrepo,
                validated,
                provider="slack",
                recipient=validated.channel or _fallback_channel(),
                status="skipped_no_webhook",
            )
            logger.info(
                "Slack notification skipped: no webhook configured",
                extra={"client_id": str(validated.client_id)},
            )

        # On commit la session principale après Slack, avant l'email.
        # Ainsi les logs Slack (et cooldown, settings, etc.) sont persistés
        # même si l'email part ensuite en retry.
        s.commit()

    # 3) Email : en dehors de la session principale, via sessions dédiées
    email_sent = False
    if to_email:
        # Peut lever SMTPException / ConnectionError / TimeoutError → autoretry_for
        email_sent = _send_email(to_email, validated)
    else:
        # Pas de destinataire → log dans une session dédiée
        with open_session() as s:
            repo = NotificationRepository(s)
            _log_notification(
                repo,
                validated,
                provider="email",
                recipient="",
                status="skipped_no_recipient",
            )
        logger.info(
            "Email notification skipped: no email configured",
            extra={"client_id": str(validated.client_id)},
        )

    # 4) Verdict global
    success = bool(slack_sent or email_sent)

    logger.info(
        "Notification task completed",
        extra={
            "client_id": str(validated.client_id),
            "slack_sent": slack_sent,
            "email_sent": email_sent,
            "overall_success": success,
        },
    )

    return success


# ---------------------------------------------------------------------------
# Tâche de haut niveau : notify_alert (AVEC grace period)
# ---------------------------------------------------------------------------

@celery.task(
    name="notify_alert",
    bind=True,
    retry_backoff=30,
    retry_kwargs={"max_retries": 3},
    acks_late=True,
    queue="notify",
)
def notify_alert(self, alert_id: str, *, remind_after_minutes: int | None = None) -> None:
    """
    Notifie une alerte de seuil (Alert) avec 2 garde-fous :

      1) ✅ GRACE (confirmation persistance)
         - avant la 1ère notification uniquement
         - basé sur client_settings.grace_period_seconds
         - référentiel de temps = Alert.triggered_at (UTC)
         - tant que age < grace => on replanifie notify_alert (countdown)

      2) Cooldown (reminder)
         - basé sur client_settings.reminder_notification_seconds
         - appliqué par alert_id via notification_log (provider slack success)

    Puis délègue l'envoi réel à tasks.notify(payload) (Slack + Email).
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
    from app.infrastructure.persistence.database.models.notification_log import NotificationLog
    from app.infrastructure.persistence.database.models.alert import Alert
    from app.infrastructure.persistence.database.models.machine import Machine
    from app.infrastructure.persistence.database.models.metric_instance import MetricInstance
    from app.workers.tasks.notification_tasks import notify as notify_task

    def _override_to_seconds(override_minutes: int | None) -> int | None:
        if isinstance(override_minutes, int) and override_minutes > 0:
            return override_minutes * 60
        return None

    # micro-jitter pour éviter de réveiller toutes les alertes pile à la même seconde
    def _jitter_seconds(max_jitter: int = 5) -> int:
        try:
            return int(alert_uuid.int % (max_jitter + 1))
        except Exception:
            return 0

    try:
        with open_session() as session:
            alert = session.get(Alert, alert_uuid)
            if not alert:
                logger.warning("Alerte %s non trouvée", alert_uuid)
                return

            # Ne notifier que les alertes en cours
            if (alert.status or "").upper() != "FIRING":
                logger.info("Alerte %s ignorée (status=%s)", alert_id, alert.status)
                return

            # Machine / metric instance (pour enrichir le message)
            machine = session.get(Machine, alert.machine_id) if alert.machine_id else None
            metric_instance = session.get(MetricInstance, alert.metric_instance_id) if alert.metric_instance_id else None

            # Déduire client_id
            raw_client_id = getattr(alert, "client_id", None) or getattr(machine, "client_id", None)
            if not isinstance(raw_client_id, uuid.UUID):
                raw_client_id = uuid.UUID(int=0)
            client_id = raw_client_id

            now_utc = dt.datetime.now(dt.timezone.utc)

            # ------------------------------------------------------------------
            # 0) ✅ GRACE PERIOD (première notif uniquement)
            # ------------------------------------------------------------------
            grace_seconds = get_grace_seconds(client_id)

            # Si triggered_at absent => on n'applique pas la grâce (fallback safe)
            started_at = getattr(alert, "triggered_at", None)
            if started_at is not None:
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=dt.timezone.utc)
                else:
                    started_at = started_at.astimezone(dt.timezone.utc)

            if grace_seconds > 0 and started_at is not None:
                # a) vérifier si on a déjà un "success" pour cette alerte (première notif déjà envoyée)
                first_success_ts = session.scalar(
                    select(NotificationLog.sent_at)
                    .where(
                        NotificationLog.alert_id == alert.id,
                        NotificationLog.status == "success",
                        NotificationLog.provider == "slack",
                    )
                    .order_by(NotificationLog.sent_at.asc())
                    .limit(1)
                )

                # b) si aucune notif encore partie => appliquer la grâce
                if first_success_ts is None:
                    age_sec = (now_utc - started_at).total_seconds()
                    if age_sec < grace_seconds:
                        remaining = int(grace_seconds - age_sec)
                        countdown = max(1, remaining + _jitter_seconds(5))

                        # log technique (audit) — réutilise NotificationRepository existant
                        try:
                            nrepo = NotificationRepository(session)
                            nrepo.add_log(
                                client_id=client_id,
                                provider="grace",
                                recipient="",
                                status="scheduled_grace",
                                message=(
                                    f"Grace active: requeue notify_alert in {countdown}s "
                                    f"(age={int(age_sec)}s < grace={grace_seconds}s)"
                                ),
                                incident_id=None,
                                alert_id=alert.id,
                                set_sent_at=False,
                            )
                            session.commit()
                        except Exception:
                            session.rollback()
                            logger.debug("notify_alert: failed to log grace marker", exc_info=True)

                        logger.info(
                            "notify_alert: grace active → reschedule",
                            extra={
                                "alert_id": str(alert.id),
                                "client_id": str(client_id),
                                "age_sec": int(age_sec),
                                "grace_sec": int(grace_seconds),
                                "countdown_sec": int(countdown),
                            },
                        )

                        notify_alert.apply_async(
                            args=[str(alert.id)],
                            kwargs={"remind_after_minutes": remind_after_minutes},
                            countdown=countdown,
                            queue="notify",
                        )
                        return

            # ------------------------------------------------------------------
            # 1) Cooldown / reminder (par alert_id)
            # ------------------------------------------------------------------
            override_seconds = _override_to_seconds(remind_after_minutes)
            remind_seconds = override_seconds if override_seconds is not None else get_remind_seconds(client_id)
            cooldown = dt.timedelta(seconds=remind_seconds)

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

            if last_success_ts and (now_utc - last_success_ts) < cooldown:
                logger.info(
                    "Notification skip (cooldown actif)",
                    extra={
                        "alert_id": str(alert.id),
                        "elapsed_seconds": int((now_utc - last_success_ts).total_seconds()),
                        "cooldown_seconds": int(remind_seconds),
                    },
                )
                return

            # ------------------------------------------------------------------
            # 2) Construire payload -> déléguer à notify(payload)
            # ------------------------------------------------------------------
            metric_name = getattr(metric_instance, "name_effective", "unknown_metric")

            # Lien alert -> incident (pour préfixer "(#xxx)" dans notify())
            incident_id_for_prefix: uuid.UUID | None = None
            try:
                if client_id and alert.machine_id and alert.metric_instance_id:
                    inc = session.scalar(
                        select(Incident)
                        .where(
                            Incident.client_id == client_id,
                            Incident.status == "OPEN",
                            Incident.incident_type == IncidentType.BREACH,
                            Incident.machine_id == alert.machine_id,
                            Incident.metric_instance_id == alert.metric_instance_id,
                        )
                        .order_by(Incident.created_at.desc())
                        .limit(1)
                    )
                    if inc is not None:
                        incident_id_for_prefix = inc.id
            except Exception:
                logger.exception(
                    "notify_alert: failed to lookup BREACH incident for prefix",
                    extra={
                        "client_id": str(client_id),
                        "machine_id": str(getattr(alert, "machine_id", None)),
                        "metric_instance_id": str(getattr(alert, "metric_instance_id", None)),
                    },
                )

            base_msg = alert.message or f"Threshold breach on {metric_name}"
            text = f"{base_msg} - Valeur: {alert.current_value}"

            sev_raw = (alert.severity or "warning").lower()
            ui_status = "error" if sev_raw == "critical" else "ok"

            payload = {
                "title": f"🚨 Alerte {sev_raw.upper()} : {metric_name}",
                "text": text,
                "severity": sev_raw,
                "status": ui_status,
                "client_id": client_id,
                "alert_id": alert.id,
                "incident_id": incident_id_for_prefix,
            }

            notify_task.apply_async(kwargs={"payload": payload}, queue="notify")

            logger.info(
                "Notification enqueued",
                extra={
                    "alert_id": str(alert.id),
                    "remind_seconds": int(remind_seconds),
                    "used_override": override_seconds is not None,
                    "grace_seconds": int(grace_seconds),
                    "incident_id": str(incident_id_for_prefix) if incident_id_for_prefix else None,
                },
            )

    except Exception as e:
        logger.error("Erreur notification alerte %s: %s", alert_id, e, exc_info=True)
        raise self.retry(exc=e)


# ---------------------------------------------------------------------------
# Reminders NON groupés (1 notif par incident OPEN)
# Cooldown = par incident (via incident_id dans notification_log)
# ---------------------------------------------------------------------------

@celery.task(name="tasks.notify_incident_reminders_for_client", queue="notify")
def notify_incident_reminders_for_client(client_id: str) -> int:
    """
    Envoie un rappel pour CHAQUE incident OPEN d'un client.
    - Pas de grouping
    - Pas de skip_cooldown
    - Cooldown par incident car on passe incident_id dans le payload
    """
    from app.infrastructure.persistence.repositories.incident_repository import IncidentRepository

    try:
        cid = uuid.UUID(str(client_id))
    except Exception:
        logger.warning("notify_incident_reminders_for_client: invalid client_id=%r", client_id)
        return 0

    n = 0
    with open_session() as s:
        irepo = IncidentRepository(s)
        incs = irepo.list_open_incidents(cid)

    if not incs:
        return 0

    for inc in incs:
        payload = {
            "title": f"🔁 Rappel : {inc.title}",
            "text": (
                "🚨 Incident toujours ouvert\n"
                f"- {inc.title}\n"
                f"- Type: {getattr(inc, 'incident_type', '')}\n"
                f"- Sévérité: {getattr(inc, 'severity', 'warning')}\n"
            ),
            "severity": (getattr(inc, "severity", None) or "warning"),
            "client_id": cid,
            # ✅ clé critique : cooldown par incident
            "incident_id": str(inc.id),
            # pas d'alert_id
            # pas de skip_cooldown
            # pas de resolved=True
        }
        notify.apply_async(kwargs={"payload": payload}, queue="notify")
        n += 1

    logger.info(
        "notify_incident_reminders_for_client: enqueued %d incident reminders",
        n,
        extra={"client_id": str(cid)},
    )
    return n


@celery.task(name="tasks.incident_reminders", queue="notify")
def incident_reminders() -> int:
    """
    Runner périodique: déclenche notify_incident_reminders_for_client()
    pour tous les clients qui ont AU MOINS 1 incident OPEN.
    """
    from sqlalchemy import select, distinct
    from app.infrastructure.persistence.database.models.incident import Incident

    client_ids: list[uuid.UUID] = []
    with open_session() as s:
        client_ids = list(
            s.scalars(
                select(distinct(Incident.client_id))
                .where(Incident.status == "OPEN")
            )
        )

    n = 0
    for cid in client_ids:
        notify_incident_reminders_for_client.delay(str(cid))
        n += 1

    logger.info("incident_reminders: triggered for %d client(s)", n)
    return n


# ---------------------------------------------------------------------------
# Rappel groupé d'incidents ouverts
# ---------------------------------------------------------------------------

@celery.task(name="tasks.notify_grouped_reminder", queue="notify")
def notify_grouped_reminder(client_id: str):
    """
    Rappelle de manière groupée tous les incidents ouverts pour un client,
    si le regroupement d'alertes est activé dans les settings du client.
    """
    from app.infrastructure.persistence.repositories.incident_repository import IncidentRepository
    from app.infrastructure.persistence.database.session import open_session    
    from app.workers.tasks.notification_tasks import notify as notify_task
    from app.infrastructure.persistence.repositories.client_settings_repository import ClientSettingsRepository

    # ✅ Coerce client_id en UUID (repo attend UUID)
    try:
        cid = uuid.UUID(str(client_id))
    except Exception:
        logger.warning("notify_grouped_reminder: invalid client_id=%r", client_id)
        return

    with open_session() as s:
        repo = IncidentRepository(s)
        csrepo = ClientSettingsRepository(s)
        client_settings = csrepo.get_by_client_id(cid)

        # Si pas de settings, on ne fait rien (sécurité)
        if not client_settings:
            logger.debug("notify_grouped_reminder: no client_settings for client_id=%s", cid)
            return

        if not bool(getattr(client_settings, "alert_grouping_enabled", False)):
             return

        # Récupérer TOUS les incidents ouverts (pas de filtre temporel)
        incs = repo.list_open_incidents(cid)

        if not incs:
            return

        # Ajoute le prefix (#xxx) en UI (sans modifier DB)
        def _display_title(i: Incident) -> str:
            base = (getattr(i, "title", "") or "").lstrip()
            prefix = _fmt_incident_prefix(getattr(i, "incident_number", None))
            # si le title est déjà préfixé (rare), on ne redouble pas
            if _INC_PREFIX_RE.match(base):
                return base
            return (prefix + base) if prefix else base

        text = "🚨 Rappel: incidents toujours ouverts\n" + "\n".join(
            f"- {_display_title(i)}" for i in incs
        )
        payload = {
            "title": "🔁 Rappel d'incidents ouverts",
            "text": text,
            "severity": "warning",
            "client_id": cid,
        }
        notify_task.apply_async(kwargs={"payload": payload}, queue="notify")


@celery.task(name="tasks.grouped_reminders", queue="notify")
def grouped_reminders() -> int:
    """
    Runner périodique (sans args) pour déclencher les rappels groupés
    sur tous les clients éligibles.
    """
    from app.infrastructure.persistence.database.session import open_session
    from app.infrastructure.persistence.database.models.client_settings import ClientSettings

    n = 0
    with open_session() as session:
        client_ids = (
            session.query(ClientSettings.client_id)
            .filter(ClientSettings.alert_grouping_enabled.is_(True))
            .all()
        )
    for (client_id,) in client_ids:
        notify_grouped_reminder.delay(str(client_id))
        n += 1
    return n


# ---------------------------------------------------------------------------
# Tâche de test
# ---------------------------------------------------------------------------

@celery.task(name="tasks.test_notification", queue="notify")
def test_notification(client_id: str | None = None):
    """
    Tâche de test pour vérifier la config des notifications.

    - Enfile une notification d'info vers les canaux configurés
      pour le client donné.
    - Si aucun client_id n'est fourni, utilise le client "zéro" (legacy).
    """
    logger.info("Starting test notification task")

    # Si aucun client_id n'est passé → fallback zero-UUID (comportement historique)
    if client_id is None:
        test_client_id = uuid.UUID("00000000-0000-0000-0000-000000000000")
    else:
        test_client_id = uuid.UUID(str(client_id))

    # On vérifie via ClientSettingsRepository (aligné avec notify)
    with open_session() as s:
        csrepo = ClientSettingsRepository(s)
        webhook = csrepo.get_effective_slack_webhook(test_client_id)
        to_email = csrepo.get_effective_notification_email(test_client_id)

    if not webhook and not to_email:
        error_msg = (
            f"No notification channels configured for client {test_client_id}"
        )
        logger.error(error_msg)
        return {"status": "error", "message": error_msg}

    test_payload = {
        "title": "Test Notification",
        "text": "Ceci est un test de notification depuis le système de monitoring",
        "severity": "info",
        "client_id": test_client_id,
        "incident_id": None,
        "alert_id": None,
    }

    logger.info(
        "Test payload prepared",
        extra={"payload": {**test_payload, "text": "[omitted]"}},
    )

    try:
        # IMPORTANT: on passe par Celery (apply_async) pour respecter la signature bind=True
        res = notify.apply_async(kwargs={"payload": test_payload}, queue="notify")
        logger.info("Test notification enqueued", extra={"task_id": res.id})
        return {"status": "enqueued", "task_id": res.id}
    except Exception as e:
        logger.error("Test notification failed", extra={"error": str(e)}, exc_info=True)
        return {"status": "error", "message": str(e)}

