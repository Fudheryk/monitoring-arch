# server/tests/migrations/test_0001_initial.py
import pytest
import sqlite3
from alembic.config import Config
from alembic import command

def test_sqlite_version_requirement():
    """Vérifie que SQLite supporte les index partiels"""
    version = tuple(map(int, sqlite3.sqlite_version.split('.')))
    assert version >= (3, 8, 0), \
        f"SQLite {sqlite3.sqlite_version} < 3.8.0 requis pour index partiels"

def test_migration_0001_upgrade(alembic_config):
    """Test que la migration monte sans erreur"""
    command.upgrade(alembic_config, "0001_initial_full")
    
def test_incident_number_auto_increment(db_session, client_factory):
    """Vérifie que incident_number s'incrémente bien"""
    client = client_factory()
    
    inc1 = Incident(client_id=client.id, title="Test 1", incident_type="BREACH")
    inc2 = Incident(client_id=client.id, title="Test 2", incident_type="BREACH")
    
    db_session.add_all([inc1, inc2])
    db_session.commit()
    
    assert inc1.incident_number == 1
    assert inc2.incident_number == 2