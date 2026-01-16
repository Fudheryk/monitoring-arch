from __future__ import annotations
"""
server/app/api/v1/endpoints/alerts.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
GET /alerts — liste les alertes du client authentifié (JWT cookies).

Notes :
- Auth: JWT (Depends(get_current_user)) — aucune API key pour l'UI.
- Multi-tenant: filtrage strict par current_user.client_id.
- Inclut le hostname de la machine (utile pour filtres côté tests/intégration).
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.database.models.alert import Alert
from app.infrastructure.persistence.database.models.machine import Machine
from app.infrastructure.persistence.database.session import get_db
from app.presentation.api.deps import get_current_user

router = APIRouter(prefix="/alerts")


@router.get("")
async def list_alerts(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> list[dict]:
    """
    Liste les 100 dernières alertes pour le tenant courant.

    Pourquoi join Machine :
    - Le modèle Alert référence machine_id ; Machine porte client_id.
    - On filtre donc via Machine.client_id pour garantir le scoping tenant.

    Remarque :
    - Si votre table Alert possède aussi client_id, vous pouvez ajouter un filtre
      supplémentaire en "defense in depth".
    """
    client_id = getattr(current_user, "client_id", None)
    if not client_id:
        # Token valide mais user incomplet (ou modèle non conforme)
        # => 401 pour rester cohérent avec le reste de l'app.
        from fastapi import HTTPException, status  # import local pour limiter le bruit
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_client_id")

    stmt = (
        select(Alert, Machine.hostname)
        .join(Machine, Alert.machine_id == Machine.id)
        .where(Machine.client_id == client_id)
        .order_by(Alert.triggered_at.desc().nullslast())
        .limit(100)
    )
    rows = db.execute(stmt).all()

    out: list[dict] = []
    for alert, hostname in rows:
        out.append(
            {
                "id": str(alert.id),
                "machine_id": str(alert.machine_id) if alert.machine_id else None,
                "metric_instance_id": str(alert.metric_instance_id) if alert.metric_instance_id else None,
                "status": alert.status,
                "severity": alert.severity,
                "message": alert.message,
                "triggered_at": alert.triggered_at.isoformat() if alert.triggered_at else None,
                "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
                # Bonus : facilite les filtres côté tests/intégration
                "machine": {"hostname": hostname} if hostname else None,
            }
        )
    return out
