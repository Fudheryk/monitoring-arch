# server/app/infrastructure/persistence/database/models/threshold_template.py

from __future__ import annotations

import uuid
import datetime as dt

from sqlalchemy import (
    Boolean,
    String,
    Integer,
    Float,
    DateTime,
    ForeignKey,
    CheckConstraint,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.database.base import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class ThresholdTemplate(Base):
    __tablename__ = "threshold_templates"

    __table_args__ = (
        # Un template "name" par metric_definition
        UniqueConstraint(
            "definition_id",
            "name",
            name="uq_threshold_templates_definition_id_name",
        ),
        # Une seule des trois valeurs doit être renseignée
        CheckConstraint(
            """
            (value_num IS NOT NULL AND value_bool IS NULL AND value_str IS NULL)
        OR  (value_num IS NULL AND value_bool IS NOT NULL AND value_str IS NULL)
        OR  (value_num IS NULL AND value_bool IS NULL AND value_str IS NOT NULL)
        """,
            name="ck_threshold_templates_single_value",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("metric_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="default",
        server_default="default",
    )

    condition: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    value_num: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_bool: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    value_str: Mapped[str | None] = mapped_column(String(255), nullable=True)

    severity: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="warning",
        server_default="warning",
    )
    consecutive_breaches: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    cooldown_sec: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    min_duration_sec: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )
