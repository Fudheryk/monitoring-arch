# server/tests/unit/test_incident_repository_open.py
import uuid
import pytest

pytestmark = pytest.mark.unit

def test_open_idempotent_and_created_flag(Session):
    from app.infrastructure.persistence.repositories.incident_repository import IncidentRepository
    from app.infrastructure.persistence.database.models.incident import Incident

    client_id = uuid.uuid4()
    title = "HTTP check failed: api"
    severity = "warning"

    with Session() as s:
        repo = IncidentRepository(s)

        # 1) Première ouverture -> created=True
        inc1, created1 = repo.open(client_id=client_id, title=title, severity=severity)
        assert created1 is True
        assert isinstance(inc1, Incident)
        first_id = inc1.id

        # 2) Deuxième ouverture identique -> même incident, created=False
        inc2, created2 = repo.open(client_id=client_id, title=title, severity=severity)
        assert created2 is False
        assert inc2.id == first_id

        # 3) Il n'y en a qu'un en base
        rows = s.query(Incident).filter_by(client_id=client_id, title=title, status="OPEN").all()
        assert len(rows) == 1
