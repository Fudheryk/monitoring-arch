# server/tests/conftest.py
"""
Conftest global pour toute la suite de tests.

Objectifs
---------
- Quand la stack est DOWN (par défaut), `pytest -q` n'exécute que les tests
  "unit-like" : /tests/unit/ (et éventuellement d'autres tests marqués unit).
  -> SQLite in-memory partagé, Celery eager, seed minimal avant chaque test.
- Les dossiers /tests/integration/ et /tests/e2e/ sont SKIP tant que
  INTEG_STACK_UP/E2E_STACK_UP != "1".
- Les tests /tests/contract/ peuvent être :
  - traités comme unit-like (SQLite) si tu le souhaites
  - OU nécessiter une stack (skip tant que INTEG_STACK_UP!=1)
  Ici, on conserve ton comportement actuel : contract = unit-like pour l'env/DB
  (via seed + overrides), MAIS on garde aussi un skip "contract" si stack down
  si c'est ce que tu veux vraiment. (Tu peux ajuster facilement.)

SQLite & check_same_thread
--------------------------
- On évite les erreurs "invalid connection option check_same_thread" en ne
  configurant cette option **que** pour SQLite côté tests unit.
"""

from __future__ import annotations

import importlib
import os
import pathlib
import pkgutil
import sys
import time
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


# ─────────────────────────────────────────────────────────────────────────────
# PYTHONPATH : rendre `app.*` importable quand on lance pytest à la racine
# ─────────────────────────────────────────────────────────────────────────────
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SERVER_DIR = os.path.join(ROOT, "server")
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _is_unit(request: pytest.FixtureRequest) -> bool:
    """
    Retourne True pour les tests "unit-like".

    Règle :
    - marqueur @pytest.mark.unit => unit-like
    - dossier /tests/unit/ => unit-like

    Remarque :
    - Si tu veux inclure /tests/contract/ en unit-like, ajoute la condition
      "/tests/contract/" ici. Ton code initial disait "unit + contract" mais
      la logique ne le faisait pas. On le corrige.
    """
    if request.node.get_closest_marker("unit") is not None:
        return True
    try:
        p = str(request.node.fspath).replace("\\", "/")
        return ("/tests/unit/" in p) or ("/tests/contract/" in p)
    except Exception:
        return False


def _get_api_key_env() -> str:
    """
    Récupère KEY pour les cas où on en a besoin en tests unit/contract.

    IMPORTANT :
    - Aucune valeur par défaut "réaliste" ne doit exister dans le repo.
    - Pour les tests unit/contract, on peut utiliser une valeur "dummy"
      (non utilisée en prod) uniquement car l'auth API key est bypassée.
    """
    return os.getenv("KEY") or "test-api-key"


# ─────────────────────────────────────────────────────────────────────────────
# Options CLI & defaults globaux
# ─────────────────────────────────────────────────────────────────────────────
def pytest_addoption(parser):
    # API est utile pour integration/e2e ; par défaut local
    parser.addoption("--api", action="store", default=os.getenv("API", "http://localhost:8000"))

    # KEY ne doit PAS avoir de défaut sensible. On laisse vide si absent.
    # Les tests qui en ont besoin doivent être pilotés par scripts/CI.
    parser.addoption("--api-key", action="store", default=os.getenv("KEY", ""))


@pytest.fixture(scope="session", autouse=True)
def _set_global_env_defaults():
    """
    Met des defaults non-sensibles pour la suite de tests.

    IMPORTANT :
    - pas de KEY par défaut ici.
    - pour unit/contract : on bypass l'auth et on seed une ApiKey avec une valeur dummy.
    """
    os.environ.setdefault("API", "http://localhost:8000")
    os.environ.setdefault("INTEG_STACK_UP", os.getenv("INTEG_STACK_UP", "0"))
    os.environ.setdefault("E2E_STACK_UP", os.getenv("E2E_STACK_UP", "0"))
    os.environ.setdefault("INGEST_FUTURE_MAX_SECONDS", "120")
    os.environ.setdefault("INGEST_LATE_MAX_SECONDS", "86400")


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures communes : API + requests.Session avec retries + helper 'wait'
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def api_base(pytestconfig) -> str:
    return pytestconfig.getoption("--api")


@pytest.fixture(scope="session")
def api_headers(pytestconfig) -> dict:
    """
    Headers par défaut pour appels HTTP en tests integration/e2e.

    NOTE :
    - On inclut X-API-Key seulement si --api-key/KEY est fourni.
    - Les tests UI (JWT cookie) ne doivent pas dépendre de ce header.
    """
    api_key = (pytestconfig.getoption("--api-key") or "").strip()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


@pytest.fixture(scope="session")
def session_retry() -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}),
        raise_on_status=False,
    )
    s.mount("http://", HTTPAdapter(max_retries=retries))
    s.mount("https://", HTTPAdapter(max_retries=retries))
    return s


@pytest.fixture
def wait():
    def _wait(fn, timeout=90, every=2):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                val = fn()
            except Exception:
                val = None
            if val:
                return val
            time.sleep(every)
        return None

    return _wait


# ─────────────────────────────────────────────────────────────────────────────
# UNIT/CONTRACT ONLY: ENV sûres + DATABASE_URL SQLite
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def unit_env(request):
    if not _is_unit(request):
        return

    os.environ.setdefault("ENV_FILE", "/dev/null")
    os.environ.setdefault("SLACK_WEBHOOK", "http://example.invalid/webhook")
    os.environ.setdefault("SLACK_DEFAULT_CHANNEL", "#canal")
    os.environ.setdefault("ALERT_REMINDER_MINUTES", "1")
    os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "1")

    # Reload best-effort si déjà importé
    try:
        if "app.core.config" in sys.modules:
            importlib.reload(sys.modules["app.core.config"])
        dbmod = sys.modules.get("app.infrastructure.persistence.database.session")
        if dbmod:
            for attr in ("_engine", "_SessionLocal"):
                if hasattr(dbmod, attr):
                    setattr(dbmod, attr, None)
            importlib.reload(dbmod)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# UNIT/CONTRACT ONLY: Celery eager
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def celery_eager(request):
    if not _is_unit(request):
        yield
        return

    try:
        from app.workers.celery_app import celery  # type: ignore
    except Exception:
        yield
        return

    prev_always = celery.conf.task_always_eager
    prev_propag = celery.conf.task_eager_propagates
    celery.conf.task_always_eager = True
    celery.conf.task_eager_propagates = True
    try:
        yield
    finally:
        celery.conf.task_always_eager = prev_always
        celery.conf.task_eager_propagates = prev_propag


# ─────────────────────────────────────────────────────────────────────────────
# UNIT/CONTRACT ONLY: DB SQLite partagée + Base.create_all
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def _sqlite_engine_unit():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ARG001
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    # Importer tous les modèles avant create_all
    from app.infrastructure.persistence.database import base as db_base  # type: ignore
    from app.infrastructure.persistence.database import models as models_pkg  # type: ignore

    for _finder, name, _ispkg in pkgutil.walk_packages(models_pkg.__path__, models_pkg.__name__ + "."):
        importlib.import_module(name)

    db_base.Base.metadata.create_all(engine)
    return engine


@pytest.fixture(scope="session")
def _Session_unit(_sqlite_engine_unit):
    return sessionmaker(
        bind=_sqlite_engine_unit,
        future=True,
        autoflush=True,
        expire_on_commit=False,
    )


@pytest.fixture
def Session(request, _Session_unit):
    if not _is_unit(request):
        pytest.skip("Session fixture is only available for unit/contract tests")
    return _Session_unit


# ─────────────────────────────────────────────────────────────────────────────
# Seed minimal **avant chaque test** (survit à la purge)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _ensure_default_seed(request, _Session_unit):
    """
    Garantit qu'un client + settings + api_key existent avant chaque test (unit/contract).

    IMPORTANT :
    - On n'utilise plus de clé par défaut "dev-*".
    - La valeur seed est une valeur dummy (test-api-key) si KEY n'est pas fournie.
    """
    if not _is_unit(request):
        return

    from sqlalchemy import select
    from app.infrastructure.persistence.database.models.api_key import ApiKey  # type: ignore
    from app.infrastructure.persistence.database.models.client import Client  # type: ignore
    from app.infrastructure.persistence.database.models.client_settings import ClientSettings  # type: ignore

    key_value = _get_api_key_env()

    with _Session_unit() as s:
        client = s.execute(select(Client)).scalars().first()
        if not client:
            client = Client(id=uuid.uuid4(), name="TestClient", email="test@example.invalid")
            s.add(client)
            s.flush()

        cs = s.execute(select(ClientSettings).where(ClientSettings.client_id == client.id)).scalars().first()
        if not cs:
            s.add(ClientSettings(client_id=client.id))

        ak = s.execute(select(ApiKey).where(ApiKey.key == key_value)).scalars().first()
        if not ak:
            s.add(ApiKey(id=uuid.uuid4(), client_id=client.id, key=key_value, name="seed-key", is_active=True))

        s.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Purge DB entre tests unit/contract
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _clear_db_between_unit_tests(request, _Session_unit):
    if not _is_unit(request):
        yield
        return
    yield
    from app.infrastructure.persistence.database import base as db_base  # type: ignore

    with _Session_unit() as s:
        for table in reversed(db_base.Base.metadata.sorted_tables):
            s.execute(table.delete())
        s.commit()


# ─────────────────────────────────────────────────────────────────────────────
# UNIT/CONTRACT ONLY: Patch DB fort (open_session + SessionLocal + engine)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def patch_db_stack_for_unit(request, monkeypatch, _Session_unit, _sqlite_engine_unit):
    if not _is_unit(request):
        return

    @contextmanager
    def _fake_open_session():
        with _Session_unit() as s:
            yield s

    def _fake_get_session():
        return _Session_unit()

    def _fake_get_db():
        with _Session_unit() as s:
            yield s

    # 1) Module source 'session'
    try:
        sess_mod = importlib.import_module("app.infrastructure.persistence.database.session")
        monkeypatch.setattr(sess_mod, "open_session", _fake_open_session, raising=False)
        monkeypatch.setattr(sess_mod, "SessionLocal", _Session_unit, raising=False)
        monkeypatch.setattr(sess_mod, "engine", _sqlite_engine_unit, raising=False)
        monkeypatch.setattr(sess_mod, "get_session", _fake_get_session, raising=False)
        monkeypatch.setattr(sess_mod, "get_db", _fake_get_db, raising=False)
    except Exception:
        pass

    # 2) Modules consommateurs (références figées)
    to_patch = [
        # repositories
        "app.infrastructure.persistence.repositories.alert_repository",
        "app.infrastructure.persistence.repositories.api_key_repository",
        "app.infrastructure.persistence.repositories.http_target_repository",
        "app.infrastructure.persistence.repositories.incident_repository",
        "app.infrastructure.persistence.repositories.ingest_repository",
        "app.infrastructure.persistence.repositories.machine_repository",
        "app.infrastructure.persistence.repositories.metric_repository",
        "app.infrastructure.persistence.repositories.sample_repository",
        "app.infrastructure.persistence.repositories.threshold_repository",
        # tasks
        "app.workers.tasks.notification_tasks",
        "app.workers.tasks.evaluation_tasks",
        "app.workers.tasks.ingest_tasks",
        "app.workers.tasks.http_monitoring_tasks",
        # services
        "app.application.services.http_monitor_service",
        # endpoints API (dépendances get_db)
        "app.api.v1.endpoints.auth",
        "app.api.v1.endpoints.alerts",
        "app.api.v1.endpoints.dashboard",
        "app.api.v1.endpoints.health",
        "app.api.v1.endpoints.http_targets",
        "app.api.v1.endpoints.incidents",
        "app.api.v1.endpoints.ingest",
        "app.api.v1.endpoints.machines",
        "app.api.v1.endpoints.metrics",
        "app.api.v1.endpoints.settings",
        # security (DB deps)
        "app.core.security",
    ]

    # Fenêtre d’ingest "large" pour unit/contract (évite archived)
    try:
        import app.api.v1.endpoints.ingest as ingest_ep  # type: ignore
        ingest_ep.app_config.settings.INGEST_FUTURE_MAX_SECONDS = 120
        ingest_ep.app_config.settings.INGEST_LATE_MAX_SECONDS = 31536000  # 365 jours
    except Exception:
        pass

    for modname in to_patch:
        try:
            m = importlib.import_module(modname)
        except ModuleNotFoundError:
            continue
        monkeypatch.setattr(m, "open_session", _fake_open_session, raising=False)
        monkeypatch.setattr(m, "SessionLocal", _Session_unit, raising=False)
        monkeypatch.setattr(m, "engine", _sqlite_engine_unit, raising=False)
        if hasattr(m, "get_session"):
            monkeypatch.setattr(m, "get_session", _fake_get_session, raising=False)
        if hasattr(m, "get_db"):
            monkeypatch.setattr(m, "get_db", _fake_get_db, raising=False)


# ─────────────────────────────────────────────────────────────────────────────
# UNIT/CONTRACT ONLY: fenêtre d’ingest *très* large (évite "archived")
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _unit_force_huge_ingest_window(request, unit_env):
    if not _is_unit(request):
        return
    import app.core.config as cfg
    huge = 60 * 60 * 24 * 365 * 50  # 50 ans
    cfg.settings.INGEST_FUTURE_MAX_SECONDS = huge
    cfg.settings.INGEST_LATE_MAX_SECONDS = huge


# ─────────────────────────────────────────────────────────────────────────────
# Mock SlackProvider pour unit/contract
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def mock_slack(request, monkeypatch):
    if not _is_unit(request):
        return None

    calls = []

    class _MockProvider:
        def __init__(self, *args, **kwargs):  # noqa: ARG002
            pass

        def send(self, **kw):
            calls.append(kw)
            return True

    def _fake_send_slack(**kw):
        calls.append(kw)
        return True

    try:
        from app.infrastructure.notifications.providers import slack_provider  # type: ignore
        if hasattr(slack_provider, "SlackProvider"):
            monkeypatch.setattr(slack_provider, "SlackProvider", _MockProvider, raising=True)
        if hasattr(slack_provider, "send_slack"):
            monkeypatch.setattr(slack_provider, "send_slack", _fake_send_slack, raising=True)
    except Exception:
        pass

    try:
        import app.workers.tasks.notification_tasks as nt  # type: ignore
        if hasattr(nt, "SlackProvider"):
            monkeypatch.setattr(nt, "SlackProvider", _MockProvider, raising=True)
        if hasattr(nt, "send_slack"):
            monkeypatch.setattr(nt, "send_slack", _fake_send_slack, raising=True)
    except Exception:
        pass

    return calls


# ─────────────────────────────────────────────────────────────────────────────
# Cursor Postgres brut (INTÉGRATION seulement si injecté explicitement)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def db_cursor():
    """
    Curseur psycopg transactionnel sur la base d'intégration Docker.

    Remarque :
    - Valeur par défaut pointe vers db:5432 (utile si ce fixture est utilisé
      dans un conteneur). Côté hôte, il faut injecter PG_DSN vers localhost.
    """
    import psycopg
    dsn = os.getenv("PG_DSN", "postgresql://postgres:postgres@db:5432/monitoring")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("BEGIN")
            try:
                yield cur
            finally:
                cur.execute("ROLLBACK")


# ─────────────────────────────────────────────────────────────────────────────
# Skips automatiques par dossier si la stack n'est pas up
# ─────────────────────────────────────────────────────────────────────────────
def pytest_collection_modifyitems(config, items):
    integ_on = os.getenv("INTEG_STACK_UP") == "1"
    e2e_on = os.getenv("E2E_STACK_UP") == "1"

    skip_integ = pytest.mark.skip(reason="Integration stack not running (export INTEG_STACK_UP=1)")
    skip_e2e = pytest.mark.skip(reason="E2E stack not running (export E2E_STACK_UP=1)")

    # Ici, on skip contract si stack down (comme ton code initial).
    # Si tu veux "contract = unit-like" sans stack, supprime ce skip.
    skip_contract = pytest.mark.skip(reason="Contract tests require real DB/API (export INTEG_STACK_UP=1)")

    for item in items:
        p = pathlib.Path(str(item.fspath))
        parts = {part.lower() for part in p.parts}
        if "integration" in parts and not integ_on:
            item.add_marker(skip_integ)
        if "e2e" in parts and not e2e_on:
            item.add_marker(skip_e2e)
        if "contract" in parts and not integ_on:
            item.add_marker(skip_contract)


# ─────────────────────────────────────────────────────────────────────────────
# UNIT/CONTRACT ONLY: bypass API key auth (dependency_overrides)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _override_api_auth_for_unit(request):
    """
    En tests unit/contract, on bypass l'auth X-API-Key pour éviter les 401.

    - On override les dépendances canoniques :
        app.core.security.api_key_auth
    - Puis on couvre d'éventuels alias utilisés par certains endpoints.
    """
    if not _is_unit(request):
        yield
        return

    from app.main import app
    from app.core import security as sec

    # Fake API key retournée par les deps d'auth
    fake = SimpleNamespace(client_id=uuid.uuid4(), key=_get_api_key_env())

    async def _fake_dep():
        return fake

    async def _fake_opt_dep():
        return fake

    overrides = {
        sec.api_key_auth: _fake_dep,
    }

    # (Optionnel) couvrir les alias éventuels
    try:
        import app.presentation.api.deps as deps_mod  # type: ignore
        if hasattr(deps_mod, "api_key_auth"):
            overrides[deps_mod.api_key_auth] = _fake_dep
    except Exception:
        pass

    try:
        import app.api.v1.endpoints.ingest as ingest_ep  # type: ignore
        if hasattr(ingest_ep, "api_key_auth"):
            overrides[ingest_ep.api_key_auth] = _fake_dep
    except Exception:
        pass

    app.dependency_overrides.update(overrides)

    try:
        yield
    finally:
        for k in list(overrides.keys()):
            app.dependency_overrides.pop(k, None)
