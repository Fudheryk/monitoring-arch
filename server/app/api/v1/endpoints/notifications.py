from __future__ import annotations
"""server/app/api/v1/endpoints/notifications.py
~~~~~~~~~~~~~~~~~~~~~~~~
GET notifications.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.core.security import api_key_auth
from app.infrastructure.persistence.database.session import get_db
from app.infrastructure.persistence.database.models.notification_log import NotificationLog
from app.infrastructure.persistence.database.models.incident import Incident
from app.infrastructure.persistence.database.models.alert import Alert

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(
    api_key=Depends(api_key_auth),
    db: Session = Depends(get_db),
) -> list[dict]:
    """
    Retourne les notifications d'un client (success/failed/skipped/pending), ordre déchronologique.


    - Filtre sur:
        - client_id
    - Limite de 1000 pour éviter de tout embarquer.

    `severity` est déduite depuis l'incident ou l'alerte liée :
      - si incident_id → Incident.severity
      - sinon si alert_id → Alert.severity
      - sinon fallback "info"
    """
    stmt = (
        select(NotificationLog, Incident, Alert)
        .outerjoin(Incident, NotificationLog.incident_id == Incident.id)
        .outerjoin(Alert, NotificationLog.alert_id == Alert.id)
        .where(
            NotificationLog.client_id == api_key.client_id,
            NotificationLog.sent_at.is_not(None),  # 🔎 seulement les notifs réellement envoyées
        )
        .order_by(func.coalesce(NotificationLog.sent_at, NotificationLog.created_at).desc())
        .limit(1000)
    )

    rows = db.execute(stmt).all()

    out: list[dict] = []
    for n, inc, alert in rows:
        ts = n.sent_at or n.created_at

        # Sévérité "métier" liée à l'incident / alerte
        if inc is not None and inc.severity:
            severity = inc.severity
        elif alert is not None and alert.severity:
            severity = alert.severity
        else:
            # Fallback : notification hors incident/alerte
            severity = "info"

        out.append(
            {
                "id": str(n.id),
                "client_id": str(n.client_id),
                "incident_id": str(n.incident_id) if n.incident_id else None,
                "alert_id": str(n.alert_id) if n.alert_id else None,
                "provider": n.provider,              # "email" / "slack" / …
                "recipient": n.recipient,
                "status": n.status,                  # "success" / "failed" / "skipped_*"
                "severity": severity,                # "info" / "warning" / "error" / "critical"
                "message": n.message,
                "error_message": n.error_message,
                "created_at": n.created_at.isoformat(),
                "sent_at": ts.isoformat() if ts else None,
            }
        )

    return out
