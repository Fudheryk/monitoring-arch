# server/tests/unit/conftest.py
# ─────────────────────────────────────────────────────────────────────────────
# Conftest pour les TESTS UNITAIRES.
#
# Objectifs :
# - Poser les ENV *avant* les imports app.* pour que Settings() voie un webhook
#   et un cooldown court.
# - Fournir la fixture `mock_slack` qui PATCH la classe SlackProvider là où
#   elle est réellement utilisée (dans notification_tasks) afin de :
#       • renvoyer True (pas de retry Celery),
#       • enregistrer chaque "envoi" dans une liste (compteur de test).
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import os
import sys
import importlib
import pytest


def pytest_configure(config) -> None:
    """
    S'exécute avant la collecte → parfait pour poser les ENV lues par Settings().
    """
    os.environ.setdefault("STUB_SLACK", "1")
    os.environ.setdefault("SLACK_WEBHOOK", "http://localhost/dummy-204")
    os.environ.setdefault("ALERT_REMINDER_MINUTES", "1")
    os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "1")

    # Si app.core.config a déjà été importé par un autre test, on le recharge
    # pour ré-instancier Settings() avec ces ENV.
    if "app.core.config" in sys.modules:
        importlib.reload(sys.modules["app.core.config"])


@pytest.fixture
def mock_slack(monkeypatch):
    """
    Patch la classe SlackProvider pour:
      - enregistrer les appels (dans `events`)
      - renvoyer True (succès) → pas de retry Celery

    NOTE IMPORTANTE :
    La tâche importe souvent `SlackProvider` DANS `notification_tasks`.
    On patch donc *deux* emplacements possibles:
      1) le module provider source (par prudence)
      2) le module des tâches (là où la classe est référencée au runtime)
    """
    events: list[dict] = []

    class _FakeSlackProvider:
        def __init__(self, *a, **kw):
            pass

        def send(self, **params):
            # on enregistre ce qui est envoyé pour les assertions
            events.append(params)
            return True  # succès → pas de retry

    # 1) Patch côté provider (au cas où d'autres chemins l'utilisent)
    try:
        prov_mod = importlib.import_module(
            "app.infrastructure.notifications.providers.slack_provider"
        )
        if hasattr(prov_mod, "SlackProvider"):
            monkeypatch.setattr(prov_mod, "SlackProvider", _FakeSlackProvider, raising=True)
    except Exception:
        pass

    # 2) Patch côté module des tâches (référence réellement utilisée)
    try:
        tasks_mod = importlib.import_module("app.workers.tasks.notification_tasks")
        if hasattr(tasks_mod, "SlackProvider"):
            monkeypatch.setattr(tasks_mod, "SlackProvider", _FakeSlackProvider, raising=True)
    except Exception:
        # Si le module n'est pas encore importé, il sera importé après et
        # utilisera notre ENV (SLACK_WEBHOOK) ; le test appelle .run() qui
        # passera par la classe patchée ci-dessus si déjà importée.
        pass

    return events
