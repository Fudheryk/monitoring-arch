from __future__ import annotations
"""server/app/api/v1/endpoints/settings.py
~~~~~~~~~~~~~~~~~~~~~~~~
Client settings.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.security import api_key_auth
from app.infrastructure.persistence.database.session import get_session
from app.infrastructure.persistence.database.models.client_settings import ClientSettings

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import api_key_auth
from app.infrastructure.persistence.database.session import get_session
from app.infrastructure.persistence.database.models.client_settings import ClientSettings

router = APIRouter(prefix="/settings")

@router.get("")
async def get_settings(api_key=Depends(api_key_auth), session: Session = Depends(get_session)) -> dict:
    s = session.scalar(select(ClientSettings).where(ClientSettings.client_id == api_key.client_id))
    if not s:
        return {}
    return {
        "notification_email": s.notification_email,
        "slack_webhook_url": s.slack_webhook_url,
        "heartbeat_threshold_minutes": s.heartbeat_threshold_minutes,
        "consecutive_failures_threshold": s.consecutive_failures_threshold,
        "alert_grouping_enabled": s.alert_grouping_enabled,
        "alert_grouping_window_seconds": s.alert_grouping_window_seconds,
    }
