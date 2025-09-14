from __future__ import annotations
"""server/app/infrastructure/persistence/repositories/metric_repository.py
~~~~~~~~~~~~~~~~~~~~~~~~
Repo metrics.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.infrastructure.persistence.database.models.metric import Metric



class MetricRepository:
    def __init__(self, session: Session):
        self.s = session

    def get_or_create(self, machine_id, name, mtype, unit=None) -> Metric:
        obj = self.s.scalar(select(Metric).where(Metric.machine_id == machine_id, Metric.name == name))
        if obj:
            return obj
        obj = Metric(machine_id=machine_id, name=name, type=mtype, unit=unit)
        self.s.add(obj)
        self.s.flush()
        return obj
