# server/tests/unit/conftest.py
from __future__ import annotations

import os
import sys
import importlib
import pytest


@pytest.fixture(scope="session", autouse=True)
def _unit_env_session_defaults():
    """
    S'applique uniquement au package 'unit' (car ce conftest est dans server/tests/unit/).
    Pose des ENV sûres AVANT l'import des modules utilisés par ces tests,
    puis recharge Settings si besoin.
    """
    os.environ.setdefault("ENV_FILE", "/dev/null")
    os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("STUB_SLACK", "1")
    os.environ.setdefault("SLACK_WEBHOOK", "http://localhost/dummy-204")
    os.environ.setdefault("ALERT_REMINDER_MINUTES", "1")
    os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "1")
    os.environ.setdefault("INGEST_FUTURE_MAX_SECONDS", "120")       # 2 minutes de marge vers le futur
    os.environ.setdefault("INGEST_LATE_MAX_SECONDS", "31536000")    # 1 an vers le passé

    if "app.core.config" in sys.modules:
        importlib.reload(sys.modules["app.core.config"])
    try:
        import app.infrastructure.persistence.database.session as sess  # type: ignore
        sess._engine = None
        sess._SessionLocal = None
    except Exception:
        pass


@pytest.fixture
def mock_slack(monkeypatch):
    """
    Patch SlackProvider (provider + tasks) pour capturer les envois et forcer le succès.
    """
    events: list[dict] = []

    class _FakeSlackProvider:
        def __init__(self, *a, **kw):  # noqa: ARG002
            pass

        def send(self, **params):
            events.append(params)
            return True

    # Provider
    try:
        prov_mod = importlib.import_module(
            "app.infrastructure.notifications.providers.slack_provider"
        )
        if hasattr(prov_mod, "SlackProvider"):
            monkeypatch.setattr(prov_mod, "SlackProvider", _FakeSlackProvider, raising=True)
    except Exception:
        pass

    # Tasks
    try:
        tasks_mod = importlib.import_module("app.workers.tasks.notification_tasks")
        if hasattr(tasks_mod, "SlackProvider"):
            monkeypatch.setattr(tasks_mod, "SlackProvider", _FakeSlackProvider, raising=True)
    except Exception:
        pass

    return events
