# server/app/infrastructure/persistence/repositories/metric_definitions_repository.py
from __future__ import annotations

from typing import Optional, Iterable, Dict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.database.models.metric_definitions import MetricDefinitions


class MetricDefinitionsRepository:
    """
    Accès au catalogue de métriques (metric_definitions).

    Principalement utilisé pour :
      - récupérer les métadonnées builtin lors de l’onboarding (baseline_service)
      - éventuellement exposer la liste des métriques supportées dans l’UI.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_name_and_vendor(self, name: str, vendor: str = "builtin") -> Optional[MetricDefinitions]:
        stmt = (
            select(MetricDefinitions)
            .where(
                MetricDefinitions.name == name,
                MetricDefinitions.vendor == vendor,
            )
            .limit(1)
        )
        return self.session.scalar(stmt)

    def get_all_by_vendor(self, vendor: str = "builtin") -> Iterable[MetricDefinitions]:
        stmt = select(MetricDefinitions).where(MetricDefinitions.vendor == vendor)
        return self.session.scalars(stmt).all()

    def get_index_by_name(self, vendor: str = "builtin") -> Dict[str, MetricDefinitions]:
        """
        Retourne un mapping {name: MetricDefinitions} pour un vendor donné.
        Utile si on veut faire des résolutions dynamiques en mémoire.
        """
        return {
            m.name: m
            for m in self.get_all_by_vendor(vendor=vendor)
        }
