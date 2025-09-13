# server/tests/e2e/conftest.py
import os
import time
import pytest
import requests
from requests.adapters import HTTPAdapter, Retry

def pytest_addoption(parser):
    parser.addoption("--api", action="store", default=os.getenv("API", "http://localhost:8000"))
    parser.addoption("--api-key", action="store", default=os.getenv("KEY", "dev-apikey-123"))

@pytest.fixture(scope="session")
def api_base(pytestconfig) -> str:
    return pytestconfig.getoption("--api")

@pytest.fixture(scope="session")
def api_headers(pytestconfig) -> dict:
    return {"X-API-Key": pytestconfig.getoption("--api-key"), "Content-Type": "application/json"}

@pytest.fixture(scope="session")
def session_retry() -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset(["GET","POST","PUT","PATCH","DELETE","HEAD","OPTIONS"]),
        raise_on_status=False,
    )
    s.mount("http://", HTTPAdapter(max_retries=retries))
    s.mount("https://", HTTPAdapter(max_retries=retries))
    return s

@pytest.fixture
def wait():
    def _wait(fn, timeout=90, every=2):
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
    return _wait
