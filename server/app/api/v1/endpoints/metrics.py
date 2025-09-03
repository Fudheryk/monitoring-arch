from __future__ import annotations
"""server/app/api/v1/endpoints/metrics.py
~~~~~~~~~~~~~~~~~~~~~~~~
GET metrics par machine.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.security import api_key_auth
from app.infrastructure.persistence.database.session import get_session
from app.infrastructure.persistence.database.models.metric import Metric
from app.infrastructure.persistence.database.models.machine import Machine

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import api_key_auth
from app.infrastructure.persistence.database.session import get_session
from app.infrastructure.persistence.database.models.metric import Metric
from app.infrastructure.persistence.database.models.machine import Machine

router = APIRouter(prefix="/metrics")

@router.get("/{machine_id}")
async def list_metrics(machine_id: str, api_key=Depends(api_key_auth), session: Session = Depends(get_session)) -> list[dict]:
    m = session.get(Machine, machine_id)
    if not m or m.client_id != api_key.client_id:
        raise HTTPException(status_code=404, detail="Machine not found")
    rows = session.scalars(select(Metric).where(Metric.machine_id == machine_id).order_by(Metric.name)).all()
    return [{
        "id": str(mt.id),
        "name": mt.name,
        "type": mt.type,
        "unit": mt.unit,
        "baseline_value": mt.baseline_value,
        "is_alerting_enabled": mt.is_alerting_enabled,
    } for mt in rows]
