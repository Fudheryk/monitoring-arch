# server/tests/unit/test_metrics_endpoints_paths.py
import uuid
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.infrastructure.persistence.database.session import get_db
from app.core import security

pytestmark = pytest.mark.unit

class _FakeKey:
    def __init__(self, client_id: uuid.UUID):
        self.client_id = client_id

CLIENT_A = uuid.uuid4()
CLIENT_B = uuid.uuid4()

def _auth_a():
    return _FakeKey(CLIENT_A)

class _FakeMachine:
    def __init__(self, client_id):
        self.client_id = client_id

class _MetricRow:
    def __init__(self, id, name, type_, unit, baseline, enabled):
        self.id = id
        self.name = name
        self.type = type_
        self.unit = unit
        self.baseline_value = baseline
        self.is_alerting_enabled = enabled

class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows
    def all(self):
        return self._rows

class _DBFake:
    def __init__(self, machine=None, metric_rows=None):
        self._machine = machine
        self._metric_rows = metric_rows or []
    def get(self, model, mid):
        # on ignore le model, on renvoie ce qu'on a configuré
        return self._machine
    def scalars(self, *_args, **_kwargs):
        # ordre par name: déjà préparé dans metric_rows
        return _ScalarResult(self._metric_rows)

def _db_override(db_obj):
    def _dep():
        try:
            yield db_obj
        finally:
            pass
    return _dep

@pytest.fixture(autouse=True)
def _clean_overrides():
    old = dict(app.dependency_overrides)
    try:
        yield
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old)

client = TestClient(app)

def test_metrics_root_ok_empty():
    # DB n'est pas utilisé par l'endpoint root, mais on override l’auth
    from app.presentation.api import deps
    app.dependency_overrides[deps.api_key_auth] = _auth_a
    r = client.get("/api/v1/metrics")
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0}

def test_metrics_invalid_uuid_404():
    from app.presentation.api import deps
    app.dependency_overrides[deps.api_key_auth] = _auth_a
    r = client.get("/api/v1/metrics/not-a-uuid")
    assert r.status_code == 404
    assert r.json()["detail"] == "Machine not found"

def test_metrics_machine_not_found_404():
    from app.presentation.api import deps
    app.dependency_overrides[deps.api_key_auth] = _auth_a
    app.dependency_overrides[get_db] = _db_override(_DBFake(machine=None))
    r = client.get(f"/api/v1/metrics/{uuid.uuid4()}")
    assert r.status_code == 404
    assert r.json()["detail"] == "Machine not found"

def test_metrics_other_client_404():
    from app.presentation.api import deps
    app.dependency_overrides[deps.api_key_auth] = _auth_a
    app.dependency_overrides[get_db] = _db_override(_DBFake(machine=_FakeMachine(client_id=CLIENT_B)))
    r = client.get(f"/api/v1/metrics/{uuid.uuid4()}")
    assert r.status_code == 404
    assert r.json()["detail"] == "Machine not found"

def test_metrics_happy_path_items_ordered():
    from app.presentation.api import deps
    app.dependency_overrides[deps.api_key_auth] = _auth_a
    rows = [
        _MetricRow(id=uuid.uuid4(), name="cpu", type_="gauge", unit="%", baseline=42.0, enabled=True),
        _MetricRow(id=uuid.uuid4(), name="mem", type_="gauge", unit="%", baseline=55.0, enabled=False),
    ]
    db = _DBFake(machine=_FakeMachine(client_id=CLIENT_A), metric_rows=rows)
    app.dependency_overrides[get_db] = _db_override(db)

    r = client.get(f"/api/v1/metrics/{uuid.uuid4()}")
    assert r.status_code == 200
    items = r.json()
    assert [it["name"] for it in items] == ["cpu", "mem"]
    assert items[0]["type"] == "gauge"
    assert items[0]["unit"] == "%"
    assert "baseline_value" in items[0] and "is_alerting_enabled" in items[0]
