# server/tests/integration/test_http_monitor_cooldown.py
# -----------------------------------------------------------------------------
# Test d'intégration : vérifie le cooldown des notifications sur un check HTTP.
#
# Changements majeurs :
# - On filtre désormais les enqueues Celery par *client_id* pour enlever le bruit
#   des autres fixtures/tests (la base peut contenir d'autres cibles/incidents).
# - Le reste du scénario est identique (premier envoi, puis silence, puis
#   renvoi après expiration du cooldown + cible due).
# -----------------------------------------------------------------------------

from __future__ import annotations

import uuid
import datetime as dt
import os
import pytest
from sqlalchemy import select, delete

pytestmark = pytest.mark.integration

from ._dbutils import require_db_or_skip
require_db_or_skip()  # skip doux si la DB d'intégration n'est pas accessible

# Skip si la stack d'intégration n'est pas up
if os.getenv("INTEG_STACK_UP", "") != "1":
    pytest.skip("Integration stack not running (export INTEG_STACK_UP=1)", allow_module_level=True)


def test_http_monitor_cooldown(monkeypatch):
    # Imports *dans* le test pour prendre les modules "réels" d'intégration
    from app.application.services.http_monitor_service import check_http_targets
    from app.infrastructure.persistence.database.session import get_sync_session
    from app.infrastructure.persistence.database.models.client import Client
    from app.infrastructure.persistence.database.models.http_target import HttpTarget
    from app.infrastructure.persistence.database.models.incident import Incident
    from app.infrastructure.persistence.database.models.notification_log import NotificationLog
    import app.workers.tasks.notification_tasks as nt
    import httpx

    # --- Fake httpx.Client : renvoie toujours 500 (pas d'appel réseau) ---
    class _Resp:
        def __init__(self, status_code):
            self.status_code = status_code

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, *a, **kw):  # method, url, timeout, headers, ...
            return _Resp(500)

    monkeypatch.setattr(httpx, "Client", _FakeClient, raising=True)

    # --- Données de test ---
    client_id = uuid.uuid4()

    # --- Capture des enqueues Celery (notify.apply_async) *pour CE client uniquement* ---
    enqueues: list[dict] = []

    def _fake_apply_async(*args, **kw):
        """
        Celery peut appeler en positionnel/nommé ; on capture seulement si le
        payload contient le client_id de ce test.
        """
        payload = None
        if "kwargs" in kw and isinstance(kw["kwargs"], dict):
            payload = kw["kwargs"].get("payload")
        if isinstance(payload, dict):
            # payload['client_id'] peut être un UUID ou une str → on casse en str
            pid = str(payload.get("client_id", ""))
            if pid == str(client_id):
                enqueues.append({
                    "args": args,
                    "kwargs": kw.get("kwargs"),
                    "queue": kw.get("queue"),
                })

        class _Res:
            id = "fake-task-id"
        return _Res()

    monkeypatch.setattr(nt.notify, "apply_async", _fake_apply_async, raising=True)

    # Nettoyage défensif : supprime les éventuels états précédents pour *ce* client
    with get_sync_session() as s:
        s.execute(delete(NotificationLog).where(NotificationLog.client_id == client_id))
        s.execute(delete(Incident).where(Incident.client_id == client_id))
        s.execute(delete(HttpTarget).where(HttpTarget.client_id == client_id))
        s.commit()

    # 1) Crée un client (si FK requise) et une cible due (last_check_at=None)
    with get_sync_session() as s:
        if s.scalar(select(Client).where(Client.id == client_id)) is None:
            s.add(Client(id=client_id, name="itest-client"))
            s.commit()

        t = HttpTarget(
            client_id=client_id,
            name="probe-api",
            url="http://example.invalid/health",
            method="GET",
            expected_status_code=200,
            timeout_seconds=5,
            check_interval_seconds=60,  # 60s entre checks
            is_active=True,
            last_check_at=None,
        )
        s.add(t)
        s.commit()

    # Helper delta : compte uniquement nos enqueues filtrés ci-dessus
    def assert_delta_enqueues(min_delta: int, msg: str = ""):
        """
        Exécute la boucle et vérifie que le nombre d'enqueues a augmenté
        d'au moins min_delta (pour NOTRE client).
        """
        before = len(enqueues)
        _ = check_http_targets()
        after = len(enqueues)
        assert (after - before) >= min_delta, f"{msg} (delta={after - before}, expected>={min_delta})"
        return before, after

    # 2) Premier passage : check + ouverture incident → >=1 notif (delta >= 1)
    assert_delta_enqueues(1, "first pass should enqueue at least one notification")

    # Armer le cooldown : on log une notif 'success' pour l'incident créé
    with get_sync_session() as s:
        inc = s.scalars(
            select(Incident).where(
                Incident.client_id == client_id,
                Incident.title.ilike("HTTP check failed:%"),
            )
        ).first()
        assert inc is not None, "Incident not created on first failing check"

        now = dt.datetime.now(dt.timezone.utc)
        s.add(
            NotificationLog(
                client_id=client_id,
                incident_id=inc.id,
                alert_id=None,
                provider="slack",
                recipient="#test",
                status="success",
                message="fake",
                error_message=None,
                sent_at=now,
                created_at=now,
            )
        )
        s.commit()

    # 3) Second passage immédiat : cible pas due → *pas* de nouvelle notif (delta == 0)
    before = len(enqueues)
    _ = check_http_targets()
    after = len(enqueues)
    assert (after - before) == 0, f"no new notification expected without cooldown expiry (delta={after - before})"

    # 4) Expire le cooldown ET rend la cible due
    with get_sync_session() as s:
        log = s.scalars(select(NotificationLog).where(NotificationLog.client_id == client_id)).first()
        assert log is not None
        # Vieillir l'envoi pour dépasser le cooldown (implémentation/ENV >= 1 minute)
        log.sent_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=2)

        t = s.scalars(select(HttpTarget).where(HttpTarget.client_id == client_id)).first()
        assert t is not None
        interval = dt.timedelta(seconds=t.check_interval_seconds or 60)
        t.last_check_at = dt.datetime.now(dt.timezone.utc) - interval - dt.timedelta(seconds=1)
        s.commit()

    # 5) Troisième passage : due + cooldown expiré → >=1 notif en plus (delta >= 1)
    assert_delta_enqueues(1, "third pass (cooldown expired) should enqueue at least one notification")
