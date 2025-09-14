from __future__ import annotations
"""server/app/infrastructure/persistence/repositories/http_target_repository.py
~~~~~~~~~~~~~~~~~~~~~~~~
Repo http targets.
"""
from typing import List
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.database.models.http_target import HttpTarget


class HttpTargetRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def active_for_client(self, client_id: UUID) -> List[HttpTarget]:
        stmt = (
            select(HttpTarget)
            .where(
                HttpTarget.client_id == client_id,
                HttpTarget.is_active.is_(True),
            )
            .order_by(HttpTarget.name)
        )
        return self.db.scalars(stmt).all()

    def all_active(self) -> List[HttpTarget]:
        stmt = (
            select(HttpTarget)
            .where(HttpTarget.is_active.is_(True))
            .order_by(HttpTarget.name)
        )
        return self.db.scalars(stmt).all()


