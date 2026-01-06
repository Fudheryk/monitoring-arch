from __future__ import annotations

"""
server/app/infrastructure/persistence/repositories/metric_instances_repository.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Repository pour la table metric_instances.

Objectif :
- Fournir un get_or_create(...) idempotent.
- Identifier une instance par (machine_id, definition_id, dimension_value).
- Garder name_effective comme trace du nom réel reçu dans le payload.
"""

from typing import Optional
from uuid import UUID

from sqlalchemy import select, and_ 
from sqlalchemy.orm import Session

from app.infrastructure.persistence.database.models.metric_instance import MetricInstance
from app.infrastructure.persistence.database.models.metric_definitions import MetricDefinitions


def _as_uuid(v):
    if isinstance(v, UUID):
        return v
    if isinstance(v, str):
        from uuid import UUID as _UUID
        try:
            return _UUID(v)
        except Exception:
            return v
    return v


class MetricInstancesRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    # ------------------------------------------------------------------
    # get_or_create par (machine, definition, dimension)
    # ------------------------------------------------------------------
    def get_or_create(
        self,
        *,
        machine_id,
        definition: MetricDefinitions | None,
        name_effective: str,
        dimension_value: Optional[str] = "",
    ) -> MetricInstance:
        """
        Renvoie la MetricInstance pour une métrique donnée.

        - Si `definition` est NON-NULL (métrique du catalogue) :
            clé logique = (machine_id, definition_id, dimension_value)
        - Si `definition` est NULL (métrique custom / vendor tiers) :
            clé logique = (machine_id, name_effective, dimension_value)

        Dans les deux cas, on garde `name_effective` comme nom réel reçu.
        """

        mid = _as_uuid(machine_id)
        dim = (dimension_value or "").strip()

        if definition is not None:
            # ───────── Cas 1 : métrique du catalogue ─────────
            did = definition.id
            stmt = select(MetricInstance).where(
                MetricInstance.machine_id == mid,
                MetricInstance.definition_id == did,
                MetricInstance.dimension_value == dim,
            )
        else:
            # ───────── Cas 2 : métrique custom / vendor tiers ─────────
            did = None
            stmt = select(MetricInstance).where(
                MetricInstance.machine_id == mid,
                MetricInstance.definition_id.is_(None),
                MetricInstance.name_effective == name_effective,
                MetricInstance.dimension_value == dim,
            )

        obj = self.s.scalars(stmt).first()
        if obj:
            # Si on a trouvé via la clé (machine, definition, dimension),
            # on s'assure que name_effective reflète le nom reçu (utile pour debug/UI).
            if obj.name_effective != name_effective:
                obj.name_effective = name_effective
            return obj

        # Création
        obj = MetricInstance(
            machine_id=mid,
            definition_id=did,
            name_effective=name_effective,
            dimension_value=dim,
        )
        self.s.add(obj)
        self.s.flush()
        return obj

    # ------------------------------------------------------------------
    # Helpers de lecture
    # ------------------------------------------------------------------

    def list_for_machine(self, machine_id) -> list[MetricInstance]:
        mid = _as_uuid(machine_id)
        return list(
            self.s.scalars(
                select(MetricInstance).where(
                    MetricInstance.machine_id == mid
                ).order_by(MetricInstance.name_effective)
            ).all()
        )

    def get_by_id(self, metric_instance_id) -> Optional[MetricInstance]:
        mid = _as_uuid(metric_instance_id)
        return self.s.get(MetricInstance, mid)
