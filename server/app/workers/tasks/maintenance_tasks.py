# server/app/workers/tasks/maintenance_tasks.py

from __future__ import annotations

from celery.utils.log import get_task_logger

from app.workers.celery_app import celery
from app.infrastructure.persistence.database.session import open_session
from app.infrastructure.persistence.repositories.incident_repository import IncidentRepository

logger = get_task_logger(__name__)


@celery.task(name="tasks.auto_resolve_stale_threshold_incidents")
def auto_resolve_stale_threshold_incidents(max_age_hours: int = 24) -> int:
    """
    Tâche périodique : résout les incidents threshold OPEN si la donnée associée
    est stale depuis longtemps (et incident ouvert depuis > max_age_hours).
    """
    with open_session() as s:
        irepo = IncidentRepository(s)
        count = irepo.auto_resolve_stale_threshold_incidents(max_age_hours=max_age_hours)
        s.commit()

    logger.info(
        "auto_resolve_stale_threshold_incidents: resolved=%d max_age_hours=%d",
        count,
        max_age_hours,
    )
    return count
