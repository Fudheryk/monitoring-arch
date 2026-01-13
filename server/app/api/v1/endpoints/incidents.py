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

    def _display_title(i: Incident) -> str:
        base = (i.title or "").lstrip()
        n = getattr(i, "incident_number", None)
        if n and int(n) > 0:
            return f"(#{int(n):03d}) {base}"
        return base

    rows = db.scalars(
        select(Incident)
        .where(Incident.client_id == api_key.client_id)
        .order_by(Incident.created_at.desc())
        .limit(100)
    ).all()

    return [{
        "id": str(i.id),
        # ✅ Affichage UI (ne modifie pas la DB)
        "title": _display_title(i),
        # (optionnel) si tu veux exposer aussi le titre “raw” stocké
        "title_raw": i.title,
        "status": i.status,
        "severity": i.severity,
        "machine_id": str(i.machine_id) if i.machine_id else None,
        "metric_instance_id": str(i.metric_instance_id) if i.metric_instance_id else None,
        "http_target_id": str(i.http_target_id) if i.http_target_id else None,
        "type": i.incident_type,
        "incident_number": i.incident_number,
        "created_at": i.created_at.isoformat(),
        "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
    } for i in rows]
