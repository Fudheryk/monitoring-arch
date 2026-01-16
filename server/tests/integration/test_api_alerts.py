# server/tests/integration/test_api_alerts.py
from __future__ import annotations

import os
import time
import uuid

import pytest
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

"""
Tests d'intégration: ingestion -> génération d'alertes -> lecture via API.

Conséquences sur ce test
------------------------
- On utilise X-API-Key uniquement pour l'ingestion.
- On récupère un cookie JWT (login admin) pour lire /api/v1/alerts.
  => ADMIN_EMAIL / ADMIN_PASSWORD requis (injectés par env/CI/provisioning).
"""

pytestmark = pytest.mark.integration

API = os.getenv("API", "http://localhost:8000")

ADMIN_EMAIL = (os.getenv("ADMIN_EMAIL") or "").strip()
ADMIN_PASSWORD = (os.getenv("ADMIN_PASSWORD") or "").strip()

# API key utilisée uniquement pour l'ingest
KEY = (os.getenv("KEY") or "").strip()

REQUEST_TIMEOUT = 10
HEALTH_TIMEOUT = 30
ALERT_TIMEOUT = 180
POLL_INTERVAL = 3


def _make_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.3,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST", "HEAD", "OPTIONS"}),
        raise_on_status=False,
    )
    s.mount("http://", HTTPAdapter(max_retries=retries))
    s.mount("https://", HTTPAdapter(max_retries=retries))
    return s


SESSION = _make_session()


def _wait(fn, timeout: int = 60, every: int = 2):
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


def _health_ok() -> bool:
    """
    Healthcheck PUBLIC : aucun header requis/autorisé.
    """
    try:
        r = SESSION.get(f"{API}/api/v1/health", timeout=5)
        return bool(r.ok)
    except Exception:
        return False


def _require_env(var: str) -> str:
    v = (os.getenv(var) or "").strip()
    if not v:
        raise RuntimeError(f"{var} env var is required for this integration test.")
    return v


def _login_ui_session() -> requests.Session:
    """
    Login admin pour obtenir le cookie JWT, utilisé pour lire les endpoints UI.
    """
    email = _require_env("ADMIN_EMAIL")
    password = _require_env("ADMIN_PASSWORD")

    s = requests.Session()
    r = s.post(
        f"{API}/api/v1/auth/login",
        json={"email": email, "password": password},
        timeout=10,
    )
    if r.status_code != 200:
        raise AssertionError(f"POST /api/v1/auth/login -> {r.status_code}, body={r.text}")

    # sanity : cookie ok
    me = s.get(f"{API}/api/v1/auth/me", timeout=10)
    if me.status_code != 200:
        raise AssertionError(f"GET /api/v1/auth/me -> {me.status_code}, body={me.text}")

    return s


def _ingest_headers(x_ingest_id: str) -> dict:
    """
    Headers strict header-only pour l'ingest.
    """
    key = _require_env("KEY")
    return {
        "X-API-Key": key,
        "Content-Type": "application/json",
        "X-Ingest-Id": x_ingest_id,
    }


def _firing_for_machine(ui: requests.Session, machine_hostname: str):
    """
    Liste les alertes FIRING liées à la machine de test.

    IMPORTANT : /api/v1/alerts est un endpoint UI (JWT cookie).
    """
    r = ui.get(f"{API}/api/v1/alerts", timeout=REQUEST_TIMEOUT)
    r.raise_for_status()

    alerts = r.json() or []
    # Filtrer sur la machine du test ; fallback via message s’il n’y a pas le hostname
    alerts = [
        a
        for a in alerts
        if a.get("machine", {}).get("hostname") == machine_hostname
        or machine_hostname in (a.get("message") or "")
    ]
    return [a for a in alerts if a.get("status") == "FIRING"]


def test_alerts_firing_on_cpu_spike():
    # 0) API up
    assert _wait(_health_ok, timeout=HEALTH_TIMEOUT, every=2), "API/stack not ready"

    # 1) Ingestion d’un spike CPU (hostname unique par run)
    machine_hostname = f"it-{uuid.uuid4().hex[:12]}"
    payload = {
        "machine": {"hostname": machine_hostname, "os": "linux"},
        # NB: ton schema d'ingest normalise via AgentMetricIn.
        # Ici on envoie directement un format simple; adapte si nécessaire.
        "metrics": [{"name": "cpu_load", "type": "numeric", "value": 3.3, "unit": "ratio"}],
        # sent_at: on laisse le serveur choisir (si ton schema accepte fallback now UTC)
        "sent_at": None,
    }

    ingest_id = f"it-{uuid.uuid4().hex}"
    r = SESSION.post(
        f"{API}/api/v1/ingest/metrics",
        headers=_ingest_headers(ingest_id),
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    assert r.status_code in (200, 202), f"ingest failed: {r.status_code} {r.text}"

    # 2) Attendre une alerte FIRING pour CETTE machine via endpoints UI (JWT cookie)
    ui = _login_ui_session()
    firing = _wait(lambda: _firing_for_machine(ui, machine_hostname), timeout=ALERT_TIMEOUT, every=POLL_INTERVAL)
    assert firing, f"Aucune alerte FIRING détectée pour {machine_hostname}"

    # 3) Vérifs de forme
    a0 = firing[0]
    assert a0.get("id"), a0
    assert a0.get("status") == "FIRING"
    assert a0.get("severity") in {"warning", "error", "critical"}
