# server/tests/unit/test_endpoints_full.py
from __future__ import annotations
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.infrastructure.persistence.database.session import get_db
from app.core.security import api_key_auth

# Modèles utilisés pour semer des données
from app.infrastructure.persistence.database.models.client import Client
from app.infrastructure.persistence.database.models.machine import Machine
from app.infrastructure.persistence.database.models.metric import Metric
from app.infrastructure.persistence.database.models.client_settings import ClientSettings
from app.infrastructure.persistence.database.models.alert import Alert
from app.infrastructure.persistence.database.models.threshold import Threshold
from app.infrastructure.persistence.database.models.incident import Incident

pytestmark = pytest.mark.unit


# ---------- helpers ----------
class _FakeAPIKey:
    def __init__(self, client_id: uuid.UUID):
        self.client_id = client_id


def _override_deps(Session, client_id: uuid.UUID):
    """Retourne deux callables pour FastAPI dependency_overrides."""
    def _db():
        # yield un Session() SQLite mémoire (fixture fournie par les tests)
        with Session() as s:
            yield s

    def _auth():
        # bypass de api_key_auth: renvoie un objet minimal avec client_id
        return _FakeAPIKey(client_id)

    return _db, _auth


def _mk(session, model, **kw):
    obj = model(**kw)
    session.add(obj)
    session.flush()
    return obj


# ---------- tests ----------

def test_machines_list_ok(Session):
    """GET /api/v1/machines — renvoie 200 et un tableau (peut être vide)."""
    client_id = uuid.uuid4()
    db_dep, auth_dep = _override_deps(Session, client_id)
    app.dependency_overrides[get_db] = db_dep
    app.dependency_overrides[api_key_auth] = auth_dep
    try:
        with Session() as s:
            _mk(s, Client, id=client_id, name="C1")
            # 1 machine -> la réponse ne doit pas 404/500
            _mk(s, Machine, id=uuid.uuid4(), client_id=client_id, hostname="m1")
            s.commit()

        with TestClient(app) as c:
            r = c.get("/api/v1/machines")
            assert r.status_code == 200
            data = r.json()
            assert isinstance(data, list)
            assert any(it.get("hostname") == "m1" for it in data)
    finally:
        app.dependency_overrides.clear()


def test_incidents_list_ok(Session):
    """GET /api/v1/incidents — 200 + liste (même vide)."""
    client_id = uuid.uuid4()
    db_dep, auth_dep = _override_deps(Session, client_id)
    app.dependency_overrides[get_db] = db_dep
    app.dependency_overrides[api_key_auth] = auth_dep
    try:
        with Session() as s:
            _mk(s, Client, id=client_id, name="C1")
            _mk(
                s, Incident,
                id=uuid.uuid4(), client_id=client_id,
                title="demo", status="OPEN", severity="warning",
                machine_id=None, description="x",
            )
            s.commit()

        with TestClient(app) as c:
            r = c.get("/api/v1/incidents")
            assert r.status_code == 200
            data = r.json()
            assert isinstance(data, list)
            assert any(i.get("title") == "demo" for i in data)
    finally:
        app.dependency_overrides.clear()


def test_alerts_list_ok(Session):
    """GET /api/v1/alerts — 200 + liste (même vide)."""
    client_id = uuid.uuid4()
    db_dep, auth_dep = _override_deps(Session, client_id)
    app.dependency_overrides[get_db] = db_dep
    app.dependency_overrides[api_key_auth] = auth_dep
    try:
        with Session() as s:
            _mk(s, Client, id=client_id, name="C1")
            m = _mk(s, Machine, id=uuid.uuid4(), client_id=client_id, hostname="m1")
            me = _mk(s, Metric, id=uuid.uuid4(), machine_id=m.id, name="cpu", type="numeric", unit="ratio")
            th = _mk(s, Threshold, id=uuid.uuid4(), metric_id=me.id, name="t", condition="gt", value_num=1.0, severity="warning", is_active=True)
            _mk(
                s, Alert,
                id=uuid.uuid4(), threshold_id=th.id, machine_id=m.id, metric_id=me.id,
                status="FIRING", severity="warning", current_value="2.0", message="over",
            )
            s.commit()

        with TestClient(app) as c:
            r = c.get("/api/v1/alerts")
            assert r.status_code == 200
            js = r.json()
            assert isinstance(js, list)
            # on ne fige pas la forme exacte, on vérifie qu’une alerte est là
            assert any((a.get("status") or "").upper() == "FIRING" for a in js)
    finally:
        app.dependency_overrides.clear()


