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


# -----------------------------------------------------------------------------
# NEW: purge des samples (ne garder que les dernières ingestions)
# -----------------------------------------------------------------------------
@celery.task(name="maintenance.purge_samples")
def purge_samples_task(keep_minutes: int = 120, batch_size: int = 50_000) -> int:
    """
    Purge `samples` en production sans refonte DB :
    - conserve uniquement les N dernières minutes (default: 120 min)
    - supprime en batches pour éviter les gros locks et transactions longues
    - fait un ANALYZE pour remettre les stats du planner d’aplomb

    NOTE:
    - On ne fait PAS de VACUUM FULL ici (trop intrusif). À faire manuellement si besoin.
    """
    keep_minutes = int(keep_minutes)
    batch_size = int(batch_size)
    if keep_minutes <= 0:
        raise ValueError("keep_minutes must be > 0")
    if batch_size <= 0:
        raise ValueError("batch_size must be > 0")

    total_deleted = 0

    with open_session() as s:
        while True:
            res = s.execute(
                text(
                    """
                    WITH doomed AS (
                      SELECT metric_instance_id, ts, seq
                      FROM samples
                      WHERE ts < now() - (:keep_minutes || ' minutes')::interval
                      LIMIT :batch_size
                    )
                    DELETE FROM samples s
                    USING doomed d
                    WHERE s.metric_instance_id = d.metric_instance_id
                      AND s.ts = d.ts
                      AND s.seq = d.seq
                    RETURNING 1;
                    """
                ),
                {"keep_minutes": keep_minutes, "batch_size": batch_size},
            )

            deleted = len(res.fetchall())
            if deleted == 0:
                break

            total_deleted += deleted
            s.commit()

        # stats planner
        s.execute(text("ANALYZE samples;"))
        s.commit()

    logger.info(
        "purge_samples_task: deleted=%d keep_minutes=%d batch_size=%d",
        total_deleted,
        keep_minutes,
        batch_size,
    )
    return total_deleted
