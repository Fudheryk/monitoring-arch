from __future__ import annotations
"""server/app/api/schemas/client.py
~~~~~~~~~~~~~~~~~~~~~~~~
Schemas clients.
"""
from pydantic import BaseModel, EmailStr


class ClientSettingsOut(BaseModel):
    notification_email: EmailStr | None = None
    slack_webhook_url: str | None = None
    heartbeat_threshold_minutes: int = 5
    consecutive_failures_threshold: int = 2
    alert_grouping_enabled: bool = True
    alert_grouping_window_seconds: int = 300
