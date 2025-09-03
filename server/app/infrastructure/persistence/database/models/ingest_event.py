from __future__ import annotations
"""server/app/infrastructure/persistence/database/models/ingest_event.py
~~~~~~~~~~~~~~~~~~~~~~~~
Table ingest_events (idempotence).
"""
from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.infrastructure.persistence.database.base import Base
import uuid
import datetime as dt

import uuid
import datetime as dt

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.database.base import Base

class IngestEvent(Base):
    __tablename__ = "ingest_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    machine_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    ingest_id: Mapped[str] = mapped_column(String(64))
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc))
