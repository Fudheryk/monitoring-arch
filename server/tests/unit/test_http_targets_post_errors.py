# server/tests/unit/test_http_targets_post_errors.py
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DataError, IntegrityError

from app.main import app
from app.infrastructure.persistence.database.session import get_db

# ✅ On importe l'exacte dépendance utilisée par le routeur :
#    c'est ce callable qu'il faut overrider dans les tests.
from app.presentation.api.deps import api_key_auth as deps_api_key_auth

# Optionnel : on garde aussi l'import core.security pour surcouvrir d'autres routes
# qui pourraient encore l'utiliser ailleurs (défensif, pas nécessaire pour http_targets).
from app.core import security

pytestmark = pytest.mark.unit


class _FakeKey:
    def __init__(self, client_id: uuid.UUID):
        self.client_id = client_id


CLIENT = uuid.uuid4()

PAYLOAD = {
    "name": "t1",
    "url": "https://example.com/health",
    "method": "get",  # lower → teste la normalisation vers UPPER
    "expected_status_code": 200,
    "timeout_seconds": 10,
    "check_interval_seconds": 60,
    "is_active": True,
}


class _ResultWithScalar:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _DB:
    def __init__(self, exc=None, existing_id=None, insert_returns=None):
        self.exc = exc
        self.existing_id = existing_id
        self.insert_returns = insert_returns
        self.rollback_called = False
        self.commit_called = False

    def execute(self, *_, **__):
        if self.exc:
            raise self.exc("stmt", {}, Exception("boom"))
        return _ResultWithScalar(self.insert_returns)

    def commit(self):
        self.commit_called = True

    def rollback(self):
        self.rollback_called = True

    def scalar(self, *_, **__):
        return self.existing_id


def _auth_override():
    """Renvoie une 'clé API' factice portée par le même client."""
    return _FakeKey(CLIENT)


def _db_override(db_obj):
    def _dep():
        try:
            yield db_obj
        finally:
            pass

    return _dep


client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_overrides():
    # Nettoie avant/après chaque test
    old = dict(app.dependency_overrides)
    try:
        yield
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old)


def test_create_target_dataerror_yields_422():
    db = _DB(exc=DataError)
    app.dependency_overrides[get_db] = _db_override(db)

    # ✅ OVERRIDE EXACT de la dépendance utilisée par la route
    app.dependency_overrides[deps_api_key_auth] = _auth_override

    # (Défensif) override aussi la version core si d'autres routes s'en servent
    app.dependency_overrides[security.api_key_auth] = _auth_override

    r = client.post("/api/v1/http-targets", json=PAYLOAD)
    assert r.status_code == 422
    assert r.json()["detail"]["message"].startswith("Invalid value")
    assert db.rollback_called and not db.commit_called


def test_create_target_integrityerror_fallbacks_to_409_with_existing_id():
    existing = uuid.uuid4()
    db = _DB(exc=IntegrityError, existing_id=existing)
    app.dependency_overrides[get_db] = _db_override(db)
    app.dependency_overrides[deps_api_key_auth] = _auth_override
    app.dependency_overrides[security.api_key_auth] = _auth_override  # (défensif)

    r = client.post("/api/v1/http-targets", json=PAYLOAD)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["existing_id"] == str(existing)
    assert db.rollback_called and not db.commit_called


def test_create_target_insert_ok_201():
    new_id = uuid.uuid4()
    db = _DB(insert_returns=new_id)
    app.dependency_overrides[get_db] = _db_override(db)
    app.dependency_overrides[deps_api_key_auth] = _auth_override
    app.dependency_overrides[security.api_key_auth] = _auth_override  # (défensif)

    r = client.post("/api/v1/http-targets", json=PAYLOAD)
    assert r.status_code == 201
    assert r.json() == {"id": str(new_id)}
    assert db.commit_called and not db.rollback_called
