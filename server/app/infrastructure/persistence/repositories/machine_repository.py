from __future__ import annotations

"""server/app/infrastructure/persistence/repositories/machine_repository.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Repository pour les entités Machine.
- Le repo **reçoit** une Session SQLAlchemy fournie par l'appelant
  (endpoint via Depends(get_db) ou tâche/service via get_sync_session()).
- Il ne crée ni ne ferme la session : responsabilité de l'appelant.
"""

from typing import Iterator, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.database.models.machine import Machine


class MachineRepository:
    """Accès de haut niveau aux Machines."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def by_client_and_hostname(self, client_id: UUID, hostname: str) -> Optional[Machine]:
        """Retourne la machine d'un client pour un hostname donné, ou None."""
        stmt = select(Machine).where(
            Machine.client_id == client_id,
            Machine.hostname == hostname,
        )
        return self.db.scalar(stmt)

    def create(
        self,
        client_id: UUID,
        hostname: str,
        os_type: Optional[str] = None,
        os_version: Optional[str] = None,
    ) -> Machine:
        """
        Crée une Machine **sans commit** (l'appelant décide du commit/rollback).
        Utilise `flush()` pour matérialiser l'ID si besoin directement après l'appel.
        """
        m = Machine(
            client_id=client_id,
            hostname=hostname,
            os_type=os_type,
            os_version=os_version,
        )
        self.db.add(m)
        self.db.flush()  # assure que m.id est disponible
        return m

    def iter_all(self) -> Iterator[Machine]:
        """Itère sur toutes les machines (streaming par défaut côté SQLAlchemy)."""
        stmt = select(Machine)
        for m in self.db.scalars(stmt):
            yield m
