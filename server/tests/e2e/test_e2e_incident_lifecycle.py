from __future__ import annotations

import os
import uuid

import pytest
import requests

"""
E2E: flux d'alerte HTTP (HTTP targets) + ouverture incident.

Contexte auth (migration)
-------------------------
- Les endpoints UI (http-targets, incidents, etc.) sont en JWT cookie.

Conséquence :
- Il doit obtenir un cookie JWT via /api/v1/auth/login, puis réutiliser ce cookie.

Hypothèses :
- Un compte admin existe (provisioning) et ses identifiants sont fournis par env :
    ADMIN_EMAIL, ADMIN_PASSWORD
- L'API est joignable via API (base URL)
- La stack E2E est up si E2E_STACK_UP=1
"""

# ✅ IMPORTANT: ne pas réassigner pytestmark deux fois ; on cumule les marqueurs
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.getenv("E2E_STACK_UP") != "1",
        reason="E2E stack not running (export E2E_STACK_UP=1)",
    ),
]

API = os.getenv("API", "http://localhost:8000")

# Creds admin pour login JWT cookie (obligatoire pour endpoints UI)
ADMIN_EMAIL = (os.getenv("ADMIN_EMAIL") or "").strip()
ADMIN_PASSWORD = (os.getenv("ADMIN_PASSWORD") or "").strip()


def _require_admin_creds() -> None:
    """
    Fail-fast : ce test dépend d'un login admin pour récupérer un cookie JWT.
    """
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        raise RuntimeError(
            "ADMIN_EMAIL et ADMIN_PASSWORD sont requis pour ce test E2E "
            "(endpoints UI en JWT cookie)."
        )


def _login_session() -> requests.Session:
    """
    Crée une session requests authentifiée (cookie JWT) via /api/v1/auth/login.

    NOTE: on utilise une Session pour conserver automatiquement les cookies.
    """
    _require_admin_creds()

    s = requests.Session()
    r = s.post(
        f"{API}/api/v1/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=10,
    )
    if r.status_code != 200:
        raise AssertionError(f"POST /api/v1/auth/login -> {r.status_code}, body={r.text}")

    # Sanity: /api/v1/auth/me doit marcher avec la session (cookie présent)
    me = s.get(f"{API}/api/v1/auth/me", timeout=10)
    if me.status_code != 200:
        raise AssertionError(f"GET /api/v1/auth/me -> {me.status_code}, body={me.text}")

    return s


def _targets_base(s: requests.Session) -> str:
    """
    Détecte dynamiquement la route des cibles HTTP (UI).

    Historique :
    - certains projets ont renommé /targets -> /http-targets -> /http_targets

    Comme c'est de l'UI (JWT cookie), on doit utiliser la session `s`
    (cookie auth), pas de X-API-Key.
    """
    candidates = [
        "/api/v1/targets",
        "/api/v1/http-targets",
        "/api/v1/http_targets",
    ]

    for c in candidates:
        # OPTIONS peut être 405 selon l'impl, on accepte 200/204/401/403/405 pour "existe"
        try:
            r = s.options(f"{API}{c}", timeout=5)
            if r.status_code in (200, 204, 401, 403, 405):
                return c
        except Exception:
            pass

        # GET devrait renvoyer 200 si ok, ou 401/403 si auth/role, ou 405 si pas GET
        try:
            r = s.get(f"{API}{c}", timeout=5)
            if r.status_code in (200, 401, 403, 405):
                return c
        except Exception:
            pass

    # Fallback : OpenAPI (PUBLIC ou JWT selon config ; on tente avec session)
    try:
        r = s.get(f"{API}/openapi.json", timeout=5)
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


def _fake_http_get(url, method="GET", timeout=10):  # noqa: ARG001
    """
    Simule un DOWN systématique côté service (remplace l'appel réseau réel).
    """
    class Resp:
        status_code = 500
        text = "fake failure"

    return Resp()


def test_e2e_alert_flow_with_monkeypatch(monkeypatch):
    """
    E2E pragmatique :
    - login admin -> cookie JWT (Session requests)
    - POST d'une cible via l'API UI (route détectée dynamiquement)
    - monkeypatch http_get -> 500 (force l'échec des checks)
    - monkeypatch notify.apply_async pour ne PAS toucher le broker
    - appel direct du service check_http_targets() (travaille sur la même DB)
    - vérif via /api/v1/incidents (UI, JWT cookie) que l'incident a été ouvert
    """

    # Forcer les tests host à parler au Postgres exposé par Docker (localhost:5432)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:5432/monitoring?connect_timeout=5",
    )

    # Recharger la config + la session DB pour prendre en compte DATABASE_URL
    import importlib
    import app.core.config as cfg
    import app.infrastructure.persistence.database.session as sess

    importlib.reload(cfg)
    importlib.reload(sess)

    # Session HTTP authentifiée (cookie JWT)
    s = _login_session()

    # 1) route des targets (UI)
    base = _targets_base(s)  # ex: /api/v1/http-targets

    # 2) créer une cible (URL arbitraire : on monkeypatch la requête interne)
    name = f"e2e-fail-{uuid.uuid4()}"
    payload = {
        "name": name,
        "url": f"https://example.com/health?rnd={uuid.uuid4()}",
        "method": "GET",
        "expected_status_code": 200,
        "timeout_seconds": 5,
        "check_interval_seconds": 60,
        "is_active": True,
    }

    r = s.post(f"{API}{base}", json=payload, timeout=10)
    if r.status_code in (200, 201):
        tid = r.json()["id"]  # noqa: F841
    elif r.status_code == 409:
        tid = (r.json().get("detail") or {}).get("existing_id")
        assert tid, f"409 sans existing_id dans la réponse: {r.text}"
    else:
        raise AssertionError(f"POST {base} -> {r.status_code}, body={r.text}")

    # 3) monkeypatch du HTTP interne + de la notification Celery
    monkeypatch.setattr(
        "app.application.services.http_monitor_service.http_get",
        _fake_http_get,
        raising=True,
    )

    import app.workers.tasks.notification_tasks as nt

    enqueues = []

    def _fake_apply_async(*args, **kw):
        enqueues.append({"args": args, "kwargs": kw.get("kwargs"), "queue": kw.get("queue")})

        class _Res:
            id = "fake-task-id"

        return _Res()

    monkeypatch.setattr(nt.notify, "apply_async", _fake_apply_async, raising=True)

    # 4) appel DIRECT au service (même DB que l'API Docker)
    from app.application.services.http_monitor_service import check_http_targets

    updated = check_http_targets()
    assert updated >= 1, "Le service aurait dû checker au moins 1 cible"

    # 5) vérifier côté API (UI, cookie JWT) qu’un incident a été ouvert
    inc = s.get(f"{API}/api/v1/incidents", timeout=10)
    assert inc.status_code == 200, f"GET /api/v1/incidents -> {inc.status_code}, body={inc.text}"
    items = inc.json() or []
    assert any(
        (it.get("title") or "").startswith("HTTP check failed:") and it.get("status") in ("OPEN", "DOWN")
        for it in items
    ), f"Aucun incident 'HTTP check failed: ...' trouvé dans {items}"

    # 6) on a bien tenté une notif (enqueues capturés)
    assert len(enqueues) >= 1, "Aucune notification enfilée (cooldown?)"
