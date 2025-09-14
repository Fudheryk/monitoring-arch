from __future__ import annotations
"""server/app/api/v1/endpoints/incidents.py
~~~~~~~~~~~~~~~~~~~~~~~~
GET incidents.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.security import api_key_auth
from app.infrastructure.persistence.database.session import get_db
from app.infrastructure.persistence.database.models.incident import Incident

router = APIRouter(prefix="/incidents")

@router.get("")
async def list_incidents(api_key=Depends(api_key_auth), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(Incident).where(Incident.client_id == api_key.client_id).order_by(Incident.created_at.desc()).limit(100)).all()
    return [{
        "id": str(i.id),
        "title": i.title,
        "status": i.status,
        "severity": i.severity,
        "machine_id": str(i.machine_id) if i.machine_id else None,
        "created_at": i.created_at.isoformat(),
        "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
    } for i in rows]
