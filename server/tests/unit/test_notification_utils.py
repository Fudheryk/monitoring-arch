# server/tests/unit/test_notification_utils.py
"""
Tests unitaires autour de la construction du payload de notification
et du calcul du délai de rappel (remind minutes).

Notes :
- Marqué @unit => fixtures de server/tests/conftest.py s'appliquent (env par défaut, etc.).
- Imports "tardifs" à l'intérieur des tests pour éviter des effets de bord
  lors de l'import de modules (ex: Celery, configuration).
"""

import uuid
import pytest

# Tous les tests de ce fichier sont unitaires
pytestmark = pytest.mark.unit


def test_get_remind_minutes_override():
    # Import tardif (après mise en place des fixtures autouse unit)
    from app.workers.tasks.notification_tasks import get_remind_minutes

    # Valeur explicite => renvoyée telle quelle
    assert get_remind_minutes(3) == 3

    # 0 / None => doit retomber sur une valeur minimale (>= 1)
    assert get_remind_minutes(0) >= 1
    assert get_remind_minutes(None) >= 1


def test_payload_validation_ok():
    # Import tardif
    from app.workers.tasks.notification_tasks import NotificationPayload

    p = NotificationPayload(
        title="t",
        text="x",
        severity="warning",
        client_id=uuid.UUID(int=0),
    )
    assert p.severity == "warning"
    # On vérifie aussi que les champs obligatoires sont bien présents
    assert p.title and p.text and p.client_id


def test_payload_severity_invalid():
    # Import tardif
    from app.workers.tasks.notification_tasks import NotificationPayload

    with pytest.raises(ValueError):
        NotificationPayload(
            title="t",
            text="x",
            severity="bad",  # valeur invalide
            client_id=uuid.UUID(int=0),
        )
