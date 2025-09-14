# server/tests/integration/test_health.py
import os
import time
import pytest
import requests

pytestmark = pytest.mark.integration

if not os.getenv("INTEG_STACK_UP"):
    pytest.skip("Integration stack not running (export INTEG_STACK_UP=1)", allow_module_level=True)

API = os.getenv("API", "http://localhost:8000")
REQUEST_TIMEOUT = 5
HEALTH_TIMEOUT = 30
POLL_INTERVAL = 2

def _wait_health_ok(timeout=HEALTH_TIMEOUT, every=POLL_INTERVAL):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{API}/api/v1/health", timeout=REQUEST_TIMEOUT)
            if r.ok:
                return True
        except Exception:
            pass
        time.sleep(every)
    return False

def test_health_ok():
    assert _wait_health_ok(), f"Health check KO sur {API}"
