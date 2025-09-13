from __future__ import annotations
"""server/app/application/services/heartbeat_service.py
~~~~~~~~~~~~~~~~~~~~~~~~
Détection machines offline.
"""
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from app.core.config import settings
from app.infrastructure.persistence.database.session import get_sync_session
from app.infrastructure.persistence.database.models.machine import Machine
from app.infrastructure.persistence.repositories.incident_repository import IncidentRepository

def check_offline() -> int:
    now = datetime.now(timezone.utc)
    limit = now - timedelta(minutes=settings.NO_DATA_MINUTES)
    count = 0
    with get_sync_session() as session:
        irepo = IncidentRepository(session)
        rows = session.scalars(select(Machine)).all()
        for m in rows:
            if not m.last_seen or m.last_seen < limit:
                irepo.open(client_id=m.client_id, title="Machine offline", severity="warning", machine_id=m.id, description="No data since threshold")
                count += 1
        session.commit()
    return count
