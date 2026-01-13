from __future__ import annotations
"""
server/app/workers/celery_app.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Instance Celery + configuration des files + routage + auto-import des tasks.

Objectifs :
- Déclarer explicitement les files (ingest/evaluate/heartbeat/http/notify/outbox).
- Router les tâches nommées vers la bonne file.
- Supporter le mode "eager" (tests) sans broker.
- Importer explicitement les modules de tâches pour l'enregistrement.

Remarques d'exploitation :
- Le worker doit écouter les files déclarées. Avec docker-compose, conserve
  l’option :  -Q ingest,evaluate,heartbeat,http,notify,outbox
  (ainsi pas de consommation par défaut de la file 'celery' uniquement).
"""

import os

from app.core.logging import setup_logging

setup_logging()

from celery import Celery
from kombu import Queue

# -----------------------------------------------------------------------------
# Broker / backend :
# 1) on respecte d'abord les variables d'env CELERY_BROKER_URL / CELERY_RESULT_BACKEND
# 2) sinon fallback sur settings.REDIS_URL (config applicative)
# -----------------------------------------------------------------------------
try:
    from app.core.config import settings
    _REDIS = getattr(settings, "REDIS_URL", os.getenv("REDIS_URL", "redis://redis:6379/0"))
except Exception:
    _REDIS = os.getenv("REDIS_URL", "redis://redis:6379/0")

BROKER_URL = os.getenv("CELERY_BROKER_URL", _REDIS)
RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", _REDIS)

# -----------------------------------------------------------------------------
# Celery app
# -----------------------------------------------------------------------------
celery = Celery("monitoring", broker=BROKER_URL, backend=RESULT_BACKEND)

# -----------------------------------------------------------------------------
# Configuration Celery
# -----------------------------------------------------------------------------
celery.conf.update(
    # Sérialisation
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,

    # Comportement des tâches
    task_default_retry_delay=30,   # 30s entre retries par défaut (si utilisé)
    # ack après exécution : OK si tâches idempotentes (sinon risque de double-exécution)
    task_acks_late=True,
    task_ignore_result=True,       # pas de stockage de résultat par défaut
    # (optionnel mais recommandé) si tu utilises task_acks_late, ça évite des rejets
    # si le worker meurt au milieu (à adapter selon ton usage)
    task_reject_on_worker_lost=True,

    # Import explicite des modules de tâches (enregistrement garanti)
    imports=[
        "app.workers.tasks.ingest_tasks",
        "app.workers.tasks.evaluation_tasks",
        "app.workers.tasks.heartbeat_tasks",
        "app.workers.tasks.http_monitoring_tasks",
        "app.workers.tasks.notification_tasks",
        "app.workers.tasks.outbox_tasks",
        "app.workers.tasks.maintenance_tasks",
    ],

    # Déclaration explicite des files (queues) utilisées par l'app
    task_queues=(
        Queue("ingest"),
        Queue("evaluate"),
        Queue("heartbeat"),
        Queue("http"),
        Queue("notify"),
    ),
    # File par défaut si non routée explicitement.
    # NOTE: si tu veux repérer immédiatement les tasks non routées / typos,
    # tu peux mettre "celery" ici et NE PAS faire écouter cette queue par défaut.
    # task_default_queue="celery",
    task_default_queue="ingest",
    task_create_missing_queues=True,  # ceinture+bretelles : crée la file si absente

    # Routage : mappe les noms de tâches vers les files adéquates
    task_routes={
        "tasks.ingest":    {"queue": "ingest"},
        "tasks.evaluate":  {"queue": "evaluate"},
        "tasks.heartbeat": {"queue": "heartbeat"},
        "tasks.check_metrics_no_data": {"queue": "ingest"},
        "tasks.http":      {"queue": "http"},
        "tasks.notify":    {"queue": "notify", "rate_limit": "10/m"},
        "tasks.incident_reminders": {"queue": "notify"},
        "tasks.notify_incident_reminders_for_client": {"queue": "notify"},
        "tasks.grouped_reminders": {"queue": "notify"},
        "tasks.notify_grouped_reminder": {"queue": "notify"},
        "tasks.auto_resolve_stale_threshold_incidents": {"queue": "evaluate"},
        "outbox.deliver":  {"queue": "outbox"},
    },
)

# Réglages worker raisonnables (limite le "buffering" côté worker)
celery.conf.worker_prefetch_multiplier = 1  # consomme 1 message à la fois

# -----------------------------------------------------------------------------
# Beat schedule (optionnel) : soit import dédié, soit fallback vide
# -----------------------------------------------------------------------------
try:
    from app.workers.scheduler.beat_schedule import beat_schedule  # type: ignore
    celery.conf.beat_schedule = beat_schedule
except Exception:
    # Fallback uniquement si l'import du schedule échoue.
    celery.conf.beat_schedule = {}

# -----------------------------------------------------------------------------
# Mode "eager" (utile en tests sans broker/worker)
# - CELERY_TASK_ALWAYS_EAGER=1 => .delay() / .apply_async() s'exécutent inline
# - NOTE : send_task() ignore ce mode (éviter send_task en tests)
# -----------------------------------------------------------------------------
_eager_flag = os.getenv("CELERY_TASK_ALWAYS_EAGER", "").strip().lower() in {"1", "true", "yes", "on"}
if _eager_flag:
    celery.conf.update(
        task_always_eager=True,
        task_eager_propagates=True,  # remonter les exceptions côté test
    )
    # Si on n'a pas explicitement pointé ailleurs, basculer en mémoire
    if BROKER_URL == _REDIS:
        celery.conf.update(broker_url="memory://")
    if RESULT_BACKEND == _REDIS:
        celery.conf.update(result_backend="cache+memory://")

# -----------------------------------------------------------------------------
# Alias de compatibilité
# -----------------------------------------------------------------------------
app = celery
celery_app = celery

# Désactive le hijack du root logger par Celery (on gère le logging nous-même)
celery_app.conf.worker_hijack_root_logger = False

__all__ = ["celery", "app", "celery_app"]
