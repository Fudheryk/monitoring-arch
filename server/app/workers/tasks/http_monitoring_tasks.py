from __future__ import annotations
"""server/app/workers/tasks/http_monitoring_tasks.py
~~~~~~~~~~~~~~~~~~~~~~~~
Monitoring HTTP.
"""
from app.workers.celery_app import celery
from app.application.services.http_monitor_service import check_http_targets

# Task périodique (déjà en place chez toi)
@celery.task(name="tasks.http")
def http_checks() -> int:
    return check_http_targets()

# ✅ Task optionnelle pour une seule cible (utile pour debug/manuel)
try:
    from app.application.services.http_monitor_service import check_one_target
except Exception:
    check_one_target = None  # si la fonction n'existe pas encore

@celery.task(name="tasks.http_one")
def http_check_one(target_id: str) -> dict:
    if check_one_target is None:
        return {"checked": False, "reason": "not_implemented"}
    return check_one_target(target_id)
