from __future__ import annotations
"""server/app/infrastructure/persistence/database/models/machine.py
~~~~~~~~~~~~~~~~~~~~~~~~
Table machines.
"""
from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.infrastructure.persistence.database.base import Base
import uuid
import datetime as dt




class Machine(Base):
    __tablename__ = "machines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"))
    hostname: Mapped[str] = mapped_column(String(255), index=True)
    os_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    os_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    last_seen: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    registered_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc))
    unregistered_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, index=True)