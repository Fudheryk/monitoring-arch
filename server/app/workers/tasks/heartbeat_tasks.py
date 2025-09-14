from __future__ import annotations
"""server/app/workers/tasks/heartbeat_tasks.py
~~~~~~~~~~~~~~~~~~~~~~~~
Heartbeat no-data.
"""
from app.workers.celery_app import celery
from app.application.services.heartbeat_service import check_offline


@celery.task(name="tasks.heartbeat")
def heartbeat_check() -> int:
    return check_offline()
