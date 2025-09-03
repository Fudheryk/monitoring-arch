from __future__ import annotations
"""server/app/infrastructure/persistence/repositories/threshold_repository.py
~~~~~~~~~~~~~~~~~~~~~~~~
Repo thresholds.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.infrastructure.persistence.database.models.threshold import Threshold
from app.infrastructure.persistence.database.models.metric import Metric

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.database.models.threshold import Threshold
from app.infrastructure.persistence.database.models.metric import Metric

class ThresholdRepository:
    def __init__(self, session: Session):
        self.s = session

    def for_machine(self, machine_id) -> list[tuple[Threshold, Metric]]:
        q = select(Threshold, Metric).join(Metric, Threshold.metric_id == Metric.id).where(Metric.machine_id == machine_id, Threshold.is_active == True)
        return list(self.s.execute(q).all())
