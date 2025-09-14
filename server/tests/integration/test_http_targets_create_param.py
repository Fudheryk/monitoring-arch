# server/tests/integration/test_http_targets_create_param.py
# Variante paramétrée simple (utilise la route détectée 'targets_base').
# Correction : cas 201 utilisent une URL unique pour éviter les 409.

import uuid
import pytest

pytestmark = pytest.mark.integration


def _payload(**overrides):
    base = {
        "name": "t1",
        "url": "https://example.com/health",  # remplacée au besoin dans le test par une URL unique
        "method": "GET",
        "expected_status_code": 200,
        "timeout_seconds": 10,
        "check_interval_seconds": 60,
        "is_active": True,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("override,expected", [
    ({}, 201),
    ({"method": "post"}, 201),
    ({"expected_status_code": 404}, 201),
    ({"expected_status_code": 99}, 422),
    ({"url": "not-a-url"}, 422),
    ({"method": "INVALID"}, 422),
])
def test_create_http_target_param(session_retry, api_base, api_headers, targets_base, override, expected):
    # Génère une URL unique pour les cas attendus 201, afin d'éviter tout conflit 409.
    unique_url = f"https://httpbin.org/status/200?rnd={uuid.uuid4().hex[:10]}"

    body = _payload(**override)
    if expected == 201 and "url" not in override:
        body["url"] = unique_url  # only for 201 cases without explicit url

    r = session_retry.post(f"{api_base}{targets_base}", json=body, headers=api_headers)
    assert r.status_code == expected, f"{r.status_code} {r.text}"
