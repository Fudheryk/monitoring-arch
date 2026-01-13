from __future__ import annotations
"""
server/app/infrastructure/persistence/database/models/__init__.py

Registre des modèles ORM.

⚠️ Importé pour ses EFFETS DE BORD : enregistre toutes les classes ORM dans
`Base.metadata` (Alebmic autogénération & create_all() en tests SQLite).
"""

from .client import Client
from .api_key import ApiKey
from .client_settings import ClientSettings
from .user import User
from .machine import Machine
from .sample import Sample
from .alert import Alert
from .incident import Incident
from .http_target import HttpTarget   # ✅ corrigé (pas "from HttpTarget")
from .notification_log import NotificationLog
from .ingest_event import IngestEvent
from .outbox_event import OutboxEvent, OutboxStatus
from .metric_definitions import MetricDefinitions
from .metric_instance import MetricInstance
from .threshold_template import ThresholdTemplate
from .threshold_new import ThresholdNew

__all__ = [
    "Client",
    "ApiKey",
    "ClientSettings",
    "User",
    "Machine",
    "Sample",
    "Alert",
    "Incident",
    "HttpTarget",
    "NotificationLog",
    "IngestEvent",
    "OutboxEvent",
    "OutboxStatus",
    "MetricDefinitions",
    "MetricInstance",
    "ThresholdTemplate",
    "ThresholdNew"
]
