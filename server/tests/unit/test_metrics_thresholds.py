from __future__ import annotations
"""
Tests ciblés sur les mutations Metrics :
- POST   /api/v1/metrics/{metric_id}/thresholds/default
- PATCH  /api/v1/metrics/{metric_id}/alerting

Objectifs :
- créer/mettre à jour un seuil "par défaut" pour une métrique (number / bool / string)
- basculer le flag d’alerte de la métrique (on/off)
- respect du scoping par client (404 si metric d’un autre client)
- validation de quelques combinaisons invalides -> 4xx

Ces tests réutilisent la fixture `Session` (sessionmaker SQLite in-memory) et overrident :
- get_db        -> pour injecter la session de test
- api_key_auth  -> pour by-passer l’auth et fournir un client_id connu
"""
import uuid
import pytest
from types import SimpleNamespace
from fastapi.testclient import TestClient

from app.main import app
from app.infrastructure.persistence.database.session import get_db as real_get_db
from app.core.security import api_key_auth

# Modèles pour le seed
from app.infrastructure.persistence.database.models.client import Client
from app.infrastructure.persistence.database.models.machine import Machine
from app.infrastructure.persistence.database.models.metric import Metric
from app.infrastructure.persistence.database.models.threshold import Threshold

# IMPORTANT : ce fichier est pensé comme *tests unitaires* (utilise la fixture Session).
pytestmark = pytest.mark.unit


# ---------- helpers communs ----------

def _mk(session, model, **kw):
    """Insère et flush un objet SQLAlchemy simple (retourne l'instance)."""
    obj = model(**kw)
    session.add(obj)
    session.flush()
    return obj


def _override_deps(Session, client_id: uuid.UUID):
    """
    Deux overrides FastAPI :
      - get_db -> yield une session SQLite mémoire de test
      - api_key_auth -> retourne un simple objet avec client_id
    """
    def _get_db_for_tests():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    async def _fake_api_key():
        return SimpleNamespace(client_id=client_id)

    app.dependency_overrides[real_get_db] = _get_db_for_tests
    app.dependency_overrides[api_key_auth] = _fake_api_key


@pytest.fixture(autouse=True)
def _clean_overrides():
    """Nettoie les overrides après chaque test."""
    old = dict(app.dependency_overrides)
    try:
        yield
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old)


# ---------- tests ----------

def test_create_default_threshold_number_then_update(Session):
    """
    Crée un seuil "par défaut" pour une métrique de type numérique, puis le met à jour.
    - Vérifie qu’il n’y a qu’UN seul enregistrement (upsert-like)
    - Vérifie la mise à jour des champs (condition / value_num)
    """
    client_id = uuid.uuid4()
    _override_deps(Session, client_id)

    with Session() as s:
        c = _mk(s, Client, id=client_id, name="C1")
        m = _mk(s, Machine, id=uuid.uuid4(), client_id=c.id, hostname="host-1")
        metric_id = uuid.uuid4()
        # "numeric" cohérent avec la sémantique interne
        _mk(s, Metric, id=metric_id, machine_id=m.id, name="cpu", type="numeric", unit="%")
        s.commit()

    client = TestClient(app)

    # 1) création
    body_create = {"kind": "number", "condition": "gt", "value": 80.0, "severity": "warning"}
    r1 = client.post(f"/api/v1/metrics/{metric_id}/thresholds/default", json=body_create)
    assert r1.status_code in (200, 201), r1.text

    # contrôle DB
    with Session() as s:
        rows = s.query(Threshold).filter(Threshold.metric_id == metric_id).all()
        assert len(rows) == 1
        th = rows[0]
        assert (th.condition or "").lower() == "gt"
        assert th.value_num == pytest.approx(80.0, abs=1e-6)

    # 2) mise à jour du même seuil
    body_update = {"kind": "number", "condition": "gt", "value": 90.0, "severity": "warning"}
    r2 = client.post(f"/api/v1/metrics/{metric_id}/thresholds/default", json=body_update)
    assert r2.status_code in (200, 201), r2.text

    with Session() as s:
        rows = s.query(Threshold).filter(Threshold.metric_id == metric_id).all()
        assert len(rows) == 1, "La route doit mettre à jour le seuil existant (pas en créer un second)."
        th = rows[0]
        assert (th.condition or "").lower() == "gt"
        assert th.value_num == pytest.approx(90.0, abs=1e-6)


