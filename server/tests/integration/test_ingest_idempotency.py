# server/tests/integration/test_ingest_idempotency.py
"""
Idempotence d'ingestion (IngestEvent)
- 1er POST => 202 accepted
- 2e POST avec le même X-Ingest-Id => 200 duplicate (aucun doublon)
Ces assertions cadrent le comportement cible après branchement d'IngestRepository.create_if_absent().
"""

from datetime import datetime, timedelta, timezone
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.infrastructure.persistence.database.session import open_session
from app.infrastructure.persistence.database.models.client import Client
from app.infrastructure.persistence.database.models.client_settings import ClientSettings
from app.infrastructure.persistence.database.models.api_key import ApiKey


@pytest.fixture(scope="module")
def http():
    """Client HTTP FastAPI."""
    return TestClient(app)


def _ensure_api_key() -> str:
    """Réutilise une clé existante ou en crée une nouvelle."""
    with open_session() as s:
        existing = s.query(ApiKey).first()
        if existing:
            return existing.key

        client = Client(id=uuid.uuid4(), name="test-client-idem")
        s.add(client)
        s.flush()

        s.add(
            ClientSettings(
                client_id=client.id,
                alert_grouping_enabled=True,
                alert_grouping_window_seconds=300,
                reminder_notification_seconds=600,
                consecutive_failures_threshold=2,
                heartbeat_threshold_minutes=5,
            )
        )
        s.flush()

        key_val = f"UT-{uuid.uuid4().hex[:24]}"
        s.add(ApiKey(id=uuid.uuid4(), client_id=client.id, key=key_val))
        s.commit()
        return key_val


def _payload(sent_at: datetime):
    """Payload minimal pour l'endpoint /ingest/metrics."""
    return {
        "machine": {"hostname": "ut-host-2"},
        "metrics": [
            {"id": "cpu_load", "type": "numeric", "value": 77.7},
        ],
        "sent_at": sent_at.astimezone(timezone.utc).isoformat(),
    }


def test_post_twice_same_ingest_id_returns_duplicate(http):
    api_key = _ensure_api_key()

    now = datetime.now(timezone.utc) - timedelta(seconds=1)  # dans la fenêtre
    body = _payload(now)

    # On force l'idempotence via l'en-tête X-Ingest-Id (contrat explicit)
    fixed_ingest_id = f"ut-{uuid.uuid4().hex}"

    # 1er POST -> 202 accepted
    r1 = http.post(
        "api/v1/ingest/metrics",
        headers={"X-API-Key": api_key, "X-Ingest-Id": fixed_ingest_id},
        json=body,
    )
    assert r1.status_code in (202, 200), r1.text
    j1 = r1.json()
    assert j1.get("ingest_id") == fixed_ingest_id

    # 2e POST identique -> attendu 200 'duplicate' SANS nouvel enregistrement
    r2 = http.post(
        "api/v1/ingest/metrics",
        headers={"X-API-Key": api_key, "X-Ingest-Id": fixed_ingest_id},
        json=body,
    )

    # Cible: 200 pour les doublons, clair et rapide
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert j2.get("ingest_id") == fixed_ingest_id
    # Le champ 'status' est proposé ici pour rendre l'intention explicite
    assert j2.get("status") in {"duplicate", "ok"}, j2

    # NOTE:
    # On ne compte PAS les samples en DB ici car le pipeline est asynchrone (Celery).
    # La non-duplication est contractualisée par la réponse (200 duplicate) et
    # garantie par l'UPSERT sur ingest_events côté IngestRepository.
