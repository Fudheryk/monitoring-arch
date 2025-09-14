# server/tests/unit/test_http_monitor_cooldown.py
import uuid
import datetime as dt
import pytest

pytestmark = pytest.mark.unit

def test_http_monitor_cooldown(Session, monkeypatch):
    from sqlalchemy import select
    from app.application.services.http_monitor_service import check_http_targets
    from app.infrastructure.persistence.database.models.http_target import HttpTarget
    from app.infrastructure.persistence.database.models.incident import Incident
    from app.infrastructure.persistence.database.models.notification_log import NotificationLog
    import app.workers.tasks.notification_tasks as nt
    import httpx

    # --- Fake httpx.Client : renvoie toujours 500 ---
    class _Resp:
        def __init__(self, status_code): self.status_code = status_code
    class _FakeClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, tb): return False
        def request(self, method, url): return _Resp(500)
    monkeypatch.setattr(httpx, "Client", _FakeClient, raising=True)

    # --- Capture des enqueues Celery (notify.apply_async) ---
    enqueues = []
    def _fake_apply_async(*, kwargs, queue):
        enqueues.append({"kwargs": kwargs, "queue": queue})
        class _Res: id = "fake"
        return _Res()
    monkeypatch.setattr(nt.notify, "apply_async", _fake_apply_async, raising=True)

    client_id = uuid.uuid4()

    # 1) Crée une cible active « due » au premier passage (last_check_at=None)
    with Session() as s:
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
        s.add(t); s.commit()

    # 2) Premier passage : check + ouverture incident + 1 notif
    updated = check_http_targets()
    assert updated == 1
    assert len(enqueues) == 1

    # On récupère l'incident créé et log une notif 'success' (pour armer le cooldown)
    with Session() as s:
        inc = s.scalars(select(Incident).where(
            Incident.client_id == client_id,
            Incident.title.like("HTTP check failed:%"),
            Incident.status == "OPEN",
        )).first()
        assert inc is not None

        s.add(NotificationLog(
            client_id=client_id,
            incident_id=inc.id,
            alert_id=None,
            provider="slack",
            recipient="#test",
            status="success",
            message="fake",
            error_message=None,
            sent_at=dt.datetime.now(dt.timezone.utc),
            created_at=dt.datetime.now(dt.timezone.utc),
        ))
        s.commit()

    # 3) Second passage **immédiat** : pas dû → 0 check, pas de nouvelle notif
    updated = check_http_targets()
    assert updated == 0
    assert len(enqueues) == 1

    # 4) On fait expirer le cooldown **et** on rend la cible « due »
    with Session() as s:
        # Vieillir le dernier envoi pour dépasser le cooldown (conftest: >=1 min)
        log = s.scalars(select(NotificationLog)).first()
        log.sent_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=2)

        # Rendre la cible due : reculer last_check_at au-delà de l'intervalle
        t = s.scalars(select(HttpTarget).where(HttpTarget.client_id == client_id)).first()
        assert t is not None
        interval = dt.timedelta(seconds=t.check_interval_seconds or 60)
        t.last_check_at = dt.datetime.now(dt.timezone.utc) - interval - dt.timedelta(seconds=1)
        s.commit()

    # 5) Troisième passage : dû + cooldown expiré → 1 check + 1 notif en plus
    updated = check_http_targets()
    assert updated == 1
    assert len(enqueues) == 2
