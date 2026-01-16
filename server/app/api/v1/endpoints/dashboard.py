from __future__ import annotations
"""
server/app/api/v1/endpoints/dashboard.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Dashboard summary (JWT cookies).

✅ Auth: JWT via get_current_user (pas d'API key pour l'UI)
✅ Multi-tenant: scoping par current_user.client_id

Retourne des compteurs simples :
- total_machines
- open_incidents
- firing_alerts
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.database.models.alert import Alert
from app.infrastructure.persistence.database.models.incident import Incident
from app.infrastructure.persistence.database.models.machine import Machine
from app.infrastructure.persistence.database.session import get_db
from app.presentation.api.deps import get_current_user

router = APIRouter(prefix="/dashboard")


@router.get("/summary")
async def summary(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    """
    Retourne des compteurs scoppés au tenant courant.

    On utilise `db.scalar(select(func.count()))` et on cast en int
    avec défaut 0 si None (tables vides).
    """
    client_id = getattr(current_user, "client_id", None)
    if not client_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_client_id")

    total_machines = db.scalar(
        select(func.count()).select_from(Machine).where(Machine.client_id == client_id)
    ) or 0

    open_incidents = db.scalar(
        select(func.count())
        .select_from(Incident)
        .where(Incident.client_id == client_id, Incident.status == "OPEN")
    ) or 0

    # Join Machine pour obtenir client_id (defense-in-depth si Alert n'a pas client_id).
    firing_alerts = db.scalar(
        select(func.count())
        .select_from(Alert)
        .join(Machine, Alert.machine_id == Machine.id)
        .where(Machine.client_id == client_id, Alert.status == "FIRING")
    ) or 0

    return {
        "total_machines": int(total_machines),
        "open_incidents": int(open_incidents),
        "firing_alerts": int(firing_alerts),
    }
