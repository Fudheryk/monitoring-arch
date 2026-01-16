from __future__ import annotations
"""
server/app/api/v1/endpoints/notifications.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
GET /notifications — liste des notifications du client authentifié (JWT).

Objectifs (E2E / "0 traces") :
- Scoper strictement par client_id (issu du user JWT).
- Ne PAS filtrer sur sent_at : en e2e on veut voir aussi `skipped_*` et `pending`
  (souvent sent_at = NULL).
- Tri stable : sent_at si présent, sinon created_at.

Notes :
- `severity` est déduite depuis l'incident ou l'alerte liée :
  - incident_id -> Incident.severity
  - sinon alert_id -> Alert.severity
  - sinon fallback "info"
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.database.session import get_db
from app.infrastructure.persistence.database.models.notification_log import NotificationLog
from app.infrastructure.persistence.database.models.incident import Incident
from app.infrastructure.persistence.database.models.alert import Alert

# ✅ JWT-only : plus de dépendance api_key_auth ici
from app.presentation.api.deps import get_current_user
from app.infrastructure.persistence.database.models.user import User

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    """
    Retourne les notifications d'un client, ordre déchronologique.

    Important :
    - On ne filtre PAS sur sent_at IS NOT NULL, car :
      - les statuts skipped_* (cooldown/no_webhook/etc.) peuvent ne pas avoir sent_at
      - les "pending" non plus
    - On garde une limite haute (1000) mais le scope client_id suffit.
    """
    client_id = getattr(current_user, "client_id", None)

    stmt = (
        select(NotificationLog, Incident, Alert)
        .outerjoin(Incident, NotificationLog.incident_id == Incident.id)
        .outerjoin(Alert, NotificationLog.alert_id == Alert.id)
        .where(NotificationLog.client_id == client_id)
        .order_by(func.coalesce(NotificationLog.sent_at, NotificationLog.created_at).desc())
        .limit(1000)
    )

    rows = db.execute(stmt).all()

    out: list[dict] = []
    for n, inc, alert in rows:
        # Timestamp d'affichage : sent_at si dispo, sinon created_at.
        ts = n.sent_at or n.created_at

        # Sévérité "métier" liée à l'incident / alerte.
        if inc is not None and getattr(inc, "severity", None):
            severity = inc.severity
        elif alert is not None and getattr(alert, "severity", None):
            severity = alert.severity
        else:
            severity = "info"

        out.append(
            {
                "id": str(n.id),
                "client_id": str(n.client_id),
                "incident_id": str(n.incident_id) if n.incident_id else None,
                "alert_id": str(n.alert_id) if n.alert_id else None,
                "provider": n.provider,  # "email" / "slack" / "cooldown" / "system" / …
                "recipient": n.recipient,
                "status": n.status,  # "success" / "failed" / "skipped_*" / "pending" ...
                "severity": severity,
                "message": n.message,
                "error_message": getattr(n, "error_message", None),
                "created_at": n.created_at.isoformat() if n.created_at else None,
                "sent_at": ts.isoformat() if ts else None,
            }
        )

    return out
