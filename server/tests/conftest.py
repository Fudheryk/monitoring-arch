# server/tests/conftest.py
import os
import importlib
import pkgutil
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


# -------- Helpers --------
def _is_unit(request: pytest.FixtureRequest) -> bool:
    """True si le test courant est marqué @pytest.mark.unit."""
    return request.node.get_closest_marker("unit") is not None


# -------- ENV par défaut (unit only) --------
@pytest.fixture(autouse=True)
def unit_env(request):
    """
    En unit : fixe des ENV sûres pour ne jamais appeler l'extérieur.
    Hors unit : ne fait rien.
    """
    if not _is_unit(request):
        return
    os.environ.setdefault("SLACK_WEBHOOK", "http://example.invalid/webhook")
    os.environ.setdefault("SLACK_DEFAULT_CHANNEL", "#notif-webhook")
    os.environ.setdefault("ALERT_REMINDER_MINUTES", "1")
    # Important : ne pas toucher DATABASE_URL ici (intégration/e2e utilisent la vraie DB)


# -------- Celery eager (unit only) --------
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

    from app.workers.celery_app import celery
    prev_always = celery.conf.task_always_eager
    prev_propag = celery.conf.task_eager_propagates
    celery.conf.task_always_eager = True
    celery.conf.task_eager_propagates = True
    try:
        yield
    finally:
        celery.conf.task_always_eager = prev_always
        celery.conf.task_eager_propagates = prev_propag


# -------- SQLite in-memory partagé (unit only) --------
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
    from app.infrastructure.persistence.database import base as db_base
    from app.infrastructure.persistence.database import models as models_pkg

    for _finder, name, _ispkg in pkgutil.walk_packages(
        models_pkg.__path__, models_pkg.__name__ + "."
    ):
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
    """
    Fournit un sessionmaker à utiliser comme `with Session() as s:` pour les tests unitaires.
    Sera *skippé* s'il est injecté dans un test non marqué @unit (sécurité d'usage).
    """
    if not _is_unit(request):
        pytest.skip("Session fixture is only available for unit tests")
    return _Session_unit


# -------- Purge DB entre tests unitaires (évite les fuites d'état) --------
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
    from app.infrastructure.persistence.database import base as db_base
    with _Session_unit() as s:
        for table in reversed(db_base.Base.metadata.sorted_tables):
            s.execute(table.delete())
        s.commit()


# -------- Patch get_sync_session (unit only) --------
@pytest.fixture(autouse=True)
def patch_get_sync_session(request, monkeypatch, _Session_unit):
    """
    Remplace get_sync_session par une version basée sur SQLite in-memory
    pour tout le code appelé pendant les tests unitaires.
    """
    if not _is_unit(request):
        return

    @contextmanager
    def _fake_get_sync_session():
        with _Session_unit() as s:
            yield s

    # 1) Patch sur le module source
    import app.infrastructure.persistence.database.session as sess_mod
    monkeypatch.setattr(sess_mod, "get_sync_session", _fake_get_sync_session, raising=True)

    # 2) Patch sur les modules qui auraient importé une référence figée
    targets = [
        "app.workers.tasks.notification_tasks",
        "app.workers.tasks.evaluation_tasks",
        "app.workers.tasks.ingest_tasks",
        "app.workers.tasks.http_monitoring_tasks",
        # ajoute d'autres modules au besoin
    ]
    for modname in targets:
        try:
            mod = importlib.import_module(modname)
        except ModuleNotFoundError:
            continue
        if hasattr(mod, "get_sync_session"):
            monkeypatch.setattr(mod, "get_sync_session", _fake_get_sync_session, raising=True)


# -------- Mock SlackProvider (unit only) --------
@pytest.fixture
def mock_slack(request, monkeypatch):
    """
    Mocke l'envoi Slack pour les tests unitaires :
    - on capture les appels (liste 'calls' retournée par la fixture)
    - on force un succès pour éviter les retries Celery
    ⚠️ On patche à la fois le module provider ET notification_tasks (référence importée).
    """
    if not _is_unit(request):
        # En intégration/e2e, on ne mock pas par défaut.
        return None

    calls = []

    class _Mock:
        def send(self, **kw):
            calls.append(kw)
            return True

    # 1) Patch du module provider (usage indirect)
    from app.infrastructure.notifications.providers import slack_provider
    monkeypatch.setattr(slack_provider, "SlackProvider", _Mock, raising=True)

    # 2) Patch du module qui a importé la classe (référence figée)
    import app.workers.tasks.notification_tasks as nt
    monkeypatch.setattr(nt, "SlackProvider", _Mock, raising=True)

    return calls
