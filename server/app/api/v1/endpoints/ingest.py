from __future__ import annotations
"""
server/app/api/v1/endpoints/ingest.py
~~~~~~~~~~~~~~~~~~~~~~~~
POST /ingest/metrics — ingestion de mesures.

Notes :
- Header X-Ingest-Id devient **optionnel** : si absent, on génère un ID.
  (Tes tests postent sans ce header ; on évite de renvoyer 400.)
"""
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException

from app.api.schemas.ingest import IngestRequest
from app.core.security import api_key_auth
from app.application.services.registration_service import ensure_machine
from app.application.services.baseline_service import init_if_first_seen
from app.workers.tasks.ingest_tasks import enqueue_samples

router = APIRouter(prefix="/ingest")


@router.post("/metrics", status_code=202)
async def post_metrics(
    payload: IngestRequest,
    api_key=Depends(api_key_auth),
    x_ingest_id: Optional[str] = Header(default=None, alias="X-Ingest-Id"),
) -> dict:
    # Si le header est fourni mais trop long, on refuse ; sinon on génère un ID.
    if x_ingest_id is not None and len(x_ingest_id) > 64:
        raise HTTPException(status_code=400, detail="Invalid X-Ingest-Id (too long)")
    ingest_id = x_ingest_id or f"auto-{uuid.uuid4().hex}"

    # Enregistre / récupère la machine, initialise la baseline si première vue
    machine = ensure_machine(payload.machine, api_key)
    init_if_first_seen(machine, payload.metrics)

    # Enfile l’ingestion asynchrone
    enqueue_samples(
        client_id=str(api_key.client_id),
        machine_id=str(machine.id),
        ingest_id=ingest_id,
        metrics=payload.metrics,
        sent_at=payload.sent_at,
    )
    return {"status": "accepted", "ingest_id": ingest_id}
