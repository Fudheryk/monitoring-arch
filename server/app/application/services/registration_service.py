from __future__ import annotations
"""server/app/application/services/registration_service.py
~~~~~~~~~~~~~~~~~~~~~~~~
Enregistrement des machines.
"""
from datetime import datetime, timezone
from app.infrastructure.persistence.database.session import get_sync_session
from app.infrastructure.persistence.repositories.machine_repository import MachineRepository

from datetime import datetime, timezone

from app.infrastructure.persistence.database.session import get_sync_session
from app.infrastructure.persistence.repositories.machine_repository import MachineRepository

def ensure_machine(machine_info, api_key_obj):
    with get_sync_session() as session:
        mrepo = MachineRepository(session)
        client_id = api_key_obj.client_id
        m = mrepo.by_client_and_hostname(client_id, machine_info.hostname)
        if not m:
            m = mrepo.create(client_id, machine_info.hostname, machine_info.os, None)
        m.last_seen = datetime.now(timezone.utc)
        session.commit()
        session.refresh(m)
        return m
