import os
import pytest
import requests

API = os.getenv("API", "http://localhost:8000")
KEY = os.getenv("KEY", "dev-apikey-123")
H = {"X-API-Key": KEY}

pytestmark = pytest.mark.integration

# Endpoints "légers" qui devraient répondre même si la base est vide
# (liste vide en 200, voire 204 selon implémentation).
ENDPOINTS = [
    ("GET", "/api/v1/alerts"),
    ("GET", "/api/v1/dashboard/summary"),
    ("GET", "/api/v1/incidents"),
    ("GET", "/api/v1/machines"),
    ("GET", "/api/v1/metrics"),
    ("GET", "/api/v1/settings"),
    # Si tu exposes un ping/health protégé par clé, ajoute-le ici.
    # ("GET", "/api/v1/health"),
]

@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_endpoints_smoke(method, path):
    r = requests.request(method, f"{API}{path}", headers=H, timeout=5)
    # On évite 404 (qui ne passerait pas par le code endpoint).
    assert r.status_code in (200, 204), (path, r.status_code, r.text[:200])
