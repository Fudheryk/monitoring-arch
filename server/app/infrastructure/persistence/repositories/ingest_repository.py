# server/app/infrastructure/persistence/repositories/ingest_repository.py
from __future__ import annotations
"""
Insert idempotent d’un ingest_event (compat SQLite/Postgres).
- Coerce client_id / machine_id vers uuid.UUID si fournis en str
- Upsert "pauvre" : on vérifie l’existence avant insert
"""
import uuid as _uuid
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, insert
from sqlalchemy.orm import Session

from app.infrastructure.persistence.database.models.ingest_event import IngestEvent


def _as_uuid(v):
    if isinstance(v, UUID):
        return v
    if isinstance(v, str):
        try:
            return UUID(v)
        except Exception:
            return v
    return v


class IngestRepository:
    def __init__(self, session: Session):
        self.s = session

    def create_if_absent(
        self,
        *,
        client_id,
        machine_id,
        ingest_id: str,
        sent_at: datetime,
    ) -> bool:
        # Déjà présent pour ce client et cette clé idempotente ?
        exists = self.s.execute(
            select(IngestEvent.id).where(
                IngestEvent.client_id == _as_uuid(client_id),
                IngestEvent.ingest_id == ingest_id,
            )
        ).first()
        if exists:
            return False

        # Insert
        self.s.execute(
            insert(IngestEvent).values(
                id=_uuid.uuid4(),
                client_id=_as_uuid(client_id),
                machine_id=_as_uuid(machine_id),
                ingest_id=ingest_id,
                sent_at=sent_at,
            )
        )
        self.s.commit()
        return True
