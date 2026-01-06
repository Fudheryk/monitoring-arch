# coding: utf-8
#!/usr/bin/env python3
# server/app/api/v1/serializers/client_settings.py
"""
Sérialise un ClientSettings en dictionnaire JSON prêt à exposer.
"""


from typing import Any, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from app.infrastructure.persistence.database.models.client_settings import ClientSettings


def serialize_client_settings(s: "ClientSettings") -> Dict[str, Any]:
    return {
        "notification_email": s.notification_email,
        "slack_webhook_url": s.slack_webhook_url,
        "slack_channel_name": s.slack_channel_name,
        "heartbeat_threshold_minutes": s.heartbeat_threshold_minutes,
        "consecutive_failures_threshold": s.consecutive_failures_threshold,
        "alert_grouping_enabled": s.alert_grouping_enabled,
        "alert_grouping_window_seconds": s.alert_grouping_window_seconds,
        "reminder_notification_seconds": s.reminder_notification_seconds,
        "notify_on_resolve": s.notify_on_resolve,
        "grace_period_seconds": s.grace_period_seconds,
    }