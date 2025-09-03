from __future__ import annotations
"""server/app/infrastructure/persistence/repositories/http_target_repository.py
~~~~~~~~~~~~~~~~~~~~~~~~
Repo http targets.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.infrastructure.persistence.database.models.http_target import HttpTarget

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.database.models.http_target import HttpTarget

class HttpTargetRepository:
    def __init__(self, session: Session):
        self.s = session

    def active_for_client(self, client_id):
        return self.s.scalars(select(HttpTarget).where(HttpTarget.client_id == client_id, HttpTarget.is_active == True)).all()

    def all_active(self):
        return self.s.scalars(select(HttpTarget).where(HttpTarget.is_active == True)).all()
