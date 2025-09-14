# /server/tests/integration/test_endpoints_smoke.py
# Smoke léger : vérifie que les endpoints principaux répondent (200/204 ou 401/403/405 si protégés).
# La route "targets" est détectée dynamiquement via la fixture targets_base.

import pytest

pytestmark = pytest.mark.integration

# Endpoints "simples" (hors 'targets' qui est dynamique)
ENDPOINTS = [
    ("GET", "/api/v1/alerts"),
    ("GET", "/api/v1/dashboard/summary"),
    ("GET", "/api/v1/incidents"),
    ("GET", "/api/v1/machines"),
    ("GET", "/api/v1/metrics"),
    ("GET", "/api/v1/settings"),
    ("GET", "/api/v1/health"),
]

@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_endpoints_smoke_basic(session_retry, api_base, api_headers, method, path):
    r = session_retry.request(method, f"{api_base}{path}", headers=api_headers)
    # Autoriser 200/204, mais aussi 401/403/405 si le routeur existe mais protégé/méthode non permise
    assert r.status_code in (200, 204, 401, 403, 405), (path, r.status_code, r.text[:200])

def test_endpoints_smoke_targets(session_retry, api_base, api_headers, targets_base):
    r = session_retry.get(f"{api_base}{targets_base}", headers=api_headers)
    assert r.status_code in (200, 401, 403, 405), (targets_base, r.status_code, r.text[:200])
