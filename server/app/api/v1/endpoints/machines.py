from __future__ import annotations
"""server/app/api/v1/endpoints/machines.py
~~~~~~~~~~~~~~~~~~~~~~~~
GET machines.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.security import api_key_auth
from app.infrastructure.persistence.database.session import get_session
from app.infrastructure.persistence.database.models.machine import Machine

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import api_key_auth
from app.infrastructure.persistence.database.session import get_session
from app.infrastructure.persistence.database.models.machine import Machine

router = APIRouter(prefix="/machines")

@router.get("")
async def list_machines(api_key=Depends(api_key_auth), session: Session = Depends(get_session)) -> list[dict]:
    rows = session.scalars(select(Machine).where(Machine.client_id == api_key.client_id).order_by(Machine.hostname)).all()
    return [{
        "id": str(m.id),
        "hostname": m.hostname,
        "os_type": m.os_type,
        "last_seen": m.last_seen.isoformat() if m.last_seen else None,
    } for m in rows]
