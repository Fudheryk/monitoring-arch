# server/tests/e2e/test_e2e_incident_lifecycle.py
from __future__ import annotations

import os
import uuid
import pytest
import requests

API = os.getenv("API", "http://localhost:8000")
KEY = os.getenv("KEY", "dev-apikey-123")
H = {"X-API-Key": KEY}

pytestmark = pytest.mark.e2e


def _targets_base() -> str:
    """
    Détecte dynamiquement la route des cibles :
    essaie /api/v1/targets, /api/v1/http-targets, /api/v1/http_targets,
    puis, en dernier recours, lit l'OpenAPI.
    """
    candidates = [
        "/api/v1/targets",
        "/api/v1/http-targets",
        "/api/v1/http_targets",
    ]
    for c in candidates:
        try:
            r = requests.options(f"{API}{c}", headers=H, timeout=5)
            if r.status_code in (200, 204, 401, 403, 405):
                return c
        except Exception:
            pass
        try:
            r = requests.get(f"{API}{c}", headers=H, timeout=5)
            if r.status_code in (200, 401, 403, 405):
                return c
        except Exception:
            pass

    # Fallback : OpenAPI
    try:
        r = requests.get(f"{API}/openapi.json", headers=H, timeout=5)
        if r.ok:
            paths = r.json().get("paths", {})
            for c in candidates:
                if c in paths:
                    return c
            # heuristique : prend le 1er chemin contenant "target"
            for p in paths.keys():
                if "target" in p:
                    return p
    except Exception:
        pass

    raise RuntimeError("Impossible de localiser la route des targets.")


def _fake_http_get(url, method="GET", timeout=10):
    """Simule un DOWN systématique côté service."""
    class Resp:
        status_code = 500
        text = "fake failure"
    return Resp()


@pytest.mark.e2e
def test_e2e_alert_flow_with_monkeypatch(monkeypatch):
    """
    E2E pragmatique :
    - POST d'une cible via l'API (route détectée dynamiquement)
    - monkeypatch http_get -> 500
    - monkeypatch notify.apply_async pour ne PAS toucher le broker
    - appel direct du service check_http_targets() (travaille sur la même DB)
    - vérif via /api/v1/incidents que l'incident a été ouvert
    """

    # Forcer les tests host à parler au Postgres exposé par Docker (localhost:5432)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:5432/monitoring?connect_timeout=5",
    )

    # 1) route des targets
    base = _targets_base()  # ex: /api/v1/http-targets

    # 2) crée une cible (peu importe l’URL réelle, on va monkeypatcher la requête)
    name = f"e2e-fail-{uuid.uuid4()}"
    payload = {
        "name": name,
        "url": "https://example.com/health?rnd={uuid.uuid4()}",
        "method": "GET",
        "expected_status_code": 200,
        "timeout_seconds": 5,
        "check_interval_seconds": 60,
        "is_active": True,
    }
    r = requests.post(f"{API}{base}", json=payload, headers=H, timeout=5)
    if r.status_code in (200, 201):
        tid = r.json()["id"]
    elif r.status_code == 409:
        # La cible existe déjà : on réutilise son id
        tid = (r.json().get("detail") or {}).get("existing_id")
        assert tid, f"409 sans existing_id dans la réponse: {r.text}"
    else:
        raise AssertionError(f"POST {base} → {r.status_code}, body={r.text}")

    # 3) monkeypatch du HTTP interne + de la notification Celery
    #    (afin d'éviter toute dépendance réseau et forcer un DOWN)
    monkeypatch.setattr(
        "app.application.services.http_monitor_service.http_get",
        _fake_http_get,
        raising=True,
    )

    import app.workers.tasks.notification_tasks as nt

    enqueues = []

    def _fake_apply_async(*args, **kw):
        enqueues.append({"args": args, "kwargs": kw.get("kwargs"), "queue": kw.get("queue")})
        class _Res: id = "fake-task-id"
        return _Res()

    monkeypatch.setattr(nt.notify, "apply_async", _fake_apply_async, raising=True)

    # 4) appel DIRECT au service (même DB que l'API Docker)
    from app.application.services.http_monitor_service import check_http_targets
    updated = check_http_targets()
    assert updated >= 1, "Le service aurait dû checker au moins 1 cible"

    # 5) vérifie côté API qu’un incident a été ouvert
    #    (plus stable que /alerts pour ce flux)
    inc = requests.get(f"{API}/api/v1/incidents", headers=H, timeout=5)
    assert inc.status_code == 200, f"GET /api/v1/incidents → {inc.status_code}"
    items = inc.json() or []
    assert any(
        (it.get("title") or "").startswith("HTTP check failed:") and it.get("status") in ("OPEN", "DOWN")
        for it in items
    ), f"Aucun incident 'HTTP check failed: ...' trouvé dans {items}"

    # 6) on a bien tenté une notif (enqueues capturés)
    assert len(enqueues) >= 1, "Aucune notification enfilée (cooldown?)"
