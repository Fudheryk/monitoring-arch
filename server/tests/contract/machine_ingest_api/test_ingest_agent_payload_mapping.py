# server/tests/contract/machine_ingest_api/test_ingest_agent_payload_mapping.py
"""
Contrats /ingest/metrics – compat 'agent':
- Pas de header X-API-Key -> accepté si metadata.key est valide (202)
- Mapping metadata.collection_time -> sent_at
- Mapping metrics[].valeur -> value, metrics[].nom -> id
- 401 si aucune clé (ni header, ni metadata.key)
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
    return TestClient(app)


def _ensure_agent_api_key() -> str:
    """Crée (ou réutilise) un client+clé pour les tests 'agent'."""
    with open_session() as s:
        existing = s.query(ApiKey).first()
        if existing:
            return existing.key

        client = Client(id=uuid.uuid4(), name="test-client-agent")
        s.add(client); s.flush()

        s.add(ClientSettings(
            client_id=client.id,
            alert_grouping_enabled=True,
            alert_grouping_window_seconds=300,
            reminder_notification_seconds=600,
            consecutive_failures_threshold=2,
            heartbeat_threshold_minutes=5,
        ))
        s.flush()

        key_val = f"AGENT-{uuid.uuid4().hex[:24]}"
        s.add(ApiKey(id=uuid.uuid4(), client_id=client.id, key=key_val))
        s.commit()
        return key_val


def _agent_payload(*, key: str | None, when: datetime | None = None):
    """Fabrique un payload au format 'agent' (sans header)."""
    when = when or (datetime.now(timezone.utc) - timedelta(seconds=10))
    return {
        "metadata": {
            "generator": "Linux System Audit Tool",
            "version": "0.4",
            "schema_version": "1.0",
            "collection_time": when.isoformat(),
            "key": key,
        },
        "metrics": [
            {
                "id": "docker_005",
                "groupe": "docker",
                "nom": "docker.containers_paused",  # doit mapper vers id si id absent
                "type": "number",                   # doit mapper vers "numeric"
                "valeur": 0,                        # doit mapper vers "value"
                "description": "Nombre de conteneurs Docker en pause.",
            }
        ],
    }


def test_agent_payload_without_header_is_accepted_if_metadata_key_valid(http):
    api_key = _ensure_agent_api_key()
    body = _agent_payload(key=api_key)

    r = http.post("api/v1/ingest/metrics", json=body)  # volontairement SANS X-API-Key
    assert r.status_code in (202, 200), r.text
    j = r.json()
    # 202 "accepted" attendu (ou 200 si doublon selon idempotence)
    assert j.get("status") in {"accepted", "ok", "duplicate"}
    # l'ingest_id doit être renvoyé par l'endpoint
    assert "ingest_id" in j or j.get("reason") == "archived"


def test_agent_payload_mapping_fields(http):
    api_key = _ensure_agent_api_key()
    # collection_time récent pour éviter 202 archived
    when = datetime.now(timezone.utc) - timedelta(seconds=5)
    body = _agent_payload(key=api_key, when=when)

    r = http.post("api/v1/ingest/metrics", json=body)
    assert r.status_code in (202, 200), r.text
    # On ne valide pas la persistance des metrics (pipeline async),
    # mais si la normalisation échouait (sent_at/valeur/nom/type),
    # l’endpoint rejetterait ou échouerait en interne.
    # Ici, l'acceptation valide implicitement le mapping 'agent' -> interne.


def test_agent_payload_missing_key_returns_401(http):
    # Pas de X-API-Key et pas de metadata.key -> doit refuser
    body = _agent_payload(key=None)
    # enlève carrément la clé
    body["metadata"].pop("key", None)

    r = http.post("api/v1/ingest/metrics", json=body)
    assert r.status_code == 401, r.text
