# server/tests/unit/test_schemas_http_target.py
import pytest
from pydantic import ValidationError
from app.api.schemas.http_target import HttpTargetIn  # adapte si le nom exact diffère

pytestmark = pytest.mark.unit

@pytest.mark.parametrize("code,ok", [
    (200, True), (201, True), (204, True),
    (301, True), (404, True), (500, True),
    (99, False), (600, False), (999, False), (-1, False),
])
def test_expected_status_bounds(code, ok):
    base = {
        "name": "t1",
        "url": "https://example.com/health",
        "method": "GET",
        "expected_status_code": 200,
        "timeout_seconds": 10,
        "check_interval_seconds": 60,
        "is_active": True,
    }
    data = {**base, "expected_status_code": code}
    if ok:
        HttpTargetIn(**data)
    else:
        with pytest.raises(ValidationError):
            HttpTargetIn(**data)

@pytest.mark.parametrize("url,ok", [
    ("https://valid.tld", True),
    ("http://valid.tld", True),
    ("not-a-url", False),
    ("ftp://nope.tld", False),
    ("", False),
])
def test_url_validation(url, ok):
    data = {
        "name": "t1",
        "url": url,
        "method": "GET",
        "expected_status_code": 200,
        "timeout_seconds": 10,
        "check_interval_seconds": 60,
        "is_active": True,
    }
    if ok:
        HttpTargetIn(**data)
    else:
        with pytest.raises(ValidationError):
            HttpTargetIn(**data)

@pytest.mark.parametrize("method,ok", [
    ("GET", True), ("POST", True), ("PUT", True),
    ("PATCH", True), ("DELETE", True), ("HEAD", True), ("OPTIONS", True),
    ("get", True),  # doit être normalisé en "GET" par le schéma
    ("INVALID", False), ("", False),
])
def test_method_enum_and_normalization(method, ok):
    data = {
        "name": "t1",
        "url": "https://valid.tld",
        "method": method,
        "expected_status_code": 200,
        "timeout_seconds": 10,
        "check_interval_seconds": 60,
        "is_active": True,
    }
    if ok:
        m = HttpTargetIn(**data)
        assert m.method == method.upper()
    else:
        with pytest.raises(ValidationError):
            HttpTargetIn(**data)
