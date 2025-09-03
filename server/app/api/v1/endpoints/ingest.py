from __future__ import annotations
"""server/app/api/v1/endpoints/ingest.py
~~~~~~~~~~~~~~~~~~~~~~~~
POST /metrics - Ingestion.
"""
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException
from app.api.schemas.ingest import IngestRequest
from app.core.security import api_key_auth
from app.application.services.registration_service import ensure_machine
from app.application.services.baseline_service import init_if_first_seen
from app.workers.tasks.ingest_tasks import enqueue_samples

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from app.api.schemas.ingest import IngestRequest
from app.core.security import api_key_auth
from app.application.services.registration_service import ensure_machine
from app.application.services.baseline_service import init_if_first_seen
from app.workers.tasks.ingest_tasks import enqueue_samples

router = APIRouter(prefix="/ingest")

@router.post("/metrics", status_code=202)
async def post_metrics(payload: IngestRequest, api_key=Depends(api_key_auth), x_ingest_id: Optional[str] = Header(default=None, alias="X-Ingest-Id")) -> dict:
    if not x_ingest_id or len(x_ingest_id) > 64:
        raise HTTPException(status_code=400, detail="Missing or invalid X-Ingest-Id")
    machine = ensure_machine(payload.machine, api_key)
    init_if_first_seen(machine, payload.metrics)
    enqueue_samples(client_id=str(api_key.client_id), machine_id=str(machine.id), ingest_id=x_ingest_id, metrics=payload.metrics, sent_at=payload.sent_at)
    return {"status": "accepted"}
