from __future__ import annotations
"""server/app/infrastructure/persistence/database/models/metric_definitions.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Modèle SQLAlchemy pour la table `metric_definitions`.

But :
- Définir le "catalogue" global des métriques connues (builtin, plugins…)
- Ne plus dépendre du modèle Metric (ancien), pour éviter les imports circulaires
  et coller à la nouvelle architecture basée sur MetricInstance.
"""

import uuid
import datetime as dt

from sqlalchemy import (
    Boolean,
    String,
    Text,
    DateTime,
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.database.base import Base


def _utcnow() -> dt.datetime:
    """Helper UTC (default + onupdate)."""
    return dt.datetime.now(dt.timezone.utc)


class MetricDefinitions(Base):
    __tablename__ = "metric_definitions"

    # ----------------------------------------------------------------------
    # Identité
    # ----------------------------------------------------------------------
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Nom logique de la métrique (ex: "cpu.usage_percent", "disk[/].usage_percent", ...)
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    # ----------------------------------------------------------------------
    # Définition "globale" de la métrique
    # ----------------------------------------------------------------------
    # Type logique de la métrique.
    #
    # On redéfinit ici l'ENUM explicitement pour ne plus dépendre du modèle Metric :
    #   - "numeric"
    #   - "boolean"
    #   - "string"
    #
    # Le name="metric_type" doit correspondre à celui utilisé dans la migration
    # existante pour rester compatible avec la BDD.
    type: Mapped[str] = mapped_column(
        SAEnum(
            "numeric",
            "boolean",
            "string",
            name="metric_type",
            create_type=False,  # on suppose que le type existe déjà en BDD
        ),
        nullable=False,
    )

    # Groupe logique (system, http, disk, security, docker, ...)
    group_name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="misc",
    )

    # Description humaine de la métrique
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Vendor d’origine (builtin, agent, plugin, ...)
    vendor: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="builtin",
    )

    # Indique si la métrique est suggérée comme critique par défaut
    is_suggested_critical: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    # Opérateur de seuil par défaut (gt, lt, eq, ne, contains, ...),
    # utilisé comme suggestion pour l'UI / l'onboarding.
    default_condition: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )

    # Familles dynamiques (disk[<mountpoint>].*, service.<unit>.service, network.<iface>.*, temperature.coretemp.<number>.* ...)
    is_dynamic_family: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    # Nom de la "dimension" dynamique (ex: "mountpoint", "unit", "iface", "number", ... )
    dynamic_dimension: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    # ----------------------------------------------------------------------
    # Métadonnées temporelles (optionnelles mais pratiques)
    # ----------------------------------------------------------------------
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )

    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )
