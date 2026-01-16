# server/tests/integration/test_notify_alert_with_real_stack.py
from __future__ import annotations

"""
Test d'intégration : ingestion de métriques -> alerte FIRING avec la stack réelle.

Prérequis
---------
- API, DB, Redis et worker Celery démarrés
- Provisioning réalisé : un compte admin + au moins une API key existent

Variables d’environnement utiles
--------------------------------
- INTEG_STACK_UP=1  -> exécuter (sinon skip)
- API=http://localhost:8000
- KEY=<API_KEY>                 -> requis pour l'ingest
- ADMIN_EMAIL=<email admin>     -> requis pour lire /alerts
- ADMIN_PASSWORD=<mdp admin>    -> requis pour lire /alerts
"""

import os
import time
import uuid

import pytest
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

pytestmark = pytest.mark.integration

# Skip propre si la stack n'est pas démarrée
if os.getenv("INTEG_STACK_UP") != "1":
    pytest.skip("Integration stack not running (export INTEG_STACK_UP=1)", allow_module_level=True)

# --- Config -------------------------------------------------------------------
API = os.getenv("API", "http://localhost:8000")

# API key : uniquement pour l'ingest (strict header-only)
KEY = (os.getenv("KEY") or "").strip()

# Creds admin : nécessaires pour accéder aux endpoints UI (JWT cookie)
ADMIN_EMAIL = (os.getenv("ADMIN_EMAIL") or "").strip()
ADMIN_PASSWORD = (os.getenv("ADMIN_PASSWORD") or "").strip()

REQUEST_TIMEOUT = 10
HEALTH_TIMEOUT = 30
ALERT_TIMEOUT = 180
POLL_INTERVAL = 3


def _require_env(var: str) -> str:
    v = (os.getenv(var) or "").strip()
    if not v:
        raise RuntimeError(f"{var} env var is required for this integration test.")
    return v


# --- Session HTTP avec retries (limite les flakes réseau) ---------------------
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


# --- Helpers ------------------------------------------------------------------
def _wait(fn, timeout: int, every: int):
    """Poll une fonction jusqu’à valeur truthy ou timeout."""
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
    """/health répond OK ? (PUBLIC, sans auth)"""
    try:
        r = SESSION.get(f"{API}/api/v1/health", timeout=REQUEST_TIMEOUT)
        return bool(r.ok)
    except Exception:
        return False


def _login_ui_session() -> requests.Session:
    """
    Login admin pour obtenir le cookie JWT, utilisé pour lire /api/v1/alerts.
    """
    email = _require_env("ADMIN_EMAIL")
    password = _require_env("ADMIN_PASSWORD")

    s = requests.Session()
    r = s.post(
        f"{API}/api/v1/auth/login",
        json={"email": email, "password": password},
        timeout=REQUEST_TIMEOUT,
    )
    if r.status_code != 200:
        raise AssertionError(f"POST /api/v1/auth/login -> {r.status_code}, body={r.text}")

    # sanity : cookie ok
    me = s.get(f"{API}/api/v1/auth/me", timeout=REQUEST_TIMEOUT)
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
    Liste des alertes FIRING filtrées pour la machine du test.

    IMPORTANT : /api/v1/alerts est un endpoint UI (JWT cookie).
    """
    r = ui.get(f"{API}/api/v1/alerts", timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    alerts = r.json() or []

    # Filtrer sur la machine du test ; fallback via message si le hostname n'est pas exposé
    alerts = [
        a
        for a in alerts
        if a.get("machine", {}).get("hostname") == machine_hostname
        or machine_hostname in (a.get("message") or "")
    ]
    return [a for a in alerts if a.get("status") == "FIRING"]


# --- Test ---------------------------------------------------------------------
def test_notify_alert_with_real_stack():
    # 0) API up
    assert _wait(_health_ok, timeout=HEALTH_TIMEOUT, every=POLL_INTERVAL), f"API /health KO sur {API}"

    # 1) Ingestion d’un spike CPU (hostname unique par run)
    machine_hostname = f"it2-{uuid.uuid4().hex[:8]}"
    payload = {
        "machine": {"hostname": machine_hostname, "os": "linux"},
        # Ajuste au seuil réel si besoin
        "metrics": [{"name": "cpu_load", "type": "numeric", "value": 3.3, "unit": "ratio"}],
        # sent_at : laissé au serveur si ton schema applique un fallback "now UTC"
        "sent_at": None,
    }

    ingest_id = f"it2-{uuid.uuid4().hex}"
    r = SESSION.post(
        f"{API}/api/v1/ingest/metrics",
        headers=_ingest_headers(ingest_id),
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    assert r.status_code in (200, 202), f"Ingest failed: {r.status_code} {r.text}"

    # 2) Attendre une alerte FIRING pour CETTE machine via endpoint UI (JWT cookie)
    ui = _login_ui_session()
    firing = _wait(lambda: _firing_for_machine(ui, machine_hostname), timeout=ALERT_TIMEOUT, every=POLL_INTERVAL)
    assert firing, f"Aucune alerte FIRING détectée pour {machine_hostname} ; vérifier worker/redis/db"
