# server/tests/contract/machine_ingest_api/test_time_window.py
"""
Contrat /ingest/metrics : fenêtre temporelle
- 422 si sent_at est dans le futur (> INGEST_FUTURE_MAX_SECONDS)
- 202 {"reason": "archived"} si trop en retard (> INGEST_LATE_MAX_SECONDS)
- 202 {"status": "accepted"} si OK

⚠️ Important :
- NE PAS importer app/main ici. On utilise la fixture `http` fournie par
  server/tests/contract/conftest.py (qui configure SQLite + crée le schéma).
"""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
import uuid

import pytest

from app.infrastructure.persistence.database.session import open_session
from app.infrastructure.persistence.database.models.client import Client
from app.infrastructure.persistence.database.models.client_settings import ClientSettings
from app.infrastructure.persistence.database.models.api_key import ApiKey


# La fixture `http` vient du conftest du dossier (TestClient(app)).
# Ne pas redéclarer `http` ici → on garantit l’ordre d’initialisation.


def _ensure_api_key() -> str:
    """
    Retourne une clé API existante ou en crée une minimale pour le test.
    Crée : Client + ClientSettings + ApiKey.
    """
    with open_session() as s:
        existing = s.query(ApiKey).first()
        if existing:
            return existing.key

        client = Client(id=uuid.uuid4(), name="test-client-ingest-window")
        s.add(client)
        s.flush()

        # Les colonnes non fournies utilisent leurs defaults ORM/DB.
        s.add(ClientSettings(
            client_id=client.id,
            alert_grouping_enabled=True,
            alert_grouping_window_seconds=300,
            reminder_notification_seconds=600,
            consecutive_failures_threshold=2,
            heartbeat_threshold_minutes=5,
        ))
        s.flush()

        key_val = f"UT-{uuid.uuid4().hex[:24]}"
        s.add(ApiKey(id=uuid.uuid4(), client_id=client.id, key=key_val))
        s.commit()
        return key_val


def _payload(sent_at: datetime) -> dict:
    """
    Payload minimal compatible avec l’endpoint ingest :
    - machine : minimal, suffit pour ensure_machine(...)
    - metrics : un point numeric
    - sent_at : ISO UTC
    """
    return {
        "machine": {"hostname": "ut-host-1"},
        "metrics": [{"id": "cpu_usage", "type": "numeric", "value": 42.0}],
        "sent_at": sent_at.astimezone(timezone.utc).isoformat(),
    }


def test_future_collection_time_returns_422(http):
    api_key = _ensure_api_key()
    future = datetime.now(timezone.utc) + timedelta(minutes=5)  # > FUTURE_MAX (120s par défaut)

    resp = http.post("api/v1/ingest/metrics", headers={"X-API-Key": api_key}, json=_payload(future))

    # Doit être rejeté
    assert resp.status_code == 422, resp.text
    body = resp.json()
    # Optionnel : message précis si tu l'as gardé ainsi
    assert body.get("detail") in {"collection_time in the future", "Unprocessable Entity"}


def test_too_late_returns_202_archived(http):
    api_key = _ensure_api_key()
    too_old = datetime.now(timezone.utc) - timedelta(hours=25)  # > LATE_MAX (86400s)

    resp = http.post("api/v1/ingest/metrics", headers={"X-API-Key": api_key}, json=_payload(too_old))

    # Accepté mais archivé (pas d’ingestion/évaluation)
    assert resp.status_code == 202, resp.text
    assert resp.json().get("reason") == "archived"


def test_normal_window_returns_202_accepted(http):
    api_key = _ensure_api_key()
    recent = datetime.now(timezone.utc) - timedelta(seconds=60)  # Dans la fenêtre

    resp = http.post("api/v1/ingest/metrics", headers={"X-API-Key": api_key}, json=_payload(recent))

    # Ingestion acceptée (asynchrone)
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body.get("status") in {"accepted", "ok"}
