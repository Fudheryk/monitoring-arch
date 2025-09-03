# server/app/infrastructure/notifications/providers/slack_provider.py
import os
import json
import requests
from typing import Optional, Dict, Any
from requests.exceptions import RequestException

class SlackProvider:
    def __init__(self, webhook: Optional[str] = None):
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
        icon_emoji: Optional[str] = None
    ) -> bool:
        """
        Envoie une notification Slack enrichie.
        
        Args:
            title: Titre du message
            text: Contenu principal
            severity: Niveau (info/warning/error)
            context: Données supplémentaires
            channel: Canal override (#channel/@user)
            username: Override du nom d'affichage
            icon_emoji: Emoji pour l'icône (:ghost:)
        """
        payload = {
            "text": f"[{severity.upper()}] {title}",
            "attachments": [{
                "color": self._get_color(severity),
                "text": text,
                "fields": self._format_context(context or {}),
                "footer": "Envoyé depuis Monitoring System"
            }]
        }
        
        if channel:
            payload["channel"] = channel
        if username:
            payload["username"] = username
        if icon_emoji:
            payload["icon_emoji"] = icon_emoji

        try:
            response = requests.post(
                self.webhook,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=5
            )
            return response.status_code == 200
        except RequestException:
            return False

    def _get_color(self, severity: str) -> str:
        colors = {
            "info": "#36a64f",  # Vert
            "warning": "#ffcc00",  # Jaune
            "error": "#ff0000"  # Rouge
        }
        return colors.get(severity.lower(), "#36a64f")

    def _format_context(self, context: Dict[str, Any]) -> list:
        return [{
            "title": key,
            "value": str(value),
            "short": True
        } for key, value in context.items()]

