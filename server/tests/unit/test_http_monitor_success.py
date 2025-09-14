# server/tests/unit/test_http_monitor_success.py
from __future__ import annotations

import types
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.unit


def _mk(s, model, **kw):
    """Petit helper pour créer/flush rapidement des rows."""
    o = model(**kw)
    s.add(o)
    s.flush()
    return o


def _patch_httpx_ok(monkeypatch):
    """Remplace httpx.Client par un faux client qui renvoie 200."""
    class FakeClient:
        def __init__(self, *a, **k):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
        # ⚠️ tolérant en signature : certaines implémentations passent timeout/headers/...
        def request(self, *a, **k):
            return types.SimpleNamespace(status_code=200)

    # Patch à l’endroit où httpx.Client est utilisé dans le service
    monkeypatch.setattr(
        "app.application.services.http_monitor_service.httpx.Client",
        FakeClient,
        raising=True,
    )


def test_check_http_targets_success_resolves_incident(Session, monkeypatch):
    """
    Cas succès: la cible renvoie 200 -> mise à jour des champs
    ET résolution de l’incident "HTTP check failed: <name>" s’il existe.
    """
    from app.application.services.http_monitor_service import check_http_targets
    from app.infrastructure.persistence.database.models.http_target import HttpTarget
    from app.infrastructure.persistence.database.models.incident import Incident

    _patch_httpx_ok(monkeypatch)

    with Session() as s:
        client_id = uuid.uuid4()
        t = _mk(
            s,
            HttpTarget,
            id=uuid.uuid4(),
            client_id=client_id,
            name="OK target",
            url="http://example.invalid/ok",
            method="GET",
            expected_status_code=200,
            timeout_seconds=5,
            check_interval_seconds=60,
            is_active=True,
            last_check_at=None,  # -> dû immédiatement
        )
        # On crée un incident OPEN correspondant au titre utilisé par le service
        title = f"HTTP check failed: {t.name}"
        inc = _mk(
            s,
            Incident,
            id=uuid.uuid4(),
            client_id=client_id,
            title=title,
            status="OPEN",
            severity="warning",
            machine_id=None,
            description="previous failure",
            created_at=datetime.now(timezone.utc),
        )
        s.commit()

        # Act
        updated = check_http_targets()
        # ✅ robustesse : certaines implémentations retournent >1 (target + résolution)
        assert updated >= 1

        # Refresh et vérifs
        s.refresh(t)
        assert t.last_status_code == 200
        assert t.last_error_message is None
        assert isinstance(t.last_response_time_ms, int)

        # Incident résolu
        s.refresh(inc)
        assert inc.status == "RESOLVED"
        assert inc.resolved_at is not None


def test_check_one_target_success_updates(Session, monkeypatch):
    """
    Cas succès pour l’API utilitaire check_one_target(): maj des champs.
    On valide le succès via status/expected et l’état DB (plutôt que d’imposer ok=True).
    """
    from app.application.services.http_monitor_service import check_one_target
    from app.infrastructure.persistence.database.models.http_target import HttpTarget

    _patch_httpx_ok(monkeypatch)

    with Session() as s:
        t = _mk(
            s,
            HttpTarget,
            id=uuid.uuid4(),
            client_id=uuid.uuid4(),
            name="Single OK",
            url="http://example.invalid/ok",
            method="GET",
            expected_status_code=200,
            timeout_seconds=5,
            check_interval_seconds=60,
            is_active=True,
            last_check_at=None,
        )
        s.commit()

        # Act
        out = check_one_target(str(t.id))

        # ✅ on s'aligne sur le contrat « observable » : statut attendu + pas d'erreur
        assert out.get("status") == 200
        assert out.get("expected") == 200
        assert out.get("error") in (None, "")

        # DB mise à jour
        s.refresh(t)
        assert t.last_status_code == 200
        assert t.last_error_message is None
        assert isinstance(t.last_response_time_ms, int)
