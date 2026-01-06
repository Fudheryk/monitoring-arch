# server/tests/integration/test_auth_flow.py
"""
Tests d'intégration auth (login → me → refresh → logout) via TestClient.

Modifs clés:
- ENV fixé avant import de l'app
- SQLite in-memory partagé (StaticPool)
- Chemins relatifs (pas d'URL http://localhost:8000)
- login en JSON: {"email","password"}
"""

import os
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.engine.url import make_url  # ✅ emplacement correct

# 1) ENV avant import
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "1")

from app.main import app  # noqa: E402
from app.infrastructure.persistence.database.base import Base  # noqa: E402
from app.infrastructure.persistence.database.session import get_db  # ✅ override FastAPI  # noqa: E402
from app.infrastructure.persistence.database.models.client import Client  # noqa: E402
from app.infrastructure.persistence.database.models.user import User  # noqa: E402
from app.core.security import hash_password  # noqa: E402

DB_URL = os.environ["DATABASE_URL"]

# 2) Engine in-memory partagé
url = make_url(DB_URL)
engine_kwargs = {"future": True}
if url.get_backend_name() == "sqlite":
    engine_kwargs["connect_args"] = {"check_same_thread": False}
    if "memory" in (url.database or "").lower():
        engine_kwargs["poolclass"] = StaticPool

engine = create_engine(DB_URL, **engine_kwargs)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

# 3) Schéma
@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

# 4) Override de la dépendance FastAPI `get_db`
def _override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = _override_get_db

# 5) Client
@pytest.fixture()
def tc():
    return TestClient(app)

# 6) Seed + creds
@pytest.fixture()
def creds():
    db = TestingSessionLocal()
    try:
        client = Client(id=uuid.uuid4(), name="Acme", email="it@acme.com")
        db.add(client)
        db.flush()

        email = f"admin+{uuid.uuid4().hex[:6]}@acme.com"
        user = User(
            id=uuid.uuid4(),
            client_id=client.id,
            email=email,
            password_hash=hash_password("admin123"),
            role="admin_client",
            is_active=True,
        )
        db.add(user)
        db.commit()
        return {"email": email, "password": "admin123"}
    finally:
        db.close()

# 7) Tests

def test_login_me_refresh_logout_flow(tc: TestClient, creds: dict):
    # Login OK
    r = tc.post("/api/v1/auth/login", json=creds)
    assert r.status_code == 200, r.text
    assert "set-cookie" in r.headers or tc.cookies

    # /me avec cookie
    r = tc.get("/api/v1/auth/me")
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["email"] == creds["email"]
    assert me.get("client_id")
    assert me.get("role") in {"admin_client", "admin", None}

    # refresh
    r = tc.post("/api/v1/auth/refresh-cookie")
    assert r.status_code == 200, r.text
    assert "set-cookie" in r.headers or tc.cookies

    # logout
    r = tc.post("/api/v1/auth/logout")
    assert r.status_code in (200, 204), r.text

    # sans cookie → 401/403
    tc.cookies.clear()
    r = tc.get("/api/v1/auth/me")
    assert r.status_code in (401, 403)

def test_login_wrong_password(tc: TestClient, creds: dict):
    bad = {"email": creds["email"], "password": "wrong"}
    r = tc.post("/api/v1/auth/login", json=bad)
    assert r.status_code in (400, 401)
