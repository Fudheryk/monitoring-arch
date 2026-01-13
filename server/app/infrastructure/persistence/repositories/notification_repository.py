from __future__ import annotations

from typing import Optional
from uuid import UUID
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from sqlalchemy import select, func

from celery.utils.log import get_task_logger

from app.infrastructure.persistence.database.models.notification_log import NotificationLog


logger = get_task_logger(__name__)


class NotificationRepository:
    """
    Repository pour la table notification_log.

    Principes :
    - Ne gère PAS les commit/rollback : c'est à la charge de l'appelant.
    - add_log(...) tronque les messages / erreurs pour éviter les blobs énormes.
    - get_last_sent_at_any(...) sert de source pour les cooldowns (status='success').
    """

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

        Args:
            client_id: client concerné
            provider: 'slack' | 'email' | 'cooldown' | 'grace' | ...
            recipient: destinataire logique (email, channel, ou tag technique)
            status: 'success' / 'failed' / 'skipped_*' / 'pending'...
            message: payload métiers (titre + texte) – tronqué à 10k chars
            incident_id: incident éventuellement lié
            alert_id: alerte éventuellement liée
            error_message: détail technique (tronqué à 10k chars)
            set_sent_at: si True -> sent_at = now(UTC), sinon NULL

        Returns:
            L'objet NotificationLog (non commit).
        """
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
        Retourne le dernier `sent_at` (tous providers confondus) pour ce client,
        éventuellement filtré par incident_id.

        - Ne considère QUE les lignes avec status='success' et sent_at non NULL.
        - Sert de base au cooldown global des notifications (task `notify`).

        Args:
            client_id: client concerné
            incident_id: si fourni, restreint à cette alerte/incident précis

        Returns:
            datetime UTC ou None.
        """
        stmt = (
            select(func.max(NotificationLog.sent_at))
            .where(
                NotificationLog.client_id == client_id,
                NotificationLog.status == "success",
            )
        )
        if incident_id is not None:
            stmt = stmt.where(NotificationLog.incident_id == incident_id)

        result = self.db.execute(stmt).scalar_one_or_none()
        return result
