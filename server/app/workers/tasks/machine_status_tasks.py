from __future__ import annotations
"""
server/app/workers/tasks/machine_status_tasks.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Tâche Celery dédiée au recalcul du statut machine
(exécutée toutes les 30 secondes par beat).
"""

from app.workers.celery_app import celery
from app.application.services.machine_status_service import update_all_machine_statuses


@celery.task(name="tasks.machine_status")
def machine_status_check() -> int:
    """
    Recalcule les statuts machines.

    Ne renvoie qu’un entier : le nombre de machines
    dont le statut a changé (utile pour debug).
    """
    return update_all_machine_statuses(heartbeat_interval=60)
