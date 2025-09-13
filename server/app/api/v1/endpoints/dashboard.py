from __future__ import annotations
"""server/app/api/v1/endpoints/dashboard.py
~~~~~~~~~~~~~~~~~~~~~~~~
Dashboard summary.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import api_key_auth
from app.infrastructure.persistence.database.session import get_db
from app.infrastructure.persistence.database.models.machine import Machine
from app.infrastructure.persistence.database.models.alert import Alert
from app.infrastructure.persistence.database.models.incident import Incident

router = APIRouter(prefix="/dashboard")

@router.get("/summary")
async def summary(
    api_key=Depends(api_key_auth),
    db: Session = Depends(get_db),
) -> dict:
    """
    Retourne des compteurs simples, scoping par client_id (auth par API key).
    On utilise `db.scalar(select(...))` (SQLAlchemy 2.0) et on cast en int
    avec défaut 0 si None (tables vides).
    """
    total_machines = db.scalar(
        select(func.count()).select_from(Machine).where(Machine.client_id == api_key.client_id)
    ) or 0

    open_incidents = db.scalar(
        select(func.count())
        .select_from(Incident)
        .where(Incident.client_id == api_key.client_id, Incident.status == "OPEN")
    ) or 0

    firing_alerts = db.scalar(
        select(func.count())
        .select_from(Alert)
        .join(Machine, Alert.machine_id == Machine.id)
        .where(Machine.client_id == api_key.client_id, Alert.status == "FIRING")
    ) or 0

    return {
        "total_machines": int(total_machines),
        "open_incidents": int(open_incidents),
        "firing_alerts": int(firing_alerts),
    }
    