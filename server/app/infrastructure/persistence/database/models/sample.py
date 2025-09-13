from __future__ import annotations
"""server/app/infrastructure/persistence/database/models/sample.py
~~~~~~~~~~~~~~~~~~~~~~~~
Table samples (valeurs typées).
"""
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.infrastructure.persistence.database.base import Base
import datetime as dt




class Sample(Base):
    __tablename__ = "samples"

    metric_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("metrics.id", ondelete="CASCADE"), primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True, default=lambda: dt.datetime.now(dt.timezone.utc))
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, default=0)
    value_type: Mapped[str] = mapped_column(String(16))  # bool|numeric|string
    num_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    bool_value: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    str_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc))
