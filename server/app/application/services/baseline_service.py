from __future__ import annotations
"""server/app/application/services/baseline_service.py
~~~~~~~~~~~~~~~~~~~~~~~~
Baseline initiale si première fois.
"""
from app.infrastructure.persistence.database.session import get_sync_session
from app.infrastructure.persistence.repositories.metric_repository import MetricRepository

from app.infrastructure.persistence.database.session import get_sync_session
from app.infrastructure.persistence.repositories.metric_repository import MetricRepository

def init_if_first_seen(machine, metrics_inputs) -> None:
    with get_sync_session() as session:
        mrepo = MetricRepository(session)
        for mi in metrics_inputs:
            metric = mrepo.get_or_create(machine.id, mi.name, mi.type, mi.unit)
            if metric.baseline_value is None:
                metric.baseline_value = str(mi.value)
        session.commit()
