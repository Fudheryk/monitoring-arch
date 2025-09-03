from __future__ import annotations
"""server/app/infrastructure/persistence/repositories/api_key_repository.py
~~~~~~~~~~~~~~~~~~~~~~~~
Repo ApiKey.get_by_key.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.infrastructure.persistence.database.models.api_key import ApiKey

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.database.models.api_key import ApiKey

class ApiKeyRepository:
    def __init__(self, session: Session):
        self.s = session

    def get_by_key(self, key: str) -> ApiKey | None:
        return self.s.scalar(select(ApiKey).where(ApiKey.key == key, ApiKey.is_active == True))
