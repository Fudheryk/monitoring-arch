# server/tests/unit/test_notify_alert_cooldown.py
"""
Vérifie qu'une alerte est envoyée une première fois puis ignorée immédiatement après
(grâce au cooldown), en mode unitaire (DB SQLite in-memory + Slack mocké).

Notes :
- Marqué @unit => active les fixtures unitaires de server/tests/conftest.py
  (DB SQLite partagée, Celery en eager, SlackProvider mocké, patch open_session, etc.).
- Imports "tardifs" à l'intérieur du test pour garantir que les patches sont en place.
"""

import uuid
import pytest

# Tous les tests de ce fichier sont unitaires
pytestmark = pytest.mark.unit


def test_notify_alert_send_then_skip(Session, mock_slack):
    # Arrange: crée une alerte minimale en base
    # Import tardif pour éviter tout side-effect avant la mise en place des fixtures
    from app.infrastructure.persistence.database.models.alert import Alert

    with Session() as s:
        a = Alert(
            id=uuid.uuid4(),
            threshold_id=uuid.uuid4(),
            machine_id=uuid.uuid4(),
            metric_id=None,
            status="FIRING",
            severity="warning",
            current_value="2.8",
            message="cpu high",
        )
        s.add(a)
        s.commit()
        alert_id = str(a.id)

    # Act: appeler le corps de la tâche pour rester in-process
    # (import tardif pour que les patches de conftest soient déjà en place)
    from app.workers.tasks.notification_tasks import notify_alert

    # 1er appel => envoi Slack attendu
    notify_alert.run(alert_id, remind_after_minutes=1)
    first_count = len(mock_slack)
    assert first_count == 1, "Le premier envoi Slack aurait dû avoir lieu"

    # Re-appel immédiat => cooldown => aucun nouvel envoi
    notify_alert.run(alert_id, remind_after_minutes=1)
    assert len(mock_slack) == first_count, (
        "Un deuxième envoi Slack ne devait pas avoir lieu (cooldown)"
    )
