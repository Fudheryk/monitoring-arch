from __future__ import annotations
"""server/app/workers/tasks/evaluation_tasks.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Évaluation périodique (batch par machine).
"""

from sqlalchemy import select

from app.workers.celery_app import celery
from app.infrastructure.persistence.database.session import get_sync_session
from app.infrastructure.persistence.database.models.machine import Machine
from app.application.services.evaluation_service import evaluate_machine


@celery.task(name="tasks.evaluate")
def evaluate_all() -> int:
    """Évalue toutes les machines (retourne le nombre d'évaluations)."""
    n = 0
    with get_sync_session() as session:
        for m in session.scalars(select(Machine)).all():
            n += evaluate_machine(str(m.id))
    return n
