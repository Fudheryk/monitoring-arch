from __future__ import annotations
"""server/app/api/schemas/client_settings.py
~~~~~~~~~~~~~~~~~~~~~~~~
Schemas clients settings.
"""
from typing import Optional
from pydantic import BaseModel, EmailStr, Field


class ClientSettingsBase(BaseModel):
    notification_email: Optional[EmailStr] = None
    slack_webhook_url: Optional[str] = Field(None, max_length=500)
    slack_channel_name: Optional[str] = Field(None, max_length=15)

    heartbeat_threshold_minutes: int = Field(5, ge=1)
    consecutive_failures_threshold: int = Field(2, ge=1)
    alert_grouping_enabled: bool = True
    alert_grouping_window_seconds: int = Field(300, ge=0)
    reminder_notification_seconds: int = Field(600, ge=0)
    grace_period_seconds: int = Field(120, ge=0)
    notify_on_resolve: bool = True


class ClientSettingsOut(ClientSettingsBase):
    """Schéma de sortie complet."""
    pass


class ClientSettingsUpdate(BaseModel):
    """
    Schéma d’update partiel (PATCH-like) :
    seuls les champs fournis sont modifiés.
    """
    notification_email: Optional[EmailStr] = None
    slack_webhook_url: Optional[str] = Field(None, max_length=500)
    slack_channel_name: Optional[str] = Field(None, max_length=15)

    heartbeat_threshold_minutes: Optional[int] = Field(None, ge=1)
    consecutive_failures_threshold: Optional[int] = Field(None, ge=1)
    alert_grouping_enabled: Optional[bool] = None
    alert_grouping_window_seconds: Optional[int] = Field(None, ge=0)
    reminder_notification_seconds: Optional[int] = Field(None, ge=0)
    grace_period_seconds: Optional[int] = Field(None, ge=0)
    notify_on_resolve: Optional[bool] = None