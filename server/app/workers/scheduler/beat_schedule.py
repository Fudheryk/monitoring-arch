from __future__ import annotations
"""server/app/workers/scheduler/beat_schedule.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Planification périodique des tâches Celery (Beat).
"""

beat_schedule = {
    "evaluate-metrics-every-60s": {
        "task": "tasks.evaluate",
        "schedule": 60.0,
    },
    "check-heartbeats-every-120s": {
        "task": "tasks.heartbeat",
        "schedule": 120.0,
    },
    "check-http-targets-every-300s": {
        "task": "tasks.http",
        "schedule": 300.0,
    },
}
