from __future__ import annotations
"""
server/app/api/v1/endpoints/alerts.py
~~~~~~~~~~~~~~~~~~~~~~~~
GET /alerts — liste les alertes du client authentifié.

Notes :
- Utilise Depends(get_db) pour une session auto-fermée.
- Inclut le hostname de la machine (utile pour filtrer côté tests).
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import api_key_auth
from app.infrastructure.persistence.database.session import get_db
from app.infrastructure.persistence.database.models.alert import Alert
from app.infrastructure.persistence.database.models.machine import Machine

router = APIRouter(prefix="/alerts")


@router.get("")
async def list_alerts(
    api_key=Depends(api_key_auth),
    db: Session = Depends(get_db),
) -> list[dict]:
    # On sélectionne l'alerte + le hostname de la machine
    stmt = (
        select(Alert, Machine.hostname)
        .join(Machine, Alert.machine_id == Machine.id)
        .where(Machine.client_id == api_key.client_id)
        .order_by(Alert.triggered_at.desc())
        .limit(100)
    )
    rows = db.execute(stmt).all()

    out: list[dict] = []
    for alert, hostname in rows:
        out.append(
            {
                "id": str(alert.id),
                "machine_id": str(alert.machine_id) if alert.machine_id else None,
                "metric_id": str(alert.metric_id) if alert.metric_id else None,
                "status": alert.status,
                "severity": alert.severity,
                "message": alert.message,
                "triggered_at": alert.triggered_at.isoformat() if alert.triggered_at else None,
                "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
                # bonus : facilite les filtres côté tests/intégration
                "machine": {"hostname": hostname} if hostname else None,
            }
        )
    return out
