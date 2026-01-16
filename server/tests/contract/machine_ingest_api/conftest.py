# server/tests/contract/machine_ingest_api/conftest.py
from __future__ import annotations
"""
Setup pour les tests *contract* de machine_ingest_api.

Objectifs
---------
- SQLite en mémoire, thread-safe (check_same_thread=False + StaticPool)
- Importer *tous* les modèles avant Base.metadata.create_all()
- Seed minimal : Client + ClientSettings + ApiKey (valeur issue de KEY si fournie,
  sinon une valeur dummy non-sensible)
- Importer TestClient *après* wiring DB (pour que l'app prenne la bonne DB)

Remarque
--------
Ce conftest crée une DB SQLite locale à ces tests contract. Il est volontairement
isolé et idempotent.
"""

import importlib
import os
import pkgutil
import sys
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


# ─────────────────────────────────────────────────────────────────────────────
# PYTHONPATH : rendre `app.*` importable quand on lance pytest à la racine
# ─────────────────────────────────────────────────────────────────────────────
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
SERVER_DIR = os.path.join(ROOT, "server")
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)


def _get_api_key_env() -> str:
    """
    KEY utilisée pour seed la table api_keys.
    - si KEY est fournie par l'env/CI, on la prend (utile si le contract veut tester "vraie" auth)
    - sinon, on utilise une valeur dummy non-sensible.
    """
    return os.getenv("KEY") or "test-api-key"


@pytest.fixture(scope="session", autouse=True)
def _bootstrap_contract_db():
    # ENV minimales pour ce sous-ensemble de tests
    os.environ.setdefault("ENV_FILE", "/dev/null")
    os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "1")
    os.environ.setdefault("INGEST_FUTURE_MAX_SECONDS", "120")
    os.environ.setdefault("INGEST_LATE_MAX_SECONDS", "86400")
    # IMPORTANT : pas de valeur par défaut sensible pour KEY
    os.environ.setdefault("KEY", _get_api_key_env())

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
    # (le projet a souvent des singletons _engine/_SessionLocal).
    import app.infrastructure.persistence.database.session as sess  # type: ignore
    sess._engine = engine
    sess._SessionLocal = SessionLocal

    # ⚠️ Importer *tous* les modèles avant create_all
    from app.infrastructure.persistence.database import base as db_base  # type: ignore
    from app.infrastructure.persistence.database import models as models_pkg  # type: ignore

    for _finder, name, _ispkg in pkgutil.walk_packages(models_pkg.__path__, models_pkg.__name__ + "."):
        importlib.import_module(name)

    db_base.Base.metadata.create_all(engine)

    # Seed minimal pour que l’auth par clé fonctionne si ces tests contract
    # font des requêtes HTTP avec X-API-Key.
    from app.infrastructure.persistence.database.models.api_key import ApiKey  # type: ignore
    from app.infrastructure.persistence.database.models.client import Client  # type: ignore
    from app.infrastructure.persistence.database.models.client_settings import ClientSettings  # type: ignore

    wanted_key = _get_api_key_env()

    with SessionLocal() as s:
        client = s.query(Client).first()
        if not client:
            client = Client(id=uuid.uuid4(), name="ContractClient", email="contract@example.invalid")
            s.add(client)
            s.flush()

        if not s.query(ClientSettings).filter_by(client_id=client.id).first():
            s.add(ClientSettings(client_id=client.id))

        if not s.query(ApiKey).filter_by(key=wanted_key).first():
            s.add(
                ApiKey(
                    id=uuid.uuid4(),
                    client_id=client.id,
                    key=wanted_key,
                    name="contract",
                    is_active=True,
                )
            )

        s.commit()


@pytest.fixture
def http():
    """
    Client HTTP FastAPI importé APRÈS wiring DB + seed.
    """
    from starlette.testclient import TestClient
    from app.main import app  # import tardif

    with TestClient(app) as c:
        yield c
