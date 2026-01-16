from __future__ import annotations
"""
server/app/api/v1/endpoints/incidents.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
GET /incidents — liste des incidents du client authentifié (JWT cookies).
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.database.models.incident import Incident
from app.infrastructure.persistence.database.session import get_db
from app.presentation.api.deps import get_current_user

router = APIRouter(prefix="/incidents")


@router.get("")
async def list_incidents(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> list[dict]:
    """
    Retourne les 100 derniers incidents du tenant courant.
    """
    client_id = getattr(current_user, "client_id", None)
    if not client_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_client_id")

    def _display_title(i: Incident) -> str:
        # UI-only : ne touche pas au stockage
        base = (i.title or "").lstrip()
        n = getattr(i, "incident_number", None)
        if n:
            try:
                n_int = int(n)
            except Exception:
                n_int = 0
            if n_int > 0:
                return f"(#{n_int:03d}) {base}"
        return base

    rows = db.scalars(
        select(Incident)
        .where(Incident.client_id == client_id)
        .order_by(Incident.created_at.desc())
        .limit(100)
    ).all()

    return [
        {
            "id": str(i.id),
            "title": _display_title(i),
            "title_raw": i.title,
            "status": i.status,
            "severity": i.severity,
            "machine_id": str(i.machine_id) if i.machine_id else None,
            "metric_instance_id": str(i.metric_instance_id) if i.metric_instance_id else None,
            "http_target_id": str(i.http_target_id) if i.http_target_id else None,
            "type": i.incident_type,
            "incident_number": i.incident_number,
            "created_at": i.created_at.isoformat() if i.created_at else None,
            "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
        }
        for i in rows
    ]
