# server/app/workers/tasks/maintenance_tasks.py
from __future__ import annotations

from celery.utils.log import get_task_logger
from sqlalchemy import text

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


@celery.task(name="maintenance.purge_samples")
def purge_samples(retention_minutes: int = 120, batch_size: int = 200_000) -> int:
    """
    Purge les samples plus vieux que retention_minutes.
    - batched delete pour limiter la pression IO/locks
    - ANALYZE à la fin (pas de VACUUM FULL ici)
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=retention_minutes)
    total = 0

    with open_session() as s:
        while True:
            # Batch delete via CTID (rapide, évite un énorme DELETE unique)
            r = s.execute(
                text("""
                    WITH doomed AS (
                      SELECT ctid
                      FROM samples
                      WHERE ts < :cutoff
                      LIMIT :limit
                    )
                    DELETE FROM samples
                    WHERE ctid IN (SELECT ctid FROM doomed)
                    RETURNING 1
                """),
                {"cutoff": cutoff, "limit": batch_size},
            )
            deleted = r.rowcount or 0
            s.commit()

            total += deleted
            if deleted == 0:
                break

        s.execute(text("ANALYZE samples"))
        s.commit()

    logger.info("purge_samples: deleted=%d retention_minutes=%d batch_size=%d", total, retention_minutes, batch_size)
    return total