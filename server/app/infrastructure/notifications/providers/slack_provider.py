# server/app/infrastructure/notifications/providers/slack_provider.py
from __future__ import annotations
"""SlackProvider — envoi de notifications via webhook Slack (Incoming Webhooks)."""

from typing import Optional, Dict, Any
import requests


class SlackProvider:
    def __init__(self, webhook: Optional[str] = None):
        """
        En prod, le webhook est injecté par la task Celery `notify` à partir
        des ClientSettings. Aucun fallback global ici.
        """
        self.webhook = (webhook or "").strip()
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
            ch = channel.strip()
            # Normalise: accepte "canal" ou "#canal"
            if ch and not ch.startswith("#"):
                ch = f"#{ch}"
            if ch != "#":  # évite un channel vide déguisé
                payload["channel"] = ch
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
        except Exception:  # noqa: BLE001
            return False

    def _get_color(self, severity: str) -> str:
        colors = {
            "info": "#36a64f",
            "warning": "#ffcc00",
            "error": "#ff0000",
            "critical": "#ff0000",
        }
        return colors.get(severity.lower(), "#36a64f")

    def _format_context(self, context: Dict[str, Any]) -> list:
        return [{"title": k, "value": str(v), "short": True} for k, v in context.items()]
