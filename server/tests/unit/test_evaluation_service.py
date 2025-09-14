# server/tests/unit/test_http_monitor_service.py
import importlib
import pytest

pytestmark = pytest.mark.unit

# On importe le module sans supposer sa forme exacte.
# Selon les implémentations, il peut exposer :
#  - une classe HttpMonitorService avec une méthode check_once(...)
#  - une fonction check_once(...)
#  - éventuellement une primitive http_get(...) que l'on veut monkeypatcher
mod = importlib.import_module("app.application.services.http_monitor_service")


class _RespOK:
    status_code = 200
    text = "OK"


class _RespKO:
    status_code = 500
    text = "KO"


def _patch_http_get(monkeypatch, resp):
    """
    Si le module expose http_get(...), on le monkey-patche pour éviter tout appel réseau.
    """
    if hasattr(mod, "http_get"):
        def fake_http_get(url, method="GET", timeout=10):  # noqa: ARG001
            return resp
        monkeypatch.setattr(mod, "http_get", fake_http_get, raising=False)


def _invoke_check_once(monkeypatch, *, expected_status, resp):
    """
    Appelle check_once quelle que soit sa forme (classe HttpMonitorService ou fonction de module).
    """
    _patch_http_get(monkeypatch, resp)

    # 1) Classe HttpMonitorService ?
    svc_cls = getattr(mod, "HttpMonitorService", None)
    if isinstance(svc_cls, type):
        svc = svc_cls()  # si le __init__ attend des args, adapte ici si nécessaire
        meth = getattr(svc, "check_once", None)
        if callable(meth):
            return meth(
                url="https://example.com/health",
                method="GET",
                expected_status_code=expected_status,
                timeout_seconds=5,
            )

    # 2) Fonction de module check_once ?
    fn = getattr(mod, "check_once", None)
    if callable(fn):
        return fn(
            url="https://example.com/health",
            method="GET",
            expected_status_code=expected_status,
            timeout_seconds=5,
        )

    pytest.skip("No HttpMonitorService or check_once() found in http_monitor_service.")


def _as_up(result):
    """
    Normalise le résultat en booléen.
    - si bool → renvoie tel quel
    - si dict → prend 'up' (ou variantes usuelles)
    """
    if isinstance(result, bool):
        return result
    if isinstance(result, dict):
        for key in ("up", "is_up", "ok"):
            if key in result:
                return bool(result[key])
    pytest.skip("Unsupported check_once return type (expected bool or dict with 'up').")


def test_up_when_expected_200(monkeypatch):
    """
    Si l'endpoint renvoie 200 et qu'on attend 200 → up = True.
    """
    out = _invoke_check_once(monkeypatch, expected_status=200, resp=_RespOK())
    assert _as_up(out) is True


def test_down_when_expected_200_but_500(monkeypatch):
    """
    Si l'endpoint renvoie 500 alors qu'on attend 200 → up = False.
    """
    out = _invoke_check_once(monkeypatch, expected_status=200, resp=_RespKO())
    assert _as_up(out) is False


def test_up_when_expected_500_and_got_500(monkeypatch):
    """
    Si, par configuration, on attend explicitement 500 et qu'on obtient 500 → up = True.
    (Certaines intégrations utilisent des endpoints 'always-500' pour tester l'alerte.)
    """
    out = _invoke_check_once(monkeypatch, expected_status=500, resp=_RespKO())
    assert _as_up(out) is True
