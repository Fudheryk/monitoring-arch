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
    """Payload valide minimal pour /ingest/metrics."""
    return {
        "machine": {"hostname": "host-1", "os": "linux", "tags": {"env": "test"}},
        "metrics": [
            {"name": "cpu_load", "type": "numeric", "value": 0.75, "unit": "ratio"},
            {"name": "up", "type": "bool", "value": True},
        ],
        "sent_at": "2025-01-01T00:00:00Z",
    }


# ---------- fixtures ----------

@pytest.fixture(autouse=True)
def override_api_key_auth(monkeypatch):
    """
    Remplace la dépendance `api_key_auth` par un stub simple avec un client_id.
    """
    from app.core import security

    fake_key = SimpleNamespace(client_id=uuid.uuid4())

    async def _fake_dep():
        return fake_key

    app.dependency_overrides[security.api_key_auth] = _fake_dep
    try:
        yield fake_key
    finally:
        app.dependency_overrides.pop(security.api_key_auth, None)


@pytest.fixture()
def client():
    return TestClient(app)


# ---------- tests ----------

def test_ingest_without_header_generates_id_and_calls_services(client, monkeypatch, override_api_key_auth):
    """
    Pas de header -> l’endpoint génère un ID "auto-...".
    On vérifie les appels à ensure_machine / init_if_first_seen / enqueue_samples.
    """
    recorded = {}
    fake_machine_id = uuid.uuid4()

    # Patch **les symboles du module endpoint** (pas les modules source)
    import app.api.v1.endpoints.ingest as ingest_ep

    def fake_ensure_machine(machine_payload, api_key):
        recorded["ensure_machine"] = (machine_payload, api_key)
        return SimpleNamespace(id=fake_machine_id)

    def fake_init_if_first_seen(machine_obj, metrics):
        recorded["init_if_first_seen"] = (machine_obj, metrics)

    def fake_enqueue_samples(**kwargs):
        recorded["enqueue_samples"] = kwargs

    monkeypatch.setattr(ingest_ep, "ensure_machine", fake_ensure_machine, raising=True)
    monkeypatch.setattr(ingest_ep, "init_if_first_seen", fake_init_if_first_seen, raising=True)
    monkeypatch.setattr(ingest_ep, "enqueue_samples", fake_enqueue_samples, raising=True)

    # Appel endpoint
    payload = _sample_payload()
    r = client.post("/api/v1/ingest/metrics", json=payload)
    assert r.status_code == 202, r.text
    data = r.json()
    assert data["status"] == "accepted"
    assert data["ingest_id"].startswith("auto-")

    # Vérifs ensure_machine
    assert "ensure_machine" in recorded
    m_payload, api_key_obj = recorded["ensure_machine"]
    assert _strip_nones(_dump_model(m_payload)) == _strip_nones(payload["machine"])
    assert getattr(api_key_obj, "client_id") == override_api_key_auth.client_id

    # Vérifs init_if_first_seen
    assert "init_if_first_seen" in recorded
    machine_obj, metrics_list = recorded["init_if_first_seen"]
    assert getattr(machine_obj, "id") == fake_machine_id
    assert _dump_metrics(metrics_list) == _dump_metrics(payload["metrics"])

    # Vérifs enqueue_samples
    assert "enqueue_samples" in recorded
    enq = recorded["enqueue_samples"]
    assert enq["client_id"] == str(override_api_key_auth.client_id)
    assert enq["machine_id"] == str(fake_machine_id)
    assert enq["ingest_id"] == data["ingest_id"]
    assert _dump_metrics(enq["metrics"]) == _dump_metrics(payload["metrics"])
    assert enq["sent_at"] == payload["sent_at"]


def test_ingest_uses_provided_header(client, monkeypatch, override_api_key_auth):
    """Si X-Ingest-Id est fourni (<= 64 chars), on le réutilise tel quel."""
    import app.api.v1.endpoints.ingest as ingest_ep

    fake_machine_id = uuid.uuid4()

    monkeypatch.setattr(
        ingest_ep,
        "ensure_machine",
        lambda machine, api_key: SimpleNamespace(id=fake_machine_id),
        raising=True,
    )
    monkeypatch.setattr(ingest_ep, "init_if_first_seen", lambda machine, metrics: None, raising=True)

    recorded = {}

    def fake_enqueue_samples(**kw):
        recorded["enq"] = kw

    monkeypatch.setattr(ingest_ep, "enqueue_samples", fake_enqueue_samples, raising=True)

    header_id = "batch-123"
    r = client.post(
        "/api/v1/ingest/metrics",
        json=_sample_payload(),
        headers={"X-Ingest-Id": header_id},
    )
    assert r.status_code == 202
    assert r.json()["ingest_id"] == header_id
    assert recorded["enq"]["ingest_id"] == header_id


def test_ingest_rejects_too_long_header(client):
    """X-Ingest-Id > 64 → 400."""
    r = client.post(
        "/api/v1/ingest/metrics",
        json=_sample_payload(),
        headers={"X-Ingest-Id": "x" * 65},
    )
    assert r.status_code == 400
    assert r.json()["detail"].lower().startswith("invalid x-ingest-id")