def test_toggle_alerting_flag(Session):
    """
    PATCH /metrics/{metric_id}/alerting : désactive puis réactive l'alerte pour la métrique.
    """
    client_id = uuid.uuid4()
    _override_deps(Session, client_id)

    with Session() as s:
        c = _mk(s, Client, id=client_id, name="C1")
        m = _mk(s, Machine, id=uuid.uuid4(), client_id=c.id, hostname="host-1")
        metric_id = uuid.uuid4()
        _mk(s, Metric, id=metric_id, machine_id=m.id, name="disk", type="numeric", unit="%")
        s.commit()

    client = TestClient(app)

    # disable
    r1 = client.patch(f"/api/v1/metrics/{metric_id}/alerting", json={"enabled": False})
    assert r1.status_code in (200, 204), r1.text

    with Session() as s:
        mt = s.get(Metric, metric_id)
        assert mt.is_alerting_enabled is False

    # enable
    r2 = client.patch(f"/api/v1/metrics/{metric_id}/alerting", json={"enabled": True})
    assert r2.status_code in (200, 204), r2.text

    with Session() as s:
        mt = s.get(Metric, metric_id)
        assert mt.is_alerting_enabled is True


def test_default_threshold_bool_validation_and_apply(Session):
    """
    Crée un seuil par défaut pour une métrique booléenne.
    - condition 'eq' / 'ne' autorisées ; les comparaisons numériques doivent être rejetées.
    """
    client_id = uuid.uuid4()
    _override_deps(Session, client_id)

    with Session() as s:
        c = _mk(s, Client, id=client_id, name="C1")
        m = _mk(s, Machine, id=uuid.uuid4(), client_id=c.id, hostname="host-2")
        metric_id = uuid.uuid4()
        _mk(s, Metric, id=metric_id, machine_id=m.id, name="feature_flag", type="bool", unit=None)
        s.commit()

    client = TestClient(app)

    # cas valide : eq true
    r_ok = client.post(
        f"/api/v1/metrics/{metric_id}/thresholds/default",
        json={"kind": "bool", "condition": "eq", "value_bool": True, "severity": "warning"},
    )
    assert r_ok.status_code in (200, 201), r_ok.text

    with Session() as s:
        th = s.query(Threshold).filter(Threshold.metric_id == metric_id).one()
        assert (th.condition or "").lower() == "eq"
        assert th.value_bool is True

    # cas invalide : 'gt' sur un bool -> 4xx
    r_bad = client.post(
        f"/api/v1/metrics/{metric_id}/thresholds/default",
        json={"kind": "bool", "condition": "gt", "value_bool": True},
    )
    assert r_bad.status_code in (400, 422), r_bad.text


def test_404_when_metric_belongs_to_another_client(Session):
    """
    Toute mutation sur une métrique d’un autre client doit renvoyer 404 (scoping).
    """
    client_ok = uuid.uuid4()
    client_other = uuid.uuid4()
    _override_deps(Session, client_ok)

    with Session() as s:
        c1 = _mk(s, Client, id=client_ok, name="C1")
        c2 = _mk(s, Client, id=client_other, name="C2")
        m2 = _mk(s, Machine, id=uuid.uuid4(), client_id=c2.id, hostname="host-other")
        metric_id = uuid.uuid4()
        _mk(s, Metric, id=metric_id, machine_id=m2.id, name="cpu", type="numeric", unit="%")
        s.commit()

    client = TestClient(app)

    r1 = client.post(
        f"/api/v1/metrics/{metric_id}/thresholds/default",
        json={"kind": "number", "condition": "gt", "value": 50.0},
    )
    assert r1.status_code == 404, r1.text

    r2 = client.patch(f"/api/v1/metrics/{metric_id}/alerting", json={"enabled": False})
    assert r2.status_code == 404, r2.text
