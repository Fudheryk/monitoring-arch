from __future__ import annotations
"""server/app/infrastructure/persistence/repositories/machine_repository.py
~~~~~~~~~~~~~~~~~~~~~~~~
Repo machines.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.infrastructure.persistence.database.models.machine import Machine

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.database.models.machine import Machine

class MachineRepository:
    def __init__(self, session: Session):
        self.s = session

    def by_client_and_hostname(self, client_id, hostname) -> Machine | None:
        return self.s.scalar(select(Machine).where(Machine.client_id == client_id, Machine.hostname == hostname))

    def create(self, client_id, hostname, os_type=None, os_version=None) -> Machine:
        m = Machine(client_id=client_id, hostname=hostname, os_type=os_type, os_version=os_version)
        self.s.add(m)
        self.s.flush()
        return m

    def iter_all(self):
        for m in self.s.scalars(select(Machine)):
            yield m
