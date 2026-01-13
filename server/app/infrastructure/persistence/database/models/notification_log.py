from __future__ import annotations
"""Table notification_log.

Chaque notification envoyée (ou ignorée) est enregistrée ici.
Utilisé pour :
  - audit
  - UI (historique)
  - cooldown anti-spam
  - affichage statut : OK / ERROR / OUVERT / RÉSOLU
"""

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.database.base import Base

import uuid
import datetime as dt


class NotificationLog(Base):
    __tablename__ = "notification_log"

    # Identifiant unique du log
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Client associé à la notification
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))

    # Optionnel : incident ou alerte concernée
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    alert_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # slack / email / cooldown / etc.
    provider: Mapped[str] = mapped_column(String(50))

    # Exemple : email, #webhook-channel, "skipped_no_webhook"
    recipient: Mapped[str] = mapped_column(String(255))

    # Statut technique/logique :
    # - success / failed
    # - skipped_cooldown / skipped_no_webhook / skipped_grace
    # - etc.
    status: Mapped[str] = mapped_column(String(32), default="pending")

    # Message métier (court + utile pour audit)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Erreur technique éventuelle
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamp d’envoi réel — sert au cooldown
    sent_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Date de création du log
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.timezone.utc),
    )
