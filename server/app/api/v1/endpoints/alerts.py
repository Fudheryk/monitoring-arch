from __future__ import annotations
"""server/app/api/v1/endpoints/alerts.py
~~~~~~~~~~~~~~~~~~~~~~~~
GET alerts.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.security import api_key_auth
from app.infrastructure.persistence.database.session import get_session
from app.infrastructure.persistence.database.models.alert import Alert
from app.infrastructure.persistence.database.models.machine import Machine

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import api_key_auth
from app.infrastructure.persistence.database.session import get_session
from app.infrastructure.persistence.database.models.alert import Alert
from app.infrastructure.persistence.database.models.machine import Machine

router = APIRouter(prefix="/alerts")

@router.get("")
async def list_alerts(api_key=Depends(api_key_auth), session: Session = Depends(get_session)) -> list[dict]:
    q = select(Alert).join(Machine, Alert.machine_id == Machine.id).where(Machine.client_id == api_key.client_id).order_by(Alert.triggered_at.desc()).limit(100)
    rows = session.scalars(q).all()
    return [{
        "id": str(a.id),
        "machine_id": str(a.machine_id),
        "metric_id": str(a.metric_id) if a.metric_id else None,
        "status": a.status,
        "severity": a.severity,
        "message": a.message,
        "triggered_at": a.triggered_at.isoformat(),
        "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
    } for a in rows]
