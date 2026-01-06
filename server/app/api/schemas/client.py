from __future__ import annotations
"""server/app/api/schemas/client.py
~~~~~~~~~~~~~~~~~~~~~~~~
Schemas clients.
"""
from pydantic import BaseModel, EmailStr


class ClientSettingsOut(BaseModel):
    notification_email: EmailStr | None = None
    slack_webhook_url: str | None = None
    slack_channel_name: str | None = None
    heartbeat_threshold_minutes: int = 5
    consecutive_failures_threshold: int = 2
    alert_grouping_enabled: bool = True
    alert_grouping_window_seconds: int = 300
    reminder_notification_seconds: int = 600
    notify_on_resolve: bool = True
    grace_period_seconds: int = 120
