from __future__ import annotations
"""server/app/workers/tasks/http_monitoring_tasks.py
~~~~~~~~~~~~~~~~~~~~~~~~
Monitoring HTTP.
"""
from app.workers.celery_app import celery
from app.application.services.http_monitor_service import (
    check_http_targets,
    check_one_target,
)

# Task périodique (déjà en place chez toi)
@celery.task(name="tasks.http")
def http_checks() -> int:
    return check_http_targets()


@celery.task(name="tasks.http_one")
def http_check_one(target_id: str) -> dict:
    """
    Task debug/manuel : check d'une seule cible HTTP.
    Retourne toujours un dict "stable" (pas d'exception silencieuse).
    """
    if not target_id:
        return {"checked": False, "reason": "missing_target_id"}

    # Normalisation early : évite de faire planter la task sur un id invalide
    try:
        uuid.UUID(str(target_id))
    except Exception:
        return {"checked": False, "reason": "bad_id"}

    return check_one_target(target_id)