from __future__ import annotations
"""server/app/infrastructure/persistence/database/models/client_settings.py
~~~~~~~~~~~~~~~~~~~~~~~~
Table client_settings.
"""
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.infrastructure.persistence.database.base import Base
import uuid
import datetime as dt




class ClientSettings(Base):
    __tablename__ = "client_settings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        unique=True,
    )

    notification_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    slack_webhook_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    slack_channel_name: Mapped[str | None] = mapped_column(String(80), nullable=True)

    heartbeat_threshold_minutes: Mapped[int] = mapped_column(Integer, default=5)
    consecutive_failures_threshold: Mapped[int] = mapped_column(Integer, default=2)
    alert_grouping_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    alert_grouping_window_seconds: Mapped[int] = mapped_column(Integer, default=300)
    reminder_notification_seconds: Mapped[int] = mapped_column(Integer, default=600)
    grace_period_seconds: Mapped[int] = mapped_column(Integer, default=120)
    notify_on_resolve: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.timezone.utc),
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.timezone.utc),
    )
