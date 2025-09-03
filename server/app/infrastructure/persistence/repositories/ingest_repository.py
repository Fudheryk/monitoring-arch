from __future__ import annotations
"""server/app/infrastructure/persistence/repositories/ingest_repository.py
~~~~~~~~~~~~~~~~~~~~~~~~
Repo idempotence (ingest_events).
"""
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.infrastructure.persistence.database.models.ingest_event import IngestEvent
import uuid

import uuid
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.infrastructure.persistence.database.models.ingest_event import IngestEvent

class IngestRepository:
    def __init__(self, session: Session):
        self.s = session

    def create_if_absent(self, *, client_id, machine_id, ingest_id, sent_at) -> bool:
        try:
            self.s.execute(
                insert(IngestEvent).values(id=uuid.uuid4(), client_id=client_id, machine_id=machine_id, ingest_id=ingest_id, sent_at=sent_at)
            )
            self.s.commit()
            return True
        except IntegrityError:
            self.s.rollback()
            return False
