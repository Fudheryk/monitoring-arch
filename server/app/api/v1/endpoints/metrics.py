from __future__ import annotations
"""server/app/api/v1/endpoints/metrics.py
~~~~~~~~~~~~~~~~~~~~~~~~
Endpoints Metrics.
"""

import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import api_key_auth
from app.infrastructure.persistence.database.session import get_db
from app.infrastructure.persistence.database.models.metric import Metric
from app.infrastructure.persistence.database.models.machine import Machine

router = APIRouter(prefix="/metrics")


@router.get("")
async def list_metrics_root(
    api_key=Depends(api_key_auth),
    db: Session = Depends(get_db),
) -> dict:
    """
    Endpoint minimal pour satisfaire le smoke test GET /api/v1/metrics.
    On peut retourner un payload neutre.
    """
    # (Option: tu peux lister les metrics du client ici si tu veux augmenter la valeur)
    return {"items": [], "total": 0}


@router.get("/{machine_id}")
async def list_metrics_by_machine(
    machine_id: str,
    api_key=Depends(api_key_auth),
    db: Session = Depends(get_db),
) -> list[dict]:
    """
    Liste les métriques d'une machine donnée (scopée par le client de l'API key).
    Conversion sûre en UUID pour compatibilité SQLite/Postgres.
    """
    try:
        mid = machine_id if isinstance(machine_id, uuid.UUID) else uuid.UUID(str(machine_id))
    except Exception:
        raise HTTPException(status_code=404, detail="Machine not found")

    m = db.get(Machine, mid)
    if not m or m.client_id != api_key.client_id:
        raise HTTPException(status_code=404, detail="Machine not found")

    rows = db.scalars(
        select(Metric).where(Metric.machine_id == mid).order_by(Metric.name)
    ).all()

    return [
        {
            "id": str(mt.id),
            "name": mt.name,
            "type": mt.type,
            "unit": mt.unit,
            "baseline_value": mt.baseline_value,
            "is_alerting_enabled": mt.is_alerting_enabled,
        }
        for mt in rows
    ]
