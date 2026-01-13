# server/tests/integration/test_outbox_flow.py
"""
Outbox (fondations) – flux minimal
- Insère un event "normal" -> la task le livre et marque DELIVERED (attempts=1)
- Insère un event avec _force_fail -> la task programme un RETRY (status=PENDING, attempts=1, next_attempt_at > now)

⚠️ Important : on doit utiliser un client_id **existant** (FK vers clients.id).
Ce test essaie d'abord de récupérer un client seedé (0002). S'il n'y en a pas,
il en crée un pour l'environnement courant.
"""

from datetime import datetime, timezone, timedelta
import uuid

from sqlalchemy import select

from app.workers.tasks.outbox_tasks import deliver_outbox_batch
from app.infrastructure.persistence.database.session import open_session
from app.infrastructure.persistence.repositories.outbox_repository import OutboxRepository
from app.infrastructure.persistence.database.models.outbox_event import (
    OutboxEvent,
    OutboxStatus,
)
from app.infrastructure.persistence.database.models.client import Client


# ──────────────────────────────────────────────────────────────────────────────
# Helpers DB
# ──────────────────────────────────────────────────────────────────────────────

def _ensure_client_id() -> str:
    """Retourne l'id d'un client existant, sinon en crée un rapidement pour le test."""
    with open_session() as s:
        existing = s.execute(select(Client.id).limit(1)).scalar_one_or_none()
        if existing:
            return str(existing)
        # Aucun client seedé ? On en crée un pour le test d'intégration.
        c = Client(id=uuid.uuid4(), name="Test Client (outbox)")
        s.add(c)
        s.commit()
        return str(c.id)


def _clear_outbox():
    with open_session() as s:
        s.query(OutboxEvent).delete()
        s.commit()


def _insert_event(type_: str, payload: dict, *, client_id: str | None = None, incident_id: str | None = None):
    """
    Insère un événement Outbox dû immédiatement.
    - client_id : si None, on prend un client valide via _ensure_client_id().
    """
    if client_id is None:
        client_id = _ensure_client_id()

    with open_session() as s:
        repo = OutboxRepository(s)
        evt = repo.insert(
            type_=type_,
            payload=payload,
            client_id=client_id,
            incident_id=incident_id,
            next_attempt_at=None,  # due immédiatement
        )
        return str(evt.id)


def _get(event_id: str) -> OutboxEvent:
    with open_session() as s:
        return s.get(OutboxEvent, event_id)


# ──────────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────────

def test_outbox_delivers_and_marks_delivered():
    _clear_outbox()
    evt_id = _insert_event(
        "IncidentRaised",
        {"subject": "Test delivery OK", "message": "hello world"},
        # client_id omis volontairement -> _ensure_client_id() s'en charge
    )

    delivered = deliver_outbox_batch()
    assert delivered == 1  # la task renvoie le nombre livrés dans ce batch

    row = _get(evt_id)
    assert row is not None
    assert row.status == OutboxStatus.DELIVERED
    assert row.attempts == 1
    assert isinstance(row.delivery_receipt, dict)
    assert row.delivery_receipt.get("ok") is True


def test_outbox_retry_schedules_next_attempt_and_stays_pending():
    _clear_outbox()
    evt_id = _insert_event(
        "IncidentRaised",
        {"subject": "Test delivery FAIL", "_force_fail": True},
        # client_id omis volontairement -> _ensure_client_id() s'en charge
    )

    delivered = deliver_outbox_batch()
    assert delivered == 0  # échec simulé -> pas de "delivered" dans ce batch

    row = _get(evt_id)
    assert row is not None
    # Après un échec : status revient à PENDING (planifié), attempts a été incrémenté à 1
    assert row.status == OutboxStatus.PENDING
    assert row.attempts == 1

    # La prochaine tentative doit être dans le futur (backoff + jitter)
    now = datetime.now(timezone.utc)
    assert row.next_attempt_at > now

    # Sans fixer exactement le backoff (configurable), on peut vérifier un minimum raisonnable
    # (évite de dépendre du détail des OUTBOX_BACKOFFS) :
    assert row.next_attempt_at - now > timedelta(seconds=5)
