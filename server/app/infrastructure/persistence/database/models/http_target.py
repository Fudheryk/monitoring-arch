from __future__ import annotations
"""server/app/infrastructure/persistence/database/models/http_target.py
~~~~~~~~~~~~~~~~~~~~~~~~
Table http_targets.
"""

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.infrastructure.persistence.database.base import Base
import uuid
import datetime as dt


class HttpTarget(Base):
    __tablename__ = "http_targets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(1000))
    method: Mapped[str] = mapped_column(String(10), default="GET")
    accepted_status_codes: Mapped[list | None] = mapped_column(
        sa.JSON,
        nullable=True,
        comment="Ranges de codes HTTP acceptés. [[200,299]] = 2xx, [[200,299],[404,404]] = 2xx+404. NULL = mode simple (<500)"
    )
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=30)
    check_interval_seconds: Mapped[int] = mapped_column(Integer, default=300)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_check_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_state_change_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_response_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), 
        default=lambda: dt.datetime.now(dt.timezone.utc), 
        onupdate=lambda: dt.datetime.now(dt.timezone.utc),
    )

    def is_status_accepted(self, status: int | None) -> bool:
        """Vérifie si un status code est acceptable."""
        if status is None or status == 0:
            return False
        
        # Mode expert : validation stricte
        if self.accepted_status_codes:
            for start, end in self.accepted_status_codes:
                if start <= status <= end:
                    return True
            return False
        
        # Mode simple : logique explicite ⬇️
        if 200 <= status < 300:  # 2xx - Succès
            return True
        if status in [301, 302, 307, 308]:  # Redirections
            return True
        if status in [401, 403]:  # Accès protégé (serveur répond)
            return True
        if status == 404:  # Page non trouvée (serveur UP)
            return True
        
        # Tout le reste = échec (5xx, codes rares, etc.)
        return False

    @property
    def is_up(self) -> bool:
        return self.is_status_accepted(self.last_status_code)
    
    def get_status_message(self) -> str:
        if self.last_error_message:
            return self.last_error_message
        status = self.last_status_code
        if status is None:
            return "Aucune vérification effectuée"
        if status == 0:
            return "Pas de réponse"

        if self.accepted_status_codes is None:
            # Mode simple — messages “utilisateur”
            if 200 <= status < 300:
                return "En ligne"
            if status in (301, 302, 307, 308):
                return "En ligne (redirection)"
            if status in (401, 403):
                return "En ligne (accès protégé)"
            if status == 404:
                return "En ligne (page non trouvée)"
            if status >= 500:
                return f"Problème serveur ({status})"
            return f"En ligne (code {status})"
        
            # Mode expert
        return "Code accepté" if self.is_status_accepted(status) else "Code non accepté"
