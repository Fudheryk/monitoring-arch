from __future__ import annotations

"""
server/app/infrastructure/persistence/repositories/threshold_new_repository.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Repository pour la nouvelle table thresholds_new, liée à metric_instances.

Rôle :
- Récupérer / lister les seuils par metric_instance.
- Fournir les thresholds à évaluer pour une machine, via metric_instances.
"""

from uuid import UUID
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.database.models.threshold_new import ThresholdNew
from app.infrastructure.persistence.database.models.metric_instance import MetricInstance


def _as_uuid(v):
    if isinstance(v, UUID):
        return v
    if isinstance(v, str):
        try:
            return UUID(v)
        except Exception:
            return v
    return v


class ThresholdNewRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    # --------------------------------------------------------------
    # Requêtes
    # --------------------------------------------------------------

    def list_by_metric_instance(self, metric_instance_id) -> list[ThresholdNew]:
        """Liste tous les seuils d'une metric_instance donnée."""
        mid = _as_uuid(metric_instance_id)
        return list(
            self.s.scalars(
                select(ThresholdNew)
                .where(ThresholdNew.metric_instance_id == mid)
                .order_by(ThresholdNew.name)
            ).all()
        )

    def get_default(self, metric_instance_id) -> Optional[ThresholdNew]:
        """Récupère le seuil nommé 'default' pour une metric_instance donnée."""
        mid = _as_uuid(metric_instance_id)
        return self.s.scalars(
            select(ThresholdNew).where(
                ThresholdNew.metric_instance_id == mid,
                ThresholdNew.name == "default",
            )
        ).first()

    def for_machine(self, machine_id) -> list[tuple[ThresholdNew, MetricInstance]]:
        """
        Retourne les thresholds_new à ÉVALUER pour une machine.

        Règle :
        - uniquement les thresholds actifs : is_active = TRUE
        """
        mid = _as_uuid(machine_id)
        q = (
            select(ThresholdNew, MetricInstance)
            .join(MetricInstance, ThresholdNew.metric_instance_id == MetricInstance.id)
            .where(
                MetricInstance.machine_id == mid,
                ThresholdNew.is_active.is_(True),
            )
        )
        return list(self.s.execute(q).all())

    # --------------------------------------------------------------
    # Mutations
    # --------------------------------------------------------------

    def add(self, t: ThresholdNew) -> ThresholdNew:
        self.s.add(t)
        return t

    def update_fields(self, t: ThresholdNew, **fields) -> ThresholdNew:
        for k, v in fields.items():
            setattr(t, k, v)
        return t
