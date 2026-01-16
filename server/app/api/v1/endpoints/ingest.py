from __future__ import annotations
"""
server/app/api/v1/endpoints/ingest.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
POST /ingest/metrics — point d'entrée d'ingestion des métriques.

Rôle de ce contrôleur :
- recevoir la requête HTTP,
- authentifier via API key,
- extraire X-Ingest-Id,
- déléguer toute la logique métier au service `ingest_metrics`.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Header

from app.api.schemas.ingest import IngestRequest
from app.application.services.ingestion_service import ingest_metrics
from app.core.security import api_key_auth

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("/metrics", status_code=202)
async def post_metrics(
    payload: IngestRequest,
    # ✅ header-only : API key obligatoire
    api_key=Depends(api_key_auth),
    x_ingest_id: Optional[str] = Header(default=None, alias="X-Ingest-Id"),
) -> dict:
    """
    Ingestion des métriques (header-only).

    - `X-API-Key` requis (401 si manquant, 403 si invalide/inactif).
    - `X-Ingest-Id` optionnel (idempotence côté service).
    """
    return ingest_metrics(payload=payload, api_key=api_key, x_ingest_id=x_ingest_id)
