from __future__ import annotations
"""
server/app/workers/tasks/heartbeat_tasks.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Tâches périodiques exécutées par Celery pour :

    → recalculer l'état (status) de toutes les machines :
         - UP
         - STALE
         - DOWN
         - NO_DATA

    → détecter les métriques "stale" (absence de données récentes)
      via le service metric_freshness_service.

Ces tâches sont appelées automatiquement via Celery Beat (scheduler),
selon la configuration définie dans workers/scheduler/beat_schedule.py.
"""

from app.workers.celery_app import celery
from app.application.services.machine_status_service import update_all_machine_statuses
from app.application.services.metric_freshness_service import check_metrics_no_data


@celery.task(name="tasks.heartbeat")
def heartbeat_check() -> int:
    """
    Tâche Celery invoquée périodiquement pour recalculer le statut des machines.

    Retourne :
        int : nombre total de machines dont le statut a été mis à jour.

    Cette fonction ne fait rien d'autre que déléguer au service métier.
    """
    # Le scheduler (Beat) appelle cette tâche toutes les 120s.
    # On fixe ici heartbeat_interval=120 pour rester cohérent avec ce rythme.
    return update_all_machine_statuses(heartbeat_interval=120)


@celery.task(name="tasks.check_metrics_no_data")
def check_metrics_no_data_task() -> int:
    """
    Tâche périodique qui vérifie les métriques sans données récentes ("no data").

    S'appuie sur :
      - MetricInstance.updated_at
      - ClientSettings.metric_staleness_seconds (get_effective_metric_staleness_seconds)
      - IncidentRepository pour ouvrir / résoudre les incidents dédiés
      - tasks.notify pour les notifications (et le cooldown)
    """
    return check_metrics_no_data()
