# server/tests/integration/test_http_targets_integration.py
"""
Tests d’intégration « live » qui appellent l’API HTTP réelle.

Prérequis :
  - La stack d’intégration est démarrée (API, DB, Redis, worker)
  - Une clé API valide est connue

Variables d’environnement utiles :
  - INTEG_STACK_UP=1     -> exécuter (sinon skip)
  - API=http://localhost:8000
  - KEY=dev-apikey-123

Exemples :
  INTEG_STACK_UP=1 API=http://localhost:8000 KEY=dev-apikey-123 \
    pytest -q server/tests/integration/test_http_targets_integration.py
"""

from __future__ import annotations

import json
import os
import random
import string
import concurrent.futures as futures
from typing import Tuple

import pytest
import requests
from requests.adapters import HTTPAdapter, Retry

pytestmark = pytest.mark.integration

# --- Skip module si la stack n'est pas démarrée --------------------------------
if not os.getenv("INTEG_STACK_UP"):
    pytest.skip("Integration stack not running (export INTEG_STACK_UP=1)", allow_module_level=True)

# --- Config API ----------------------------------------------------------------
API_BASE = os.getenv("API", "http://localhost:8000")
API_KEY = os.getenv("KEY") or os.getenv("API_KEY") or os.getenv("DEV_API_KEY")

REQUEST_TIMEOUT = 10  # timeout par requête
LIST_TIMEOUT = 10

def api(path: str) -> str:
    """Construit une URL vers l'API v1."""
    return f"{API_BASE}/api/v1{path}"

def _headers() -> dict:
    """Entêtes standards incluant la clé API."""
    return {
        "Content-Type": "application/json",
        "X-API-Key": API_KEY,
    }

def _unique_suffix(n: int = 12) -> str:
    """Suffixe aléatoire (évite collisions entre runs)."""
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

def _make_session() -> requests.Session:
    """Session HTTP avec retries pour limiter les flakes réseau."""
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

def _post_target(body: dict) -> Tuple[int, dict]:
    """POST /http-targets et renvoie (status_code, payload_json_ou_raw)."""
    r = SESSION.post(api("/http-targets"), headers=_headers(), json=body, timeout=REQUEST_TIMEOUT)
    try:
        data = r.json()
    except Exception:
        data = {"_raw": r.text}
    return r.status_code, data

# --- Garde-fous initiaux -------------------------------------------------------
if not API_KEY:
    pytest.skip("Missing API key (set KEY env var)", allow_module_level=True)

try:
    # Vérifie rapidement que l'API répond avant de lancer les tests
    resp = SESSION.get(api("/health"), timeout=REQUEST_TIMEOUT)
    if not resp.ok:
        pytest.skip(f"API health not OK: {resp.status_code}", allow_module_level=True)
except Exception:
    pytest.skip("API server not reachable. Start it first.", allow_module_level=True)

# --- Tests ---------------------------------------------------------------------

def test_concurrent_create_then_conflict():
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
        f1 = ex.submit(_post_target, body)
        f2 = ex.submit(_post_target, body)
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
    s3, d3 = _post_target(body)
    assert s3 == 409, f"expected 409, got {s3} {d3}"
    assert d3.get("detail", {}).get("existing_id") == created_id


def test_validation_invalid_scheme():
    """
    URL avec schéma non http(s) => 422.
    On vérifie le statut et la présence d’un message parlant (champ 'url').
    """
    unique = _unique_suffix()
    body = _payload(name=f"BadScheme {unique}", url="ftp://example.com/health")

    s, d = _post_target(body)
    assert s == 422, f"expected 422, got {s} {d}"

    # L’erreur pydantic peut varier ; on vérifie que 'url' est pointé dans 'loc'
    detail = d.get("detail", [])
    assert any(
        ("url" in (item.get("loc") or [])) or (item.get("loc", [""])[-1] == "url")
        for item in detail
    )
    # Et que le message parle bien de http/https
    as_text = json.dumps(detail).lower()
    assert "http" in as_text and "https" in as_text


def test_validation_invalid_method():
    """
    Méthode inconnue ("FETCH") => 422 (Enum côté schéma).
    """
    unique = _unique_suffix()
    body = _payload(name=f"BadMethod {unique}", url=f"https://httpbin.org/status/204?rnd={unique}")
    body["method"] = "FETCH"

    s, d = _post_target(body)
    assert s == 422, f"expected 422, got {s} {d}"

    # Vérifie que la localisation de l’erreur pointe 'method'
    detail = d.get("detail", [])
    assert any((item.get("loc") or [])[-1] == "method" for item in detail)


def test_list_contains_created_item():
    """
    Création d’une cible puis GET /http-targets : l’élément doit apparaître.
    """
    unique = _unique_suffix()
    url = f"https://httpbin.org/status/200?rnd={unique}"
    body = _payload(name=f"ListCheck {unique}", url=url)

    s, d = _post_target(body)
    assert s == 201 and "id" in d, f"create failed: {s} {d}"
    created_id = d["id"]

    r = SESSION.get(api("/http-targets"), headers=_headers(), timeout=LIST_TIMEOUT)
    assert r.status_code == 200, f"list failed: {r.status_code} {r.text}"

    items = r.json()
    ids = {it["id"] for it in items}
    assert created_id in ids, "created id not found in listing"


def test_idempotence_simple_conflict_after_first_create():
    """
    POST => 201, puis POST identique => 409 avec existing_id = id créé.
    (Cas non concurrent, pour couvrir la voie la plus simple.)
    """
    unique = _unique_suffix()
    url = f"https://httpbin.org/status/500?once={unique}"
    body = _payload(name=f"Idem {unique}", url=url)

    s1, d1 = _post_target(body)
    assert s1 == 201 and "id" in d1, f"first post failed: {s1} {d1}"

    s2, d2 = _post_target(body)
    assert s2 == 409, f"expected 409, got {s2} {d2}"
    assert d2.get("detail", {}).get("existing_id") == d1["id"]
