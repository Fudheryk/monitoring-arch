from __future__ import annotations
"""server/app/infrastructure/notifications/providers/slack_provider.py
~~~~~~~~~~~~~~~~~~~~~~~~
SlackProvider — envoi de notifications via webhook Slack (Incoming Webhooks).
"""

import os
from typing import Optional, Dict, Any

import requests


class SlackProvider:
    def __init__(self, webhook: Optional[str] = None):
        # En tests unitaires, le provider est mocké ; sinon on lit l’ENV.
        self.webhook = webhook or os.getenv("SLACK_WEBHOOK")
        if not self.webhook:
            raise ValueError("Slack webhook URL must be provided")

    def send(
        self,
        *,
        title: str,
        text: str,
        severity: str = "info",
        context: Optional[Dict[str, Any]] = None,
        channel: Optional[str] = None,
        username: Optional[str] = None,
        icon_emoji: Optional[str] = None,
    ) -> bool:
        """
        Envoie une notification Slack enrichie.
        - `context` est mappé en fields façon attachments.
        - Slack Incoming Webhooks renvoie généralement HTTP 200.
        """
        payload = {
            "text": f"[{severity.upper()}] {title}",
            "attachments": [
                {
                    "color": self._get_color(severity),
                    "text": text,
                    "fields": self._format_context(context or {}),
                    "footer": "Envoyé depuis Monitoring System",
                }
            ],
        }

        if channel:
            payload["channel"] = channel
        if username:
            payload["username"] = username
        if icon_emoji:
            payload["icon_emoji"] = icon_emoji

        try:
            r = requests.post(
                self.webhook,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=5,
            )
            return r.status_code == 200
        except Exception:
            # Attrape TOUTE exception (pas uniquement RequestException) pour s’aligner avec les tests
            return False

    def _get_color(self, severity: str) -> str:
        colors = {
            "info": "#36a64f",     # Vert
            "warning": "#ffcc00",  # Jaune
            "error": "#ff0000",    # Rouge
            "critical": "#ff0000", # Mappe "critical" sur rouge
        }
        return colors.get(severity.lower(), "#36a64f")

    def _format_context(self, context: Dict[str, Any]) -> list:
        return [
            {"title": key, "value": str(value), "short": True}
            for key, value in context.items()
        ]
