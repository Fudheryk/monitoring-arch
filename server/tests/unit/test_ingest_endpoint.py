from __future__ import annotations

"""
Unit tests ingest endpoint.

Contexte auth (migration)
-------------------------
- Ingest = API key obligatoire (header-only) via Depends(api_key_auth).
- En unit tests, on bypass l'auth avec dependency_overrides :
  on ne doit PAS dépendre d'une vraie clé.

Attention au routing
--------------------
- L'endpoint testé est /api/v1/ingest/metrics (cf router prefix="/ingest").
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.unit


# ---------- helpers ----------

def _dump_model(m):
    """Retourne un dict depuis un modèle Pydantic (v2 ou v1), ou l'objet si déjà dict."""
    if isinstance(m, dict):
        return m
    if hasattr(m, "model_dump"):  # pydantic v2
        return m.model_dump()
    if hasattr(m, "dict"):  # pydantic v1
        return m.dict()
    try:
        return dict(m)
    except Exception:
        return m


def _strip_nones(obj):
    """Retire récursivement les paires clé: None des dict/list pour des comparaisons robustes."""
    if isinstance(obj, dict):
        return {k: _strip_nones(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip_nones(v) for v in obj]
    return obj


def _dump_metrics(metrics):
    """Dump pydantic -> dict puis supprime les None pour éviter les deltas artificiels."""
    dumped = [_dump_model(m) for m in (metrics or [])]
    return _strip_nones(dumped)


def _sample_payload():
    """
    Payload valide minimal pour POST /api/v1/ingest/metrics.

    NOTE : le schéma d'ingest normalise plusieurs alias (name/nom/id, bool/boolean, etc.)
    Ici on reste volontairement simple.
    """
    return {
        "machine": {"hostname": "host-1", "os": "linux", "tags": {"env": "test"}},
        "metrics": [
            {"name": "cpu_load", "type": "numeric", "value": 0.75, "unit": "ratio"},
            {"name": "up", "type": "boolean", "value": True},
        ],
        "sent_at": "2025-01-01T00:00:00Z",
    }


# ---------- fixtures ----------

@pytest.fixture(autouse=True)
def override_api_key_auth():
    """
    Remplace la dépendance d'auth API key (obligatoire) par un stub.

    IMPORTANT (migration) :
    - l'ingest utilise Depends(api_key_auth) (pas optional)
    - en unit tests on bypass pour éviter toute dépendance DB/API keys réelles.
    """
    from app.core import security

    fake = SimpleNamespace(client_id=uuid.uuid4(), key="test-api-key", id=uuid.uuid4())

    async def _ok():
        return fake  # async car Depends accepte async

    app.dependency_overrides[security.api_key_auth] = _ok

    try:
        yield _ok
    finally:
        app.dependency_overrides.pop(security.api_key_auth, None)


@pytest.fixture()
def client():
    return TestClient(app)


# ---------- tests ----------

def test_ingest_without_header_generates_id_and_calls_services(client, monkeypatch):
    """
    Sans header X-Ingest-Id -> le service génère un ID "auto-...".
    On vérifie les appels à ensure_machine / enqueue_samples.

    NOTE :
    - Avec la refactor "service" (ingestion_service.py), l'endpoint délègue
      à ingest_metrics(). Il n'appelle plus init_if_first_seen/enqueue_samples
      directement depuis le module endpoint.
    - Donc on patch ingest_metrics (service) plutôt que des symboles endpoint.
    """
    recorded = {}

    # Patch le service appelé par l'endpoint
    import app.api.v1.endpoints.ingest as ingest_ep

    def fake_ingest_metrics(*, payload, api_key, x_ingest_id=None):
        recorded["payload"] = payload
        recorded["api_key"] = api_key
        recorded["x_ingest_id"] = x_ingest_id
        return {"status": "accepted", "ingest_id": "auto-fake-123"}

    monkeypatch.setattr(ingest_ep, "ingest_metrics", fake_ingest_metrics, raising=True)

    r = client.post("/api/v1/ingest/metrics", json=_sample_payload())
    assert r.status_code == 202, r.text

    data = r.json()
    assert data["status"] == "accepted"
    assert data["ingest_id"].startswith("auto-")

    # Vérifs : l'endpoint a bien passé un x_ingest_id None
    assert recorded["x_ingest_id"] is None

    # Vérifs : payload normalisé contient machine/metrics/sent_at
    payload = recorded["payload"]
    dumped = _strip_nones(_dump_model(payload))
    assert "machine" in dumped and dumped["machine"]["hostname"] == "host-1"
    assert "metrics" in dumped and len(dumped["metrics"]) == 2
    assert "sent_at" in dumped

    # Vérifs : api_key stub est bien transmis
    api_key_obj = recorded["api_key"]
    assert hasattr(api_key_obj, "client_id")
    assert isinstance(getattr(api_key_obj, "client_id", None), uuid.UUID)
    assert getattr(api_key_obj, "key", None) == "test-api-key"


def test_ingest_uses_provided_header(client, monkeypatch):
    """Si X-Ingest-Id est fourni (<= 64 chars), il est transmis tel quel au service."""
    import app.api.v1.endpoints.ingest as ingest_ep

    recorded = {}

    def fake_ingest_metrics(*, payload, api_key, x_ingest_id=None):
        recorded["x_ingest_id"] = x_ingest_id
        return {"status": "accepted", "ingest_id": x_ingest_id}

    monkeypatch.setattr(ingest_ep, "ingest_metrics", fake_ingest_metrics, raising=True)

    header_id = "batch-123"
    r = client.post(
        "/api/v1/ingest/metrics",
        json=_sample_payload(),
        headers={"X-Ingest-Id": header_id},
    )
    assert r.status_code == 202, r.text
    assert r.json()["ingest_id"] == header_id
    assert recorded["x_ingest_id"] == header_id


def test_ingest_rejects_too_long_header(client):
    """X-Ingest-Id > 64 → 400."""
    r = client.post(
        "/api/v1/ingest/metrics",
        json=_sample_payload(),
        headers={"X-Ingest-Id": "x" * 65},
    )
    assert r.status_code == 400
    assert r.json()["detail"].lower().startswith("invalid x-ingest-id")
