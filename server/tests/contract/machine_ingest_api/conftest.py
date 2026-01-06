# server/tests/contract/machine_ingest_api/conftest.py
from __future__ import annotations
"""
Setup pour les tests *contract* de machine_ingest_api :
- SQLite en mémoire, thread-safe (check_same_thread=False + StaticPool)
- Import *tous* les modèles avant Base.metadata.create_all()
- Seed minimal : Client + ClientSettings + ApiKey(KEY ou 'dev-apikey-123')
- TestClient importé après wiring DB
"""
import os
import sys
import uuid
import importlib
import pkgutil
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Assurer que 'server/app' est importable en tant que package 'app'
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
SERVER_DIR = os.path.join(ROOT, "server")
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

@pytest.fixture(scope="session", autouse=True)
def _bootstrap_contract_db():
    # ENV minimales pour ce sous-ensemble de tests
    os.environ.setdefault("ENV_FILE", "/dev/null")
    os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "1")
    os.environ.setdefault("INGEST_FUTURE_MAX_SECONDS", "120")
    os.environ.setdefault("INGEST_LATE_MAX_SECONDS", "86400")
    os.environ.setdefault("KEY", os.getenv("KEY", "dev-apikey-123"))

    # Recharger les settings si déjà importés
    if "app.core.config" in sys.modules:
        importlib.reload(sys.modules["app.core.config"])

    # Engine SQLite in-memory partagé et thread-safe
    engine = create_engine(
        os.environ["DATABASE_URL"],
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, future=True, autoflush=False, autocommit=False)

    # Câbler le module session de l'app sur CET engine/sessionmaker
    import app.infrastructure.persistence.database.session as sess  # type: ignore
    sess._engine = engine
    sess._SessionLocal = SessionLocal

    # ⚠️ Importer *tous* les modèles avant create_all
    from app.infrastructure.persistence.database import base as db_base  # type: ignore
    from app.infrastructure.persistence.database import models as models_pkg  # type: ignore
    for _finder, name, _ispkg in pkgutil.walk_packages(
        models_pkg.__path__, models_pkg.__name__ + "."
    ):
        importlib.import_module(name)

    db_base.Base.metadata.create_all(engine)

    # Seed minimal pour que l’auth par clé fonctionne vraiment
    from app.infrastructure.persistence.database.models.client import Client  # type: ignore
    from app.infrastructure.persistence.database.models.client_settings import ClientSettings  # type: ignore
    from app.infrastructure.persistence.database.models.api_key import ApiKey  # type: ignore

    with SessionLocal() as s:
        client = s.query(Client).first()
        if not client:
            client = Client(id=uuid.uuid4(), name="ContractClient", email="contract@example.invalid")
            s.add(client)
            s.flush()

        if not s.query(ClientSettings).filter_by(client_id=client.id).first():
            s.add(ClientSettings(client_id=client.id))

        wanted_key = os.getenv("KEY", "dev-apikey-123")
        if not s.query(ApiKey).filter_by(key=wanted_key).first():
            s.add(ApiKey(id=uuid.uuid4(), client_id=client.id, key=wanted_key, name="contract", is_active=True))

        s.commit()

@pytest.fixture
def http():
    """Client HTTP FastAPI importé APRÈS wiring DB + seed."""
    from starlette.testclient import TestClient
    from app.main import app  # import tardif
    with TestClient(app) as c:
        yield c
