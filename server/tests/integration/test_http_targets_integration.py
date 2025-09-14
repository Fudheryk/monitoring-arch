# server/tests/integration/test_http_targets_integration.py
"""
Tests d’intégration live contre l’API.
- Utilise 'targets_base' pour s’adapter à /api/v1/targets ou /api/v1/http-targets.
- Couverture : validation, création, listing, idempotence, concurrence.
- Correction : les tests créant des cibles avec statut attendu 201 utilisent une URL UNIQUE
  (suffixe aléatoire) pour éviter les conflits 409 "URL already exists".
"""

from __future__ import annotations

import json
import random
import string
import concurrent.futures as futures
from typing import Tuple

import pytest

pytestmark = pytest.mark.integration

REQUEST_TIMEOUT = 10
LIST_TIMEOUT = 10


def _unique_suffix(n: int = 12) -> str:
    """Génère un suffixe alphanumérique pour éviter les collisions entre runs."""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _payload(name: str, url: str) -> dict:
    """Payload minimal valide pour créer une http-target."""
    return {
        "name": name,
        "url": url,
        "method": "GET",
        "expected_status_code": 200,
        "timeout_seconds": 10,
        "check_interval_seconds": 60,
        "is_active": True,
    }


def _post_target(session_retry, api_base, api_headers, targets_base, body: dict) -> Tuple[int, dict]:
    """POST sur la route détectée et renvoie (status_code, payload_json_ou_raw)."""
    r = session_retry.post(f"{api_base}{targets_base}", headers=api_headers, json=body, timeout=REQUEST_TIMEOUT)
    try:
        data = r.json()
    except Exception:
        data = {"_raw": r.text}
    return r.status_code, data


def test_concurrent_create_then_conflict(session_retry, api_base, api_headers, targets_base):
    """
    Deux POST concurrents sur la même URL :
      - on attend un 201 et un 409 (ordre indifférent)
      - le 409.detail.existing_id == id retourné par le 201
      - un POST supplémentaire identique retourne 409 (idempotence)
    """
    unique = _unique_suffix()
    url = f"https://httpbin.org/status/500?rnd={unique}"
    body = _payload(name=f"Concurrent {unique}", url=url)

    with futures.ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(_post_target, session_retry, api_base, api_headers, targets_base, body)
        f2 = ex.submit(_post_target, session_retry, api_base, api_headers, targets_base, body)
        s1, d1 = f1.result()
        s2, d2 = f2.result()

    statuses = {s1, s2}
    assert statuses == {201, 409}, f"expected {{201,409}}, got {s1},{s2}; d1={d1}, d2={d2}"

    # Récupère l'id créé (du 201) et l'existing_id (du 409)
    if s1 == 201:
        created_id, conflict_detail = d1.get("id"), d2.get("detail", {})
    else:
        created_id, conflict_detail = d2.get("id"), d1.get("detail", {})

    assert created_id and isinstance(created_id, str)
    assert conflict_detail and conflict_detail.get("existing_id") == created_id

    # Idempotence : re-POST identique => 409 avec existing_id égal
    s3, d3 = _post_target(session_retry, api_base, api_headers, targets_base, body)
    assert s3 == 409, f"expected 409, got {s3} {d3}"
    assert d3.get("detail", {}).get("existing_id") == created_id


def test_validation_invalid_scheme(session_retry, api_base, api_headers, targets_base):
    """
    URL avec schéma non http(s) => 422. Vérifie le champ 'url' dans la validation.
    """
    unique = _unique_suffix()
    body = _payload(name=f"BadScheme {unique}", url="ftp://example.com/health")

    s, d = _post_target(session_retry, api_base, api_headers, targets_base, body)
    assert s == 422, f"expected 422, got {s} {d}"

    detail = d.get("detail", [])
    assert any(("url" in (item.get("loc") or [])) or (item.get("loc", [""])[-1] == "url") for item in detail)
    as_text = json.dumps(detail).lower()
    assert "http" in as_text and "https" in as_text


def test_validation_invalid_method(session_retry, api_base, api_headers, targets_base):
    """Méthode inconnue => 422 (Enum côté schéma)."""
    unique = _unique_suffix()
    body = _payload(name=f"BadMethod {unique}", url=f"https://httpbin.org/status/204?rnd={unique}")
    body["method"] = "FETCH"

    s, d = _post_target(session_retry, api_base, api_headers, targets_base, body)
    assert s == 422, f"expected 422, got {s} {d}"

    detail = d.get("detail", [])
    assert any((item.get("loc") or [])[-1] == "method" for item in detail)


def test_list_contains_created_item(session_retry, api_base, api_headers, targets_base):
    """Après création, l'item doit apparaître dans le listing."""
    unique = _unique_suffix()
    url = f"https://httpbin.org/status/200?rnd={unique}"
    body = _payload(name=f"ListCheck {unique}", url=url)

    s, d = _post_target(session_retry, api_base, api_headers, targets_base, body)
    assert s == 201 and "id" in d, f"create failed: {s} {d}"
    created_id = d["id"]

    r = session_retry.get(f"{api_base}{targets_base}", headers=api_headers, timeout=LIST_TIMEOUT)
    assert r.status_code == 200, f"list failed: {r.status_code} {r.text}"

    items = r.json()
    ids = {it["id"] for it in items}
    assert created_id in ids, "created id not found in listing"


@pytest.mark.parametrize("override,expected", [
    ({}, 201),
    ({"method": "post"}, 201),
    ({"expected_status_code": 404}, 201),
    ({"expected_status_code": 99}, 422),
    ({"url": "not-a-url"}, 422),
    ({"method": "INVALID"}, 422),
])
def test_create_http_target_param(session_retry, api_base, api_headers, targets_base, override, expected):
    """
    Cas paramétriques : pour les cas 201, on force une URL unique ; pour les cas 422 on garde l’override.
    """
    # URL unique par test pour éviter les 409 "already exists"
    base_url_unique = f"https://httpbin.org/status/200?rnd={_unique_suffix()}"
    body = _payload(name=f"Param { _unique_suffix() }", url=base_url_unique)
    body.update(override)

    s, d = _post_target(session_retry, api_base, api_headers, targets_base, body)
    assert s == expected, f"{s} {d}"
