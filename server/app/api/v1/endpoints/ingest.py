from __future__ import annotations

"""
server/app/api/v1/endpoints/ingest.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
POST /ingest/metrics — point d'entrée d'ingestion des métriques.    
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header

from app.api.schemas.ingest import IngestRequest
from app.application.services.ingestion_service import ingest_metrics
from app.presentation.api.deps import api_key_auth_optional  # clé de voûte pour les tests


router = APIRouter(prefix="/ingest")
logger = logging.getLogger(__name__)


@router.post("/metrics", status_code=202)
async def post_metrics(
    payload: IngestRequest,
    api_key=Depends(api_key_auth_optional),
    x_ingest_id: Optional[str] = Header(default=None, alias="X-Ingest-Id"),
) -> dict:
    """
    Endpoint HTTP pour l’ingestion des métriques.

    Le contrôleur se contente de :
    - recevoir la requête HTTP,
    - extraire la clé API (si présente) via la dépendance FastAPI,
    - extraire l’identifiant d’ingestion (X-Ingest-Id),
    - déléguer l’ensemble du traitement au service `ingest_metrics`.

    Toute la logique métier (validation, fenêtre temporelle, idempotence,
    persistance, envoi en file, etc.) vit dans le service applicatif.
    """
    return ingest_metrics(
        payload=payload,
        api_key=api_key,
        x_ingest_id=x_ingest_id,
    )
