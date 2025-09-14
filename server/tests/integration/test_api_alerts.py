# server/tests/integration/test_api_alerts.py
import os
import time
import uuid
import pytest
import requests
from requests.adapters import HTTPAdapter, Retry

pytestmark = pytest.mark.integration

API = os.getenv("API", "http://localhost:8000")
KEY = os.getenv("KEY", "dev-apikey-123")
HDR = {
    "X-API-Key": KEY,
    "Content-Type": "application/json",
    "X-Ingest-Id": f"it-{uuid.uuid4().hex}",
}

REQUEST_TIMEOUT = 10
HEALTH_TIMEOUT = 30
ALERT_TIMEOUT = 180
POLL_INTERVAL = 3

def _make_session():
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

def _wait(fn, timeout=60, every=2):
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

def _health_ok():
    try:
        r = SESSION.get(f"{API}/api/v1/health", timeout=5)
        return r.ok
    except Exception:
        return False

def _firing_for_machine(machine_id: str):
    r = SESSION.get(f"{API}/api/v1/alerts", headers=HDR, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    alerts = r.json()
    # Filtrer sur la machine du test ; fallback via message s’il n’y a pas le hostname
    alerts = [
        a for a in alerts
        if a.get("machine", {}).get("hostname") == machine_id
        or machine_id in (a.get("message") or "")
    ]
    return [a for a in alerts if a.get("status") == "FIRING"]

def test_alerts_firing_on_cpu_spike():
    # 0) API up
    assert _wait(_health_ok, timeout=HEALTH_TIMEOUT, every=2), "API/stack not ready"

    # 1) Ingestion d’un spike CPU (hostname unique par run)
    machine_id = "test-server"
    payload = {
        "machine": {"hostname": machine_id, "os": "linux"},
        "metrics": [{"name": "cpu_load", "type": "numeric", "value": 3.3, "unit": "ratio"}],
        "sent_at": None,
    }
    r = SESSION.post(f"{API}/api/v1/ingest/metrics", headers=HDR, json=payload, timeout=REQUEST_TIMEOUT)
    assert r.status_code in (200, 202), f"ingest failed: {r.status_code} {r.text}"

    # 2) Attendre une alerte FIRING pour CETTE machine
    firing = _wait(lambda: _firing_for_machine(machine_id), timeout=ALERT_TIMEOUT, every=POLL_INTERVAL)
    assert firing, f"Aucune alerte FIRING détectée pour {machine_id}"

    # 3) Vérifs de forme
    a0 = firing[0]
    assert "id" in a0 and a0["id"], a0
    assert a0["status"] == "FIRING"
    assert a0.get("severity") in {"warning", "error", "critical"}
