from __future__ import annotations
"""app/workers/celery_app.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Celery app + routage + auto-import des modules de tâches + beat schedule.
"""
from celery import Celery

from app.core.config import settings
from celery.schedules import crontab
from app.core.config import settings

celery = Celery("monitoring", broker=settings.REDIS_URL, backend=settings.REDIS_URL)

# Routage par files
celery.conf.task_routes = {
    "tasks.ingest": {"queue": "ingest"},
    "tasks.evaluate": {"queue": "evaluate"},
    "tasks.heartbeat": {"queue": "heartbeat"},
    "tasks.http": {"queue": "http"},
    "tasks.notify": {
        "queue": "notify",
        "rate_limit": "10/m"  # Limite à 10 notifications/minute
    },
}

# Configuration des retries pour les notifications
celery.conf.task_default_retry_delay = 30  # 30 secondes
celery.conf.task_max_retries = 3

# Configuration Beat pour les notifications récurrentes
celery.conf.beat_schedule = {
    'daily-summary': {
        'task': 'tasks.notify',
        'schedule': crontab(hour=8, minute=0),
        'kwargs': {
            "title": "Rapport quotidien",
            "text": "Vérification des indicateurs système", 
            "severity": "info",
            "channel": "#daily-reports"
        }
    },
}

celery.conf.update(
    imports=[
        "app.workers.tasks.ingest_tasks",
        "app.workers.tasks.evaluation_tasks",
        "app.workers.tasks.heartbeat_tasks",
        "app.workers.tasks.http_monitoring_tasks",
        "app.workers.tasks.notification_tasks",
    ],
)

# (optionnel) Beat schedule si tu l’as en Python
try:
    from app.workers.scheduler.beat_schedule import beat_schedule
    celery.conf.beat_schedule = beat_schedule
except Exception:
    pass


