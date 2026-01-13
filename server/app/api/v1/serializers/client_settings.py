# coding: utf-8
#!/usr/bin/env python3
# server/app/api/v1/serializers/client_settings.py
"""
Sérialise un ClientSettings en dictionnaire JSON prêt à exposer.
"""

import re
from typing import Any, Dict, TYPE_CHECKING


if TYPE_CHECKING:
    from app.infrastructure.persistence.database.models.client_settings import ClientSettings

_ENV_PLACEHOLDER_RE = re.compile(r"^\$\{[A-Z0-9_]+\}$")

def _clean_env_placeholder(v: str | None) -> str | None:
    if not v:
        return None
    s = v.strip()
    if _ENV_PLACEHOLDER_RE.match(s):
        return None
    return s

def serialize_client_settings(s: "ClientSettings") -> Dict[str, Any]:
    return {
        "notification_email": _clean_env_placeholder(s.notification_email),
        "slack_webhook_url": _clean_env_placeholder(s.slack_webhook_url),
        "slack_channel_name": _clean_env_placeholder(s.slack_channel_name),
        "heartbeat_threshold_minutes": s.heartbeat_threshold_minutes,
        "consecutive_failures_threshold": s.consecutive_failures_threshold,
        "alert_grouping_enabled": s.alert_grouping_enabled,
        "alert_grouping_window_seconds": s.alert_grouping_window_seconds,
        "reminder_notification_seconds": s.reminder_notification_seconds,
        "grace_period_seconds": s.grace_period_seconds,
        "notify_on_resolve": s.notify_on_resolve,
    }