from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.persistence.database.base import Base


class OutboxStatus(str, enum.Enum):
    PENDING = "PENDING"
    DELIVERING = "DELIVERING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class JSONPortable(sa.types.TypeDecorator):
    """JSONB sur Postgres, JSON ailleurs."""
    impl = sa.JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import JSONB
            return dialect.type_descriptor(JSONB(astext_type=sa.Text()))
        return dialect.type_descriptor(sa.JSON())


class UUIDPortable(sa.types.TypeDecorator):
    """UUID natif sur Postgres, VARCHAR(36) ailleurs."""
    impl = sa.String(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import UUID as PGUUID
            return dialect.type_descriptor(PGUUID(as_uuid=True))
        return dialect.type_descriptor(sa.String(36))


class TstzPortable(sa.types.TypeDecorator):
    """TIMESTAMPTZ sur Postgres, DateTime() ailleurs."""
    impl = sa.DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(sa.TIMESTAMP(timezone=True))
        return dialect.type_descriptor(sa.DateTime())


def StatusEnum():
    """
    - Sur Postgres: réutilise le type DB 'outbox_status' (créé par migration),
      sans tenter de le (re)créer => create_type=False
    - Ailleurs: Enum string portable (CHECK constraint)
    """
    # native_enum=True est ignoré si le dialect ne le supporte pas (ex: SQLite)
    return sa.Enum(OutboxStatus, name="outbox_status", native_enum=True, create_type=False)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(UUIDPortable(), primary_key=True, default=uuid.uuid4)

    client_id: Mapped[uuid.UUID] = mapped_column(
        UUIDPortable(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDPortable(), sa.ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True
    )

    type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONPortable(), nullable=False)

    status: Mapped[OutboxStatus] = mapped_column(
        StatusEnum(), nullable=False, default=OutboxStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(TstzPortable(), nullable=False, default=lambda: datetime.now(timezone.utc))

    delivery_receipt: Mapped[dict | None] = mapped_column(JSONPortable(), nullable=True)
    last_error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TstzPortable(), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        TstzPortable(), nullable=False, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    client = relationship("Client", lazy="selectin")
    incident = relationship("Incident", lazy="selectin")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OutboxEvent id={self.id} type={self.type} status={self.status} attempts={self.attempts}>"