def test_http_targets_list_ok(Session):
    """GET /api/v1/http-targets — 200 + liste (même vide)."""
    client_id = uuid.uuid4()
    db_dep, auth_dep = _override_deps(Session, client_id)
    app.dependency_overrides[get_db] = db_dep
    app.dependency_overrides[api_key_auth] = auth_dep
    try:
        # pas besoin d'insérer : la liste vide suffit à exécuter l'endpoint
        with TestClient(app) as c:
            r = c.get("/api/v1/http-targets")
            assert r.status_code == 200
            assert isinstance(r.json(), list)
    finally:
        app.dependency_overrides.clear()


def test_metrics_by_machine_happy_path_and_404(Session):
    """
    /api/v1/metrics/{machine_id}
    - happy path: machine du bon client -> 200 + items
    - 404: machine d’un autre client
    """
    client_id = uuid.uuid4()
    other_client = uuid.uuid4()
    db_dep, auth_dep = _override_deps(Session, client_id)
    app.dependency_overrides[get_db] = db_dep
    app.dependency_overrides[api_key_auth] = auth_dep
    try:
        with Session() as s:
            _mk(s, Client, id=client_id, name="C1")
            _mk(s, Client, id=other_client, name="C2")
            m_ok = _mk(s, Machine, id=uuid.uuid4(), client_id=client_id, hostname="m-ok")
            m_ko = _mk(s, Machine, id=uuid.uuid4(), client_id=other_client, hostname="m-ko")

            # 2 métriques sur m_ok pour couvrir la boucle de mapping
            _mk(s, Metric, id=uuid.uuid4(), machine_id=m_ok.id, name="cpu", type="numeric", unit="ratio")
            _mk(s, Metric, id=uuid.uuid4(), machine_id=m_ok.id, name="disk", type="numeric", unit="%")
            s.commit()

        with TestClient(app) as c:
            r1 = c.get(f"/api/v1/metrics/{m_ok.id}")
            assert r1.status_code == 200
            items = r1.json()
            assert isinstance(items, list) and len(items) >= 2
            assert {it["name"] for it in items} >= {"cpu", "disk"}

            r2 = c.get(f"/api/v1/metrics/{m_ko.id}")
            assert r2.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_metrics_root_empty(Session):
    """
    GET /api/v1/metrics (route racine que tu as ajoutée)
    -> {"items": [], "total": 0}
    """
    client_id = uuid.uuid4()
    db_dep, auth_dep = _override_deps(Session, client_id)
    app.dependency_overrides[get_db] = db_dep
    app.dependency_overrides[api_key_auth] = auth_dep
    try:
        with TestClient(app) as c:
            r = c.get("/api/v1/metrics")
            assert r.status_code == 200
            js = r.json()
            assert js == {"items": [], "total": 0}
    finally:
        app.dependency_overrides.clear()


def test_settings_empty_then_present(Session):
    """
    GET /api/v1/settings :
    - vide -> {}
    - avec enregistrement -> dict avec les 6 clés attendues
    """
    client_id = uuid.uuid4()
    db_dep, auth_dep = _override_deps(Session, client_id)
    app.dependency_overrides[get_db] = db_dep
    app.dependency_overrides[api_key_auth] = auth_dep
    try:
        with Session() as s:
            _mk(s, Client, id=client_id, name="C1")
            s.commit()

        with TestClient(app) as c:
            r0 = c.get("/api/v1/settings")
            assert r0.status_code == 200
            assert r0.json() == {}

        with Session() as s:
            _mk(
                s, ClientSettings,
                client_id=client_id,
                notification_email="ops@example.test",
                slack_webhook_url="https://hooks.slack.example/123",
                heartbeat_threshold_minutes=10,
                consecutive_failures_threshold=3,
                alert_grouping_enabled=True,
                alert_grouping_window_seconds=120,
            )
            s.commit()

        with TestClient(app) as c:
            r1 = c.get("/api/v1/settings")
            assert r1.status_code == 200
            js = r1.json()
            # 6 clés, valeurs reflétées
            assert set(js.keys()) == {
                "notification_email",
                "slack_webhook_url",
                "heartbeat_threshold_minutes",
                "consecutive_failures_threshold",
                "alert_grouping_enabled",
                "alert_grouping_window_seconds",
            }
            assert js["notification_email"] == "ops@example.test"
            assert js["heartbeat_threshold_minutes"] == 10
    finally:
        app.dependency_overrides.clear()
