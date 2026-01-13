from __future__ import annotations
"""server/app/infrastructure/persistence/database/models/incident.py
~~~~~~~~~~~~~~~~~~~~~~~~
Table incidents.
"""
from sqlalchemy import DateTime, Integer, String, Text, ForeignKey, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.infrastructure.persistence.database.base import Base
from app.infrastructure.persistence.database.models.http_target import HttpTarget

import uuid
import enum
import datetime as dt




class IncidentType(str, enum.Enum):
    NO_DATA_MACHINE = "NO_DATA_MACHINE"
    NO_DATA_METRIC = "NO_DATA_METRIC"
    BREACH = "BREACH"
    HTTP_FAILURE = "HTTP_FAILURE"


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    incident_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    incident_type: Mapped[IncidentType] = mapped_column(
        SAEnum(IncidentType, name="incident_type", native_enum=True, create_constraint=False),
        nullable=False,
    )
    dedup_key: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="OPEN")
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    machine_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("machines.id"), nullable=True)
    metric_instance_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("metric_instances.id"), nullable=True)
    http_target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("http_targets.id"), nullable=True)
    http_target: Mapped[HttpTarget | None] = relationship("HttpTarget", backref="incidents")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc))
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc))