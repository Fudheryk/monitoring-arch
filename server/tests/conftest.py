# server/tests/conftest.py
"""
Conftest *global* pour toute la suite de tests.

Points clés :
- Bootstrap PYTHONPATH pour que 'server' soit importable partout (corrige "No module named 'server'").
- Centralise les options pytest (--api, --api-key) pour éviter le bug "option already added".
- Fournit des fixtures communes à tous les tests (api_base, api_headers, session_retry, wait).
- Pour les tests @unit uniquement :
  - Définit des ENV sûres (pas d'appels externes) + DATABASE_URL SQLite in-memory.
  - Active Celery en mode "eager" (exécution in-process).
  - Monte une DB SQLite in-memory partagée + Base.create_all.
  - Patch FORT de la pile DB : get_sync_session, SessionLocal, engine (module session + modules consommateurs).
  - Mocke Slack (provider + tâches).
- Fournit un db_cursor psycopg pour certains tests d'intégration qui le demandent explicitement.
"""

import os
import time
import importlib
import pkgutil
from contextlib import contextmanager

import pytest
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


# ============================================================================
# Helpers
# ============================================================================
def _is_unit(request: pytest.FixtureRequest) -> bool:
    """True si le test courant est marqué @pytest.mark.unit."""
    return request.node.get_closest_marker("unit") is not None


# ============================================================================
# Options CLI & defaults globaux (UNE seule déclaration)
# ============================================================================
def pytest_addoption(parser):
    """
    Déclare --api et --api-key UNE seule fois pour toute la suite (évite 'already added').
    Utilisé par les fixtures 'api_base' et 'api_headers'.
    """
    parser.addoption("--api", action="store", default=os.getenv("API", "http://localhost:8000"))
    parser.addoption("--api-key", action="store", default=os.getenv("KEY", "dev-apikey-123"))


@pytest.fixture(scope="session", autouse=True)
def _set_global_env_defaults():
    """
    Valeurs par défaut raisonnables pour un usage local.
    En CI, ces variables sont déjà définies par le workflow.
    """
    os.environ.setdefault("API", "http://localhost:8000")
    os.environ.setdefault("KEY", "dev-apikey-123")
    # Garde-fous utilisés par certains tests pour éviter des SKIP intempestifs
    os.environ.setdefault("INTEG_STACK_UP", os.getenv("INTEG_STACK_UP", "1"))
    os.environ.setdefault("E2E_STACK_UP", os.getenv("E2E_STACK_UP", "1"))


# ============================================================================
# Fixtures communes : API + requests.Session avec retries + helper 'wait'
# ============================================================================
@pytest.fixture(scope="session")
def api_base(pytestconfig) -> str:
    return pytestconfig.getoption("--api")


@pytest.fixture(scope="session")
def api_headers(pytestconfig) -> dict:
    return {"X-API-Key": pytestconfig.getoption("--api-key"), "Content-Type": "application/json"}


@pytest.fixture(scope="session")
def session_retry() -> requests.Session:
    """
    Session HTTP robuste avec backoff & retries (utile pour les integ/E2E).
    """
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
    """
    Helper simple : poll une fonction jusqu'à ce qu'elle renvoie une valeur truthy.
    """
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


# ============================================================================
# UNIT-ONLY: ENV sûres (pas d'appels externes) + DATABASE_URL SQLite
# ============================================================================
@pytest.fixture(autouse=True)
def unit_env(request):
    """
    En unit : fixe des ENV sûres pour ne jamais appeler l'extérieur.
    Définit aussi une DATABASE_URL SQLite in-memory si non fournie, au cas où
    le code lit directement l'ENV (avant nos patchs).
    Hors unit : ne fait rien.
    """
    if not _is_unit(request):
        return
    os.environ.setdefault("SLACK_WEBHOOK", "http://example.invalid/webhook")
    os.environ.setdefault("SLACK_DEFAULT_CHANNEL", "#notif-webhook")
    os.environ.setdefault("ALERT_REMINDER_MINUTES", "1")
    os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")


# ============================================================================
# UNIT-ONLY: Celery en mode "eager" (tâches exécutées in-process)
# ============================================================================
@pytest.fixture(autouse=True)
def celery_eager(request):
    """
    Active le mode 'eager' de Celery en unit (tâches exécutées in-process).
    ⚠️ Comme c'est un fixture générateur, il DOIT toujours 'yield', même hors unit.
    """
    if not _is_unit(request):
        # Pas de setup, mais on yield quand même pour satisfaire pytest.
        yield
        return

    try:
        from app.workers.celery_app import celery  # type: ignore
    except Exception:
        # Si Celery n'est pas importable, on ne casse pas les tests unitaires.
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


# ============================================================================
# UNIT-ONLY: DB SQLite in-memory partagée + Base.metadata.create_all
# ============================================================================
@pytest.fixture(scope="session")
def _sqlite_engine_unit():
    """
    Engine in-memory **partagé** entre connexions (StaticPool + check_same_thread=False)
    + activation des contraintes FK sur chaque connexion.
    """
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ARG001
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    # Charger tous les modèles avant create_all (import dynamique robuste)
    from app.infrastructure.persistence.database import base as db_base  # type: ignore
    from app.infrastructure.persistence.database import models as models_pkg  # type: ignore

    for _finder, name, _ispkg in pkgutil.walk_packages(
        models_pkg.__path__, models_pkg.__name__ + "."
    ):
        importlib.import_module(name)

    db_base.Base.metadata.create_all(engine)
    return engine


@pytest.fixture(scope="session")
def _Session_unit(_sqlite_engine_unit):
    """
    Retourne un sessionmaker lié au moteur SQLite in-memory.
    """
    return sessionmaker(
        bind=_sqlite_engine_unit,
        future=True,
        autoflush=True,
        expire_on_commit=False,
    )


@pytest.fixture
def Session(request, _Session_unit):
    """
    Fournit un sessionmaker à utiliser comme `with Session() as s:` pour les tests unitaires.
    Sera *skippé* s'il est injecté dans un test non marqué @unit (sécurité d'usage).
    """
    if not _is_unit(request):
        pytest.skip("Session fixture is only available for unit tests")
    return _Session_unit


# Purge DB entre tests unitaires (évite les fuites d'état)
@pytest.fixture(autouse=True)
def _clear_db_between_unit_tests(request, _Session_unit):
    """
    Après chaque test unitaire, on supprime le contenu de toutes les tables.
    ⚠️ Générateur : doit 'yield' aussi hors unit.
    """
    if not _is_unit(request):
        yield
        return

    # --- setup (avant test) : rien ---
    yield  # exécution du test
    # --- teardown (après test) ---
    from app.infrastructure.persistence.database import base as db_base  # type: ignore
    with _Session_unit() as s:
        for table in reversed(db_base.Base.metadata.sorted_tables):
            s.execute(table.delete())
        s.commit()


# ============================================================================
# UNIT-ONLY: Patch DB fort (get_sync_session + SessionLocal + engine)
# ============================================================================
@pytest.fixture(autouse=True)
def patch_db_stack_for_unit(request, monkeypatch, _Session_unit, _sqlite_engine_unit):
    """
    Rend *impossible* l'usage de Postgres pendant les tests unitaires.
    On remplace, dans les modules sensibles :
      - get_sync_session -> contextmanager qui yield une session SQLite
      - SessionLocal     -> sessionmaker SQLite
      - engine           -> engine SQLite

    ⚠️ Très important : certains modules importent/figent ces symboles à l'import.
    Il faut donc aussi patcher ces modules *consommateurs* afin d'écraser leur
    référence locale (ex: `from ...session import get_sync_session`). Sans ça,
    ils continueraient à viser Postgres.
    """
    if not _is_unit(request):
        return

    @contextmanager
    def _fake_get_sync_session():
        with _Session_unit() as s:
            yield s

    # 1) Module source 'session' (remplace les symboles à la source)
    try:
        sess_mod = importlib.import_module("app.infrastructure.persistence.database.session")
        monkeypatch.setattr(sess_mod, "get_sync_session", _fake_get_sync_session, raising=False)
        monkeypatch.setattr(sess_mod, "SessionLocal", _Session_unit, raising=False)
        monkeypatch.setattr(sess_mod, "engine", _sqlite_engine_unit, raising=False)
    except Exception:
        pass

    # 2) Modules qui peuvent avoir importé/figé ces symboles à l'import
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
        # ⚠️ services utilisés par les tests unitaires
        "app.application.services.http_monitor_service",  # ← AJOUT CRUCIAL
    ]

    for modname in to_patch:
        try:
            m = importlib.import_module(modname)
        except ModuleNotFoundError:
            continue
        # get_sync_session
        monkeypatch.setattr(m, "get_sync_session", _fake_get_sync_session, raising=False)
        # SessionLocal
        monkeypatch.setattr(m, "SessionLocal", _Session_unit, raising=False)
        # engine
        monkeypatch.setattr(m, "engine", _sqlite_engine_unit, raising=False)


# ============================================================================
# UNIT-ONLY: Mock SlackProvider (provider + tasks)
# ============================================================================
@pytest.fixture
def mock_slack(request, monkeypatch):
    """
    Mocke l'envoi Slack pour les tests unitaires :
    - on capture les appels (liste 'calls' retournée par la fixture)
    - on force un succès pour éviter les retries Celery
    ⚠️ On patche à la fois le module provider ET notification_tasks (référence importée).
    """
    if not _is_unit(request):
        # En intégration/E2E, on ne mock pas par défaut.
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

    # 1) Patch du module provider (usage indirect)
    try:
        from app.infrastructure.notifications.providers import slack_provider  # type: ignore
        if hasattr(slack_provider, "SlackProvider"):
            monkeypatch.setattr(slack_provider, "SlackProvider", _MockProvider, raising=True)
        if hasattr(slack_provider, "send_slack"):
            monkeypatch.setattr(slack_provider, "send_slack", _fake_send_slack, raising=True)
    except Exception:
        pass

    # 2) Patch du module qui a importé la classe/fonction (référence figée)
    try:
        import app.workers.tasks.notification_tasks as nt  # type: ignore
        if hasattr(nt, "SlackProvider"):
            monkeypatch.setattr(nt, "SlackProvider", _MockProvider, raising=True)
        if hasattr(nt, "send_slack"):
            monkeypatch.setattr(nt, "send_slack", _fake_send_slack, raising=True)
    except Exception:
        pass

    return calls


# ============================================================================
# Payload factories simples (utiles pour tests API)
# ============================================================================
@pytest.fixture
def http_target_base_payload():
    return {
        "name": "t1",
        "url": "https://example.com/health",
        "method": "GET",
        "expected_status_code": 200,
        "timeout_seconds": 10,
        "check_interval_seconds": 60,
        "is_active": True,
    }


@pytest.fixture
def payload_factory(http_target_base_payload):
    def _factory(**overrides):
        data = {**http_target_base_payload}
        data.update(overrides)
        return data
    return _factory


# ============================================================================
# Cursor Postgres brut (INTÉGRATION seulement quand on l'utilise)
# ============================================================================
@pytest.fixture
def db_cursor():
    """
    Fournit un curseur psycopg transactionnel sur la base d'intégration.
    - Démarre une transaction avant le test et fait ROLLBACK après.
    - N'est utilisé que si injecté explicitement dans un test d'intégration.
    """
    import psycopg  # psycopg-binary doit être présent dans requirements-dev.txt

    dsn = os.getenv("PG_DSN", "postgresql://postgres:postgres@localhost:5432/app")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("BEGIN")
            try:
                yield cur
            finally:
                cur.execute("ROLLBACK")
