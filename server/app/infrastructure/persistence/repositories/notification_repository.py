# server/app/infrastructure/persistence/repositories/notification_repository.py

from __future__ import annotations

from typing import Optional
from uuid import UUID
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy import select, func

from celery.utils.log import get_task_logger
from app.infrastructure.persistence.database.models.notification_log import NotificationLog
from app.application.services.notification_service import TECH_NOTIFICATION_PROVIDERS

logger = get_task_logger(__name__)


class NotificationRepository:
    """
    Repository pour la table notification_log.

    Principes :
    - Ne gère PAS les commit/rollback : c'est à la charge de l'appelant.
    - add_log(...) tronque les messages / erreurs pour éviter les blobs énormes.
    - get_last_sent_at_any(...) sert de source pour les cooldowns / due-reminders.

    ✅ Best practice :
    - get_last_sent_at_any(...) doit ignorer les providers techniques
      (grace, group_open, cooldown) pour ne pas polluer la "source de vérité"
      de "dernière notification réellement envoyée".
    """

    # Providers techniques (audit / marqueurs) à exclure des calculs de cooldown
    TECH_PROVIDERS = ("grace", "group_open", "cooldown")

    def __init__(self, db: Session):
        self.db = db

    def add_log(
        self,
        *,
        client_id: UUID,
        provider: str,
        recipient: str,
        status: str,
        message: Optional[str],
        incident_id: Optional[UUID] = None,
        alert_id: Optional[UUID] = None,
        error_message: Optional[str] = None,
        set_sent_at: bool = False,
    ) -> NotificationLog:
        """
        Crée une entrée dans notification_log.

        Note :
          - sent_at est uniquement renseigné si set_sent_at=True (succès réel).
          - Les logs "skipped_*" / "failed" n'impactent pas les cooldowns car
            get_last_sent_at_any filtre déjà status='success' + sent_at non NULL.
        """
        from datetime import datetime, timezone

        row = NotificationLog(
            client_id=client_id,
            provider=provider,
            recipient=recipient,
            status=status,
            message=(message[:10000] if message else None),
            error_message=(error_message[:10000] if error_message else None),
            incident_id=incident_id,
            alert_id=alert_id,
            sent_at=(datetime.now(timezone.utc) if set_sent_at else None),
        )
        self.db.add(row)
        return row

    def get_last_sent_at_any(
            self,
            client_id: UUID,
            incident_id: Optional[UUID] = None,
        ) -> Optional[datetime]:
            """
            Retourne le dernier `sent_at` (tous providers "réels" confondus) pour ce client,
            éventuellement filtré par incident_id.

            ✅ Sert de source de vérité pour les cooldowns et "due reminders".

            Règles :
            - uniquement status='success'
            - uniquement sent_at non NULL
            - exclut les providers techniques :
                grace / group_open / cooldown
                (via TECH_NOTIFICATION_PROVIDERS partagée)

            Args:
                client_id: client concerné
                incident_id: si fourni, restreint à cet incident précis

            Returns:
                datetime UTC ou None.
            """
            stmt = (
                select(func.max(NotificationLog.sent_at))
                .where(
                    NotificationLog.client_id == client_id,
                    NotificationLog.status == "success",
                    NotificationLog.sent_at.is_not(None),
                    NotificationLog.provider.notin_(TECH_NOTIFICATION_PROVIDERS),
                )
            )

            if incident_id is not None:
                stmt = stmt.where(NotificationLog.incident_id == incident_id)

            return self.db.execute(stmt).scalar_one_or_none()
