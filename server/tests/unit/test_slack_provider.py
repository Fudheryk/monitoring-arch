import pytest
from types import SimpleNamespace

pytestmark = pytest.mark.unit

def test_slack_provider_success(monkeypatch):
    from app.infrastructure.notifications.providers.slack_provider import SlackProvider

    calls = {}
    def fake_post(url, json, headers, timeout):
        calls["url"] = url; calls["json"] = json
        return SimpleNamespace(status_code=200)

    monkeypatch.setenv("SLACK_WEBHOOK", "http://example.invalid/hook")
    monkeypatch.setattr("requests.post", fake_post, raising=True)

    ok = SlackProvider().send(title="T", text="X", severity="warning", context={"a":1}, channel="#c", username="u", icon_emoji=":bell:")
    assert ok is True
    assert calls["json"]["text"].startswith("[WARNING]")

def test_slack_provider_failure(monkeypatch):
    from app.infrastructure.notifications.providers.slack_provider import SlackProvider
    class Boom(Exception): pass
    def fake_post(*a, **k): raise Boom("net down")

    monkeypatch.setenv("SLACK_WEBHOOK", "http://example.invalid/hook")
    monkeypatch.setattr("requests.post", fake_post, raising=True)

    assert SlackProvider().send(title="T", text="X") is False
