# server/app/infrastructure/messaging/events.py
from __future__ import annotations
"""
Types d'événements outbox (simples dataclasses) consommés par le worker.
On garde ça minimal et indépendant de l'ORM.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class IncidentRaised:
    incident_id: str
    client_id: str
    resource_id: Optional[str]
    severity: str
    created_at: datetime
    context: Mapping[str, Any]


@dataclass(frozen=True)
class IncidentResolved:
    incident_id: str
    client_id: str
    resolved_at: datetime
    context: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ReminderDue:
    incident_id: str
    client_id: str
    due_at: datetime
    context: Mapping[str, Any]
