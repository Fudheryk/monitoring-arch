# server/tests/e2e/test_end_to_end_notify.py
import os
import uuid
import pytest

pytestmark = pytest.mark.e2e

# Skip propre si la stack n'est pas démarrée
if not os.getenv("E2E_STACK_UP"):
    pytest.skip("E2E stack not running (export E2E_STACK_UP=1)", allow_module_level=True)

REQUEST_TIMEOUT = 10
HEALTH_TIMEOUT = 30
ALERT_TIMEOUT = 180

def _health_ok(session, api_base):
    r = session.get(f"{api_base}/api/v1/health", timeout=5)
    return r.ok

def _get_firing_for_machine(session, api_base, headers, machine_id):
    r = session.get(f"{api_base}/api/v1/alerts", headers=headers, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    alerts = r.json()
    # Filtrer sur la machine du test si dispo dans la réponse ; fallback via message
    alerts = [a for a in alerts if a.get("machine", {}).get("hostname") == machine_id
              or machine_id in (a.get("message") or "")]
    return [a for a in alerts if a.get("status") == "FIRING"]

def test_end_to_end_ingest_to_alert(session_retry, api_base, api_headers, wait):
    # 1) API up
    assert wait(lambda: _health_ok(session_retry, api_base), timeout=HEALTH_TIMEOUT), "Health check KO"

    # 2) Ingestion
    machine_id = f"e2e-{uuid.uuid4().hex[:8]}"
    payload = {
        "machine": {"hostname": machine_id, "os": "linux"},
        "metrics": [{"name": "cpu_load", "type": "numeric", "value": 3.3, "unit": "ratio"}],
        "sent_at": None,
    }
    r = session_retry.post(f"{api_base}/api/v1/ingest/metrics",
                           headers=api_headers, json=payload, timeout=REQUEST_TIMEOUT)
    assert r.status_code in (200, 202), f"ingest failed: {r.status_code} {r.text}"

    # 3) Alerte FIRING attendue pour CETTE machine
    firing = wait(lambda: _get_firing_for_machine(session_retry, api_base, api_headers, machine_id),
                  timeout=ALERT_TIMEOUT, every=3)
    assert firing, f"Aucune alerte FIRING pour {machine_id}"
