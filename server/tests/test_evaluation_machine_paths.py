import uuid
import pytest

# ⚠️ Très important : on importe le module pour pouvoir patcher le *symbole importé*
# dans ce module (evaluate_machine.py importe "get_sync_session" par nom).
import app.application.services.evaluation_service as es

from sqlalchemy import select
from app.infrastructure.persistence.database.models import client as C

pytestmark = pytest.mark.unit


def _mk(session, model, **kw):
    """Petit utilitaire pour insérer + flush en une ligne."""
    o = model(**kw)
    session.add(o)
    session.flush()
    return o


def test_evaluate_machine_breach_creates_alert_and_notifies(Session, monkeypatch):
    """
    Important : on FORCE le service à utiliser la session SQLite de test,
    pas Postgres. On monkeypatch `get_sync_session` **dans le module du service**,
    i.e. `app.application.services.evaluation_service`, car le service a importé
    le symbole par nom.

    On vérifie le chemin "breach" : création d'alerte + appel à notify_alert.delay.
    """

    # ---- Route le service vers la session de test (context manager compatible) ----
    def _override_get_sync_session():
        class _Ctx:
            def __enter__(self):
                self._s = Session()  # <-- fixture fournit une Session() liée au SQLite en mémoire
                return self._s

            def __exit__(self, exc_type, exc, tb):
                self._s.close()

        return _Ctx()

    # ✅ Patch sur la BONNE cible (le module du service), pas sur le module "db_session"
    monkeypatch.setattr(es, "get_sync_session", _override_get_sync_session, raising=True)

    from app.application.services.evaluation_service import evaluate_machine
    from app.infrastructure.persistence.database.models import (
        machine as M,
        metric as Me,
        threshold as Th,
        sample as Sa,
        alert as Al,
        incident as In,
    )

    notified = []

    class DummyTask:
        @staticmethod
        def delay(alert_id):
            notified.append(alert_id)

    # Patch la tâche notify_alert.delay pour ne PAS faire de vrai Celery
    import app.workers.tasks.notification_tasks as nt

    monkeypatch.setattr(nt, "notify_alert", DummyTask, raising=True)

    with Session() as s:
        client_id = uuid.uuid4()
        _mk(s, C.Client, id=client_id, name="Test Client")
        m = _mk(s, M.Machine, id=uuid.uuid4(), client_id=client_id, hostname="m1")
        me = _mk(
            s,
            Me.Metric,
            id=uuid.uuid4(),
            machine_id=m.id,
            name="cpu_load",
            type="numeric",
            unit="ratio",
        )
        th = _mk(
            s,
            Th.Threshold,
            id=uuid.uuid4(),
            metric_id=me.id,
            name="High CPU",
            condition="gt",
            value_num=1.0,
            severity="warning",
            is_active=True,
        )
        # dernier sample > 1.0  -> breach
        _mk(s, Sa.Sample, metric_id=me.id, value_type="numeric", num_value=3.3, seq=0)
        s.commit()

        # ⚠️ On passe un UUID (et pas str(m.id)) pour éviter l'erreur 'str'.hex côté SQLAlchemy UUID
        total = evaluate_machine(m.id)
        assert total >= 1

        a = s.scalars(select(Al.Alert).where(Al.Alert.threshold_id == th.id)).first()
        assert a and a.status == "FIRING"

        inc = s.scalars(
            select(In.Incident).where(
                In.Incident.machine_id == m.id, In.Incident.status == "OPEN"
            )
        ).first()
        assert inc is not None

    assert notified, "notify_alert.delay aurait dû être appelée"


def test_evaluate_machine_no_breach_resolves(Session, monkeypatch):
    """
    Même patch que ci-dessus : force le service à utiliser la session SQLite en mémoire.
    On vérifie le chemin "no breach" : l'alerte préexistante doit être résolue.
    """

    def _override_get_sync_session():
        class _Ctx:
            def __enter__(self):
                self._s = Session()
                return self._s

            def __exit__(self, exc_type, exc, tb):
                self._s.close()

        return _Ctx()

    # ✅ Patch sur la BONNE cible (le module du service)
    monkeypatch.setattr(es, "get_sync_session", _override_get_sync_session, raising=True)

    from app.application.services.evaluation_service import evaluate_machine
    from app.infrastructure.persistence.database.models import (
        machine as M,
        metric as Me,
        threshold as Th,
        sample as Sa,
        alert as Al,
    )

    with Session() as s:
        client_id = uuid.uuid4()
        _mk(s, C.Client, id=client_id, name="Test Client")

        m = _mk(s, M.Machine, id=uuid.uuid4(), client_id=client_id, hostname="m2")
        me = _mk(
            s,
            Me.Metric,
            id=uuid.uuid4(),
            machine_id=m.id,
            name="cpu_load",
            type="numeric",
            unit="ratio",
        )
        th = _mk(
            s,
            Th.Threshold,
            id=uuid.uuid4(),
            metric_id=me.id,
            name="High CPU",
            condition="gt",
            value_num=1.0,
            severity="warning",
            is_active=True,
        )
        # une alerte FIRING préexistante
        _mk(
            s,
            Al.Alert,
            id=uuid.uuid4(),
            threshold_id=th.id,
            machine_id=m.id,
            metric_id=me.id,
            status="FIRING",
            severity="warning",
            current_value="3.3",
        )
        # sample sous le seuil -> no breach
        _mk(s, Sa.Sample, metric_id=me.id, value_type="numeric", num_value=0.5, seq=0)
        s.commit()

        # ⚠️ Passer un UUID (pas str)
        evaluate_machine(m.id)

        a = s.scalars(select(Al.Alert).where(Al.Alert.threshold_id == th.id)).first()
        assert a.status != "FIRING"
