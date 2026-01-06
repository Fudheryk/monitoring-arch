# server/app/infrastructure/persistence/database/models/metric_instance.py

from __future__ import annotations

import uuid
import datetime as dt

from sqlalchemy import (
    Boolean,
    String,
    Text,
    DateTime,
    ForeignKey,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.database.base import Base  # même import que metric_definitions


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class MetricInstance(Base):
    __tablename__ = "metric_instances"

    __table_args__ = (
        # Ne pas avoir deux fois la même (definition_id, dimension_value) sur une machine
        UniqueConstraint(
            "machine_id",
            "definition_id",
            "dimension_value",
            name="uq_metric_instance",
        ),
        # Ne pas avoir deux fois le même name_effective sur une machine
        Index(
            "ix_metric_instances_machine_name_effective",
            "machine_id",
            "name_effective",
            unique=True,
        ),
    )

    # ------------------------------------------------------------------ #
    # Identité / FK
    # ------------------------------------------------------------------ #
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    machine_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("machines.id", ondelete="CASCADE"),
        nullable=False,
    )

    definition_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("metric_definitions.id", ondelete="CASCADE"),
        nullable=True,
    )

    # ------------------------------------------------------------------ #
    # Identification de la métrique
    # ------------------------------------------------------------------ #

    # Nom réel reçu du payload (ex: "network.enp0s3.bytes_sent")
    name_effective: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Valeur de dimension (iface, mountpoint, service_name, …)
    # Pour les non dynamiques : chaîne vide par défaut
    dimension_value: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="",
        server_default="",
    )

    # ------------------------------------------------------------------ #
    # Etat d’alerting
    # ------------------------------------------------------------------ #
    is_alerting_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("FALSE"),
    )

    # Règle métier : cette métrique nécessite (ou non) un seuil
    needs_threshold: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("TRUE"),
    )

    is_paused: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("FALSE"),
    )

    # ------------------------------------------------------------------ #
    # Valeurs
    # ------------------------------------------------------------------ #
    baseline_value: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    last_value: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # ------------------------------------------------------------------ #
    # Timestamps
    # ------------------------------------------------------------------ #
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
