import uuid
import types
import asyncio
import pytest

# Ces tests sont purement unitaires (on appelle directement les fonctions des endpoints).
pytestmark = pytest.mark.unit


def _run(coro):
    """
    Exécute une coroutine sans nécessiter pytest-anyio/trio.
    - Essaye d'abord asyncio.run()
    - Si un event loop est déjà actif (rare selon config), fallback sur run_until_complete
    """
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(coro)


def test_dashboard_summary_keys(Session):
    """
    Vérifie que /dashboard (summary) renvoie bien les 3 clés attendues,
    et qu'elles valent 0 sur base vide.
    """
    from app.api.v1.endpoints.dashboard import summary

    with Session() as s:
        api_key = types.SimpleNamespace(client_id=uuid.uuid4())
        res = _run(summary(api_key=api_key, db=s))

        assert set(res.keys()) == {"total_machines", "open_incidents", "firing_alerts"}
        assert res["total_machines"] == 0
        assert res["open_incidents"] == 0
        assert res["firing_alerts"] == 0


def test_metrics_root_empty(Session):
    """
    Vérifie que GET /metrics (racine) renvoie un payload minimal conforme.
    """
    from app.api.v1.endpoints.metrics import list_metrics_root

    with Session() as s:
        api_key = types.SimpleNamespace(client_id=uuid.uuid4())
        res = _run(list_metrics_root(api_key=api_key, db=s))

        assert res == {"items": [], "total": 0}


def test_settings_get_settings_empty(Session):
    """
    Vérifie que /settings retourne {} lorsqu'aucun paramétrage client n'existe.
    """
    from app.api.v1.endpoints.settings import get_settings

    with Session() as s:
        api_key = types.SimpleNamespace(client_id=uuid.uuid4())
        res = _run(get_settings(api_key=api_key, db=s))

        assert isinstance(res, dict)
        assert res == {}
