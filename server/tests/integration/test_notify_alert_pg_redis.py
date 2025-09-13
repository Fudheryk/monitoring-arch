# server/tests/integration/test_notify_alert_with_real_stack.py
"""
Test d'intégration : ingestion de métriques -> alerte FIRING avec la stack réelle.

Prérequis :
  - API, DB, Redis et worker Celery démarrés
  - Clé API valide (KEY)

Variables d’environnement utiles :
  - INTEG_STACK_UP=1  -> exécuter (sinon skip)
  - API=http://localhost:8000
  - KEY=dev-apikey-123
"""

import os
import time
import uuid
import pytest
import requests
from requests.adapters import HTTPAdapter, Retry

pytestmark = pytest.mark.integration

# Skip propre si la stack n'est pas démarrée
if not os.getenv("INTEG_STACK_UP"):
    pytest.skip("Integration stack not running (export INTEG_STACK_UP=1)", allow_module_level=True)

# --- Config -------------------------------------------------------------------
API = os.getenv("API", "http://localhost:8000")
KEY = os.getenv("KEY", "dev-apikey-123")
HDR = {"X-API-Key": KEY, "Content-Type": "application/json"}

REQUEST_TIMEOUT = 10
HEALTH_TIMEOUT = 30
ALERT_TIMEOUT = 180
POLL_INTERVAL = 3

# --- Session HTTP avec retries (limite les flakes réseau) ---------------------
def _make_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.3,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST", "HEAD", "OPTIONS"]),
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
    """/health répond OK ?"""
    try:
        r = SESSION.get(f"{API}/api/v1/health", timeout=REQUEST_TIMEOUT)
        return r.ok
    except Exception:
        return False

def _firing_for_machine(machine_id: str):
    """Liste des alertes FIRING filtrées pour la machine du test."""
    r = SESSION.get(f"{API}/api/v1/alerts", headers=HDR, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    alerts = r.json()
    # Filtrer sur la machine du test ; fallback via message si le hostname n'est pas exposé
    alerts = [
        a for a in alerts
        if a.get("machine", {}).get("hostname") == machine_id
        or machine_id in (a.get("message") or "")
    ]
    return [a for a in alerts if a.get("status") == "FIRING"]

# --- Test ---------------------------------------------------------------------
def test_notify_alert_with_real_stack():
    # 0) API up
    assert _wait(_health_ok, timeout=HEALTH_TIMEOUT, every=POLL_INTERVAL), f"API /health KO sur {API}"

    # 1) Ingestion d’un spike CPU (hostname unique par run)
    machine_id = f"it2-{uuid.uuid4().hex[:8]}"
    payload = {
        "machine": {"hostname": machine_id, "os": "linux"},
        "metrics": [{"name": "cpu_load", "type": "numeric", "value": 3.3, "unit": "ratio"}],  # ajuste au seuil réel
        "sent_at": None,
    }
    r = SESSION.post(f"{API}/api/v1/ingest/metrics", headers=HDR, json=payload, timeout=REQUEST_TIMEOUT)
    assert r.status_code in (200, 202), f"Ingest failed: {r.status_code} {r.text}"

    # 2) Attendre une alerte FIRING pour CETTE machine
    firing = _wait(lambda: _firing_for_machine(machine_id), timeout=ALERT_TIMEOUT, every=POLL_INTERVAL)
    assert firing, f"Aucune alerte FIRING détectée pour {machine_id} ; vérifier worker/redis/db"
