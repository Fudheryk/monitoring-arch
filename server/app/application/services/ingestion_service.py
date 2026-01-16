from __future__ import annotations
"""
server/app/application/services/ingestion_service.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Service d’orchestration de l’ingestion des métriques.

Ce service :
- Valide X-Ingest-Id (longueur max)
- Normalise sent_at (UTC) + applique la fenêtre temporelle
- Idempotence via IngestRepository
- Enregistre/vérifie la machine (ensure_machine)
- Enqueue des samples vers Celery (enqueue_samples)
"""

import hashlib
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Any

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse

from app.api.schemas.ingest import IngestRequest
from app.application.services.registration_service import (
    ensure_machine,
    MachineRegistrationError,
)
from app.core import config as app_config
from app.infrastructure.persistence.database.session import open_session
from app.infrastructure.persistence.repositories.ingest_repository import IngestRepository
from app.workers.tasks.ingest_tasks import enqueue_samples

logger = logging.getLogger(__name__)


def _fingerprint_metrics(metrics: list[dict]) -> str:
    """Hash stable des métriques (id/type/value triés) pour l’idempotence."""
    norm: list[dict] = []
    for m in metrics or []:
        norm.append(
            {
                "id": m.get("id"),
                "type": m.get("type"),
                # compat payload agent legacy : certains envoient "valeur"
                "value": m.get("valeur", m.get("value")),
            }
        )
    norm.sort(key=lambda x: str(x.get("id")))
    data = json.dumps(norm, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _compute_ingest_id(client_id: str, machine_key: str, sent_at: datetime, metrics: list[dict]) -> str:
    """Calcule un ingest_id stable basé sur client/machine/timestamp/fingerprint."""
    t = sent_at.replace(microsecond=0).isoformat()
    base = f"{client_id}|{machine_key}|{t}|{_fingerprint_metrics(metrics)}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def ingest_metrics(
    *,
    payload: IngestRequest,
    api_key: Any,  # instance ApiKey (NE DOIT PAS être None en mode strict)
    x_ingest_id: Optional[str] = None,
) -> dict | JSONResponse:
    settings = app_config.settings

    # -------------------------------------------------------------------
    # -1) Validation X-Ingest-Id (avant tout) : comportement stable
    # -------------------------------------------------------------------
    if x_ingest_id is not None and len(x_ingest_id) > 64:
        raise HTTPException(status_code=400, detail="Invalid X-Ingest-Id (too long)")

    # -------------------------------------------------------------------
    # 0) Auth STRICT : header obligatoire (pas de fallback payload)
    # -------------------------------------------------------------------
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key",
        )

    machine_key: str = getattr(api_key, "key", "") or ""

    # -------------------------------------------------------------------
    # 1) sent_at : validation & normalisation UTC
    # -------------------------------------------------------------------
    if payload.sent_at is None:
        # Conserve la structure d'erreur attendue (compat historique)
        raise HTTPException(
            status_code=422,
            detail=[
                {
                    "type": "datetime_type",
                    "loc": ["body", "sent_at"],
                    "msg": "Input should be a valid datetime",
                    "input": None,
                }
            ],
        )

    sent_at_dt: datetime = payload.sent_at
    sent_at_dt = sent_at_dt.astimezone(timezone.utc) if sent_at_dt.tzinfo else sent_at_dt.replace(tzinfo=timezone.utc)
    sent_at_str = sent_at_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    now = datetime.now(timezone.utc)

    # -------------------------------------------------------------------
    # 2) Fenêtre temporelle
    # -------------------------------------------------------------------
    if (sent_at_dt - now) > timedelta(seconds=settings.INGEST_FUTURE_MAX_SECONDS):
        raise HTTPException(status_code=422, detail="collection_time in the future")

    if (now - sent_at_dt) > timedelta(seconds=settings.INGEST_LATE_MAX_SECONDS):
        return JSONResponse({"reason": "archived"}, status_code=202)

    # -------------------------------------------------------------------
    # 3) Idempotence : ingest_id explicite sinon auto-<hash>
    # -------------------------------------------------------------------
    if x_ingest_id is not None:
        ingest_id = x_ingest_id
    else:
        raw_metrics_for_hash = payload.raw_metrics or []
        raw_hash = _compute_ingest_id(
            str(api_key.client_id),
            machine_key,
            sent_at_dt,
            raw_metrics_for_hash,
        )
        ingest_id = ("auto-" + raw_hash)[:64]

    # -------------------------------------------------------------------
    # 4) Enregistrement / vérification machine
    # -------------------------------------------------------------------
    try:
        machine = ensure_machine(payload.machine, api_key.id)
    except MachineRegistrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc) or "Machine not allowed for this API key",
        ) from exc

    # -------------------------------------------------------------------
    # 5) Insert idempotent dans "ingests"
    # -------------------------------------------------------------------
    with open_session() as session:
        created = IngestRepository(session).create_if_absent(
            client_id=api_key.client_id,
            machine_id=machine.id,
            ingest_id=ingest_id,
            sent_at=sent_at_dt,
        )

    if not created:
        logger.info("ingest.duplicate", extra={"ingest_id": ingest_id, "client_id": str(api_key.client_id)})
        return JSONResponse({"status": "duplicate", "ingest_id": ingest_id}, status_code=200)

    # -------------------------------------------------------------------
    # 6) Enqueue Celery (format canonique: payload.metrics)
    # -------------------------------------------------------------------
    enqueue_samples(
        client_id=str(api_key.client_id),
        machine_id=str(machine.id),
        ingest_id=ingest_id,
        metrics=payload.metrics,
        sent_at=sent_at_str,
    )

    return {"status": "accepted", "ingest_id": ingest_id}
