from __future__ import annotations
"""server/app/infrastructure/persistence/repositories/incident_repository.py
~~~~~~~~~~~~~~~~~~~~~~~~
Repo incidents.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.infrastructure.persistence.database.models.incident import Incident
from datetime import datetime, timezone
from uuid import UUID

class IncidentRepository:
    def __init__(self, session: Session):
        self.s = session

    def open(self, *, client_id, title, severity, machine_id=None, description=None) -> Incident:
        # Vérifier d'abord si l'incident existe déjà
        existing = self.s.scalars(
            select(Incident).where(
                Incident.client_id == client_id,
                Incident.machine_id == machine_id,
                Incident.title == title,
                Incident.status == "OPEN"
            )
        ).first()
    
        if existing:
            # Mettre à jour l'incident existant si nécessaire
            existing.updated_at = datetime.now(timezone.utc)
            return existing
        
        # Créer un nouvel incident seulement s'il n'existe pas
        inc = Incident(
            client_id=client_id, 
            title=title, 
            severity=severity, 
            machine_id=machine_id, 
            description=description
        )
        self.s.add(inc)
        self.s.flush()
        return inc

    def resolve_open_by_machine_and_title(self, *, client_id, machine_id, title) -> int:
        rows = self.s.scalars(select(Incident).where(
            Incident.client_id == client_id, 
            Incident.machine_id == machine_id, 
            Incident.title == title, 
            Incident.status == "OPEN"
        )).all()
        n = 0
        for i in rows:
            i.status = "RESOLVED"
            i.resolved_at = datetime.now(timezone.utc)
            n += 1
        self.s.flush()
        return n
    
    def resolve_by_title(self, client_id: UUID, title: str) -> None:
        """Résout tous les incidents ouverts avec le même titre pour un client donné."""
        rows = self.s.scalars(select(Incident).where(
            Incident.client_id == client_id, 
            Incident.title == title, 
            Incident.status == "OPEN"
        )).all()
        
        for inc in rows:
            inc.status = "RESOLVED"
            inc.resolved_at = datetime.now(timezone.utc)
        
        self.s.flush()