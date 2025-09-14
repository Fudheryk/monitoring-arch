import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.security import api_key_auth
from app.infrastructure.persistence.database.session import get_db as real_get_db

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def override_deps(Session):
    """
    - Remplace get_db par une session SQLite de test
    - Bypass api_key_auth avec un api_key factice
    """
    def _get_db_for_tests():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    fake_key = SimpleNamespace(client_id=uuid.uuid4())

    async def _fake_api_key():
        return fake_key

    app.dependency_overrides[real_get_db] = _get_db_for_tests
    app.dependency_overrides[api_key_auth] = _fake_api_key
    yield
    app.dependency_overrides.pop(real_get_db, None)
    app.dependency_overrides.pop(api_key_auth, None)


def test_metrics_404_when_machine_not_found():
    """Couvre la branche 404 de GET /metrics/{machine_id}."""
    client = TestClient(app)
    random_mid = str(uuid.uuid4())

    r = client.get(f"/api/v1/metrics/{random_mid}")
    assert r.status_code == 404, r.text
    assert r.json()["detail"].lower().startswith("machine not found")
