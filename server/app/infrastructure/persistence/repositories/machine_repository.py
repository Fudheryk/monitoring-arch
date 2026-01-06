from __future__ import annotations
"""
server/app/infrastructure/persistence/repositories/machine_repository.py
~~~~~~~~~~~~~~~~~~~~~~~~
Repository pour les entités Machine.
- Le repo reçoit une Session fournie par l'appelant.
- Ici on accepte client_id en uuid.UUID **ou** str (coercition défensive).
- On garde le contrat "pas de commit" (flush seulement) : le service décide.
"""
from typing import Iterator, Optional, Any
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.database.models.machine import Machine


def _as_uuid(v: Any) -> uuid.UUID:
    """Accepte uuid.UUID | str | Any -> uuid.UUID (lève ValueError si invalide)."""
    if isinstance(v, uuid.UUID):
        return v
    return uuid.UUID(str(v))


class MachineRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, machine_id: Any) -> Optional[Machine]:
        """Retourne une Machine par son id, ou None."""
        return self.db.get(Machine, _as_uuid(machine_id))

    def by_client_and_fingerprint(self, client_id: Any, fingerprint: str) -> Optional[Machine]:
        """Retourne la machine d'un client pour une empreinte donnée, ou None."""
        stmt = select(Machine).where(
            Machine.client_id == _as_uuid(client_id),
            Machine.fingerprint == fingerprint,
        )
        return self.db.scalar(stmt)

    def by_client_and_hostname(self, client_id: Any, hostname: str) -> Optional[Machine]:
        """Retourne la machine d'un client pour un hostname donné, ou None."""
        stmt = select(Machine).where(
            Machine.client_id == _as_uuid(client_id),
            Machine.hostname == hostname,
        )
        return self.db.scalar(stmt)

    def create(
        self,
        client_id: Any,
        hostname: str,
        os_type: Optional[str] = None,
        os_version: Optional[str] = None,
    ) -> Machine:
        """
        Crée une Machine **sans commit** (l'appelant décide du commit/rollback).
        `flush()` garantit que `m.id` est disponible tout de suite.
        """
        m = Machine(
            client_id=_as_uuid(client_id),
            hostname=hostname,
            os_type=os_type,
            os_version=os_version,
        )
        self.db.add(m)
        self.db.flush()     # m.id dispo
        return m

    def iter_all(self) -> Iterator[Machine]:
        """Itère sur toutes les machines."""
        stmt = select(Machine)
        yield from self.db.scalars(stmt)
