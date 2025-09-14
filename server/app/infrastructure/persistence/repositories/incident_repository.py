from __future__ import annotations

"""server/app/infrastructure/persistence/repositories/incident_repository.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Repository pour la gestion des incidents.

Principes :
- Le repo **reçoit** une Session SQLAlchemy gérée par l'appelant (endpoint via
  `Depends(get_db)` ou tâche/service via `get_sync_session()`).
- Il ne crée ni ne ferme la session, et **ne commit pas** (c'est le rôle de l'appelant).
- Méthodes fournies :
  - `open(...)` : ouvre (ou met à jour l'existant) un incident "OPEN" pour un couple
    (client_id, machine_id, title).
  - `resolve_open_by_machine_and_title(...)` : passe à "RESOLVED" tous les incidents
    "OPEN" correspondant à (client_id, machine_id, title).
  - `resolve_by_title(...)` : passe à "RESOLVED" tous les incidents "OPEN" d'un
    client pour un `title` donné (toutes machines confondues).
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.database.models.incident import Incident


class IncidentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def open(
        self,
        *,
        client_id: UUID,
        title: str,
        severity: str,
        machine_id: Optional[UUID] = None,
        description: Optional[str] = None,
    ) -> tuple[Incident, bool]:
        """
        Ouvre un incident "OPEN" s'il n'existe pas déjà pour (client_id, machine_id, title).
        Retourne (incident, created) où `created=True` ssi un nouvel incident a été créé.
        """
        existing = self.db.execute(
            select(Incident)
            .where(
                Incident.client_id == client_id,
                Incident.machine_id == machine_id,
                Incident.title == title,
                Incident.status == "OPEN",
            )
            .limit(1)
        ).scalar_one_or_none()

        if existing:
            existing.updated_at = datetime.now(timezone.utc)
            return existing, False

        inc = Incident(
            client_id=client_id,
            title=title,
            severity=severity,
            machine_id=machine_id,
            description=description,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            # status="OPEN",  # si pas de default au modèle
        )
        self.db.add(inc)
        self.db.flush()
        return inc, True

    def resolve_open_by_machine_and_title(
        self,
        *,
        client_id: UUID,
        machine_id: UUID,
        title: str,
    ) -> int:
        """
        Passe à 'RESOLVED' tous les incidents "OPEN" pour (client_id, machine_id, title).
        Retourne le nombre d'incidents modifiés.
        """
        rows = self.db.scalars(
            select(Incident).where(
                Incident.client_id == client_id,
                Incident.machine_id == machine_id,
                Incident.title == title,
                Incident.status == "OPEN",
            )
        ).all()

        n = 0
        now = datetime.now(timezone.utc)
        for inc in rows:
            inc.status = "RESOLVED"
            inc.resolved_at = now
            inc.updated_at = now
            n += 1

        self.db.flush()
        return n

    def resolve_by_title(self, client_id: UUID, title: str) -> int:
        """
        Passe à 'RESOLVED' tous les incidents "OPEN" d'un client pour un `title` donné
        (toutes machines confondues). Retourne le nombre d'incidents modifiés.
        """
        rows = self.db.scalars(
            select(Incident).where(
                Incident.client_id == client_id,
                Incident.title == title,
                Incident.status == "OPEN",
            )
        ).all()

        n = 0
        now = datetime.now(timezone.utc)
        for inc in rows:
            inc.status = "RESOLVED"
            inc.resolved_at = now
            inc.updated_at = now
            n += 1

        self.db.flush()
        return n
