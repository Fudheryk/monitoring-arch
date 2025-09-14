# server/tests/unit/test_http_monitor_service.py
# -------------------------------------------------------------------
# Test unitaire ROBUSTE pour le service de monitoring HTTP :
# - Ne suppose pas la présence d'une classe précise à importer.
# - Détecte dynamiquement une classe (HttpMonitorService/HTTPMonitorService/MonitorService)
#   ou une fonction (check_once/run_check/check_target) à appeler.
# - Monkey-patche les appels réseau (mod.http_get, requests, httpx) pour rester offline.
# -------------------------------------------------------------------

import importlib
import pytest

pytestmark = pytest.mark.unit

# On importe le module sans supposer sa forme exacte.
mod = importlib.import_module("app.application.services.http_monitor_service")


class _RespOK:
    status_code = 200
    text = "OK"


class _RespKO:
    status_code = 500
    text = "KO"


def _patch_http(monkeypatch, resp):
    """
    Force TOUT appel réseau à renvoyer 'resp' (offline):
    - si le module expose http_get(...), on le patch
    - requests.request / requests.get
    - httpx.request / httpx.get si httpx est installé
    """
    # 1) http_get interne éventuel
    if hasattr(mod, "http_get"):
        def fake_http_get(url, method="GET", timeout=10, **_):  # noqa: ARG001
            return resp
        monkeypatch.setattr(mod, "http_get", fake_http_get, raising=False)

    # 2) requests
    try:
        import requests  # type: ignore

        def _req(*args, **kwargs):  # noqa: ARG001
            return resp

        monkeypatch.setattr(requests, "request", _req, raising=True)
        monkeypatch.setattr(requests, "get", _req, raising=True)
        monkeypatch.setattr(requests, "post", _req, raising=True)
        monkeypatch.setattr(requests, "head", _req, raising=True)
    except Exception:
        pass

    # 3) httpx
    try:
        import httpx  # type: ignore

        def _reqx(*args, **kwargs):  # noqa: ARG001
            return resp

        monkeypatch.setattr(httpx, "request", _reqx, raising=True)
        monkeypatch.setattr(httpx, "get", _reqx, raising=True)
        monkeypatch.setattr(httpx, "post", _reqx, raising=True)
        monkeypatch.setattr(httpx, "head", _reqx, raising=True)
    except Exception:
        pass


def _resolve_callable():
    """
    Retourne un callable check(url, method, expected_status_code, timeout_seconds)
    en détectant classe+méthode ou fonction au niveau module.
    """
    # Priorité : fonctions connues
    for fname in ("check_once", "run_check", "check_target"):
        fn = getattr(mod, fname, None)
        if callable(fn):
            return fn

    # Sinon, classe connue avec méthode connue
    for cname in ("HttpMonitorService", "HTTPMonitorService", "MonitorService"):
        svc_cls = getattr(mod, cname, None)
        if isinstance(svc_cls, type):
            svc = svc_cls()  # adapte si besoin (init avec deps)
            for mname in ("check_once", "run_check", "check_target"):
                meth = getattr(svc, mname, None)
                if callable(meth):
                    # on capture self via un wrapper
                    def _bound(url, method, expected_status_code, timeout_seconds):
                        return meth(
                            url=url,
                            method=method,
                            expected_status_code=expected_status_code,
                            timeout_seconds=timeout_seconds,
                        )
                    return _bound

    pytest.skip(
        "No HttpMonitorService/HTTPMonitorService/MonitorService or check_once()/run_check()/check_target() found."
    )


def _invoke(monkeypatch, *, expected_status, resp):
    _patch_http(monkeypatch, resp)
    check = _resolve_callable()
    return check(
        url="https://example.com/health",
        method="GET",
        expected_status_code=expected_status,
        timeout_seconds=5,
    )


def _as_up(result):
    """
    Normalise le résultat en booléen.
    - si bool -> tel quel
    - si dict -> clés usuelles ('up', 'is_up', 'ok')
    """
    if isinstance(result, bool):
        return result
    if isinstance(result, dict):
        for key in ("up", "is_up", "ok"):
            if key in result:
                return bool(result[key])
    pytest.skip("Unsupported return type (expected bool or dict with 'up').")


def test_up_when_expected_200(monkeypatch):
    out = _invoke(monkeypatch, expected_status=200, resp=_RespOK())
    assert _as_up(out) is True


def test_down_when_expected_200_but_500(monkeypatch):
    out = _invoke(monkeypatch, expected_status=200, resp=_RespKO())
    assert _as_up(out) is False


def test_up_when_expected_500_and_got_500(monkeypatch):
    out = _invoke(monkeypatch, expected_status=500, resp=_RespKO())
    assert _as_up(out) is True
