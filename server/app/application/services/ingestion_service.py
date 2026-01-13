from __future__ import annotations
"""
server/app/application/services/ingestion_service.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Service d’orchestration de l’ingestion des métriques.

Rôle :
    - Valider X-Ingest-Id (longueur max)
    - Résoudre l’API key (optionnelle → fallback sur payload.agent_key)
    - Normaliser sent_at (UTC) + appliquer la fenêtre temporelle
    - Gérer l’idempotence via IngestRepository
      * basée sur le payload brut (payload.raw_metrics)
    - Enregistrer / vérifier la machine (ensure_machine)
    - Initialiser la baseline si première fois (init_if_first_seen)
      * à partir des métriques déjà normalisées (payload.metrics / MetricInput)
    - Enqueue des samples vers Celery (enqueue_samples)
      * en poussant le format canonique (payload.metrics)

Ce service est pensé pour être appelé depuis l’endpoint FastAPI :

    async def post_metrics(...):
        return ingestion_service.ingest_metrics(payload, api_key, x_ingest_id)

On garde volontairement :
    - les HTTPException FastAPI (422, 400, 403)
    - les JSONResponse "archived" et "duplicate"

pour ne pas casser le comportement et les tests existants.
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
from app.core.security import resolve_api_key_from_value
from app.infrastructure.persistence.database.session import open_session
from app.infrastructure.persistence.repositories.ingest_repository import IngestRepository
from app.workers.tasks.ingest_tasks import enqueue_samples

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# Helpers internes (reprennent ceux de l’endpoint pour centraliser la logique)
# ---------------------------------------------------------------------------

def _fingerprint_metrics(metrics: list[dict]) -> str:
    """Hash stable des métriques (id/type/value triés par id) pour l’idempotence."""
    norm = []
    for m in metrics or []:
        norm.append(
            {
                "id": m.get("id"),
                "type": m.get("type"),
                "value": m.get("valeur", m.get("value")),
            }
        )
    norm.sort(key=lambda x: str(x["id"]))
    data = json.dumps(norm, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _compute_ingest_id(client_id: str, machine_key: str, sent_at: datetime, metrics: list[dict]) -> str:
    """Calcule un ingest_id stable (auto-<hash>) basé sur client, machine, timestamp seconde près + fingerprint métriques."""
    t = sent_at.replace(microsecond=0).isoformat()
    base = f"{client_id}|{machine_key}|{t}|{_fingerprint_metrics(metrics)}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Service principal
# ---------------------------------------------------------------------------

def ingest_metrics(
    *,
    payload: IngestRequest,
    api_key: Any,                         # instance ApiKey retournée par api_key_auth_optional (ou None)
    x_ingest_id: Optional[str] = None,    # header X-Ingest-Id brut
) -> dict | JSONResponse:
    """
    Orchestration complète de l’ingestion d’un batch de métriques.

    Rôle :
      - valider X-Ingest-Id (longueur) AVANT l’auth (conformité tests),
      - résoudre l’API key (optionnelle, sinon fallback sur payload.agent_key),
      - normaliser sent_at en UTC + appliquer la fenêtre temporelle,
      - gérer l’idempotence via IngestRepository (ingest_id explicite ou auto-*),
      - appeler ensure_machine(machine_info, api_key_id) pour lier / vérifier la machine,
      - initialiser les baselines si première fois,
      - pousser les samples dans la file Celery.

    Retour :
      - dict simple {"status": "accepted", "ingest_id": ...} en cas normal,
      - JSONResponse({"reason": "archived"}, 202) si trop tard,
      - JSONResponse({"status": "duplicate", ...}, 200) sur doublon.

    Exceptions :
      - HTTPException 400 si X-Ingest-Id trop long,
      - HTTPException 422 si sent_at invalide ou trop dans le futur,
      - HTTPException 403 si lier la machine à la clé est interdit (MachineRegistrationError),
      - HTTPException 401 si la clé agent (payload.agent_key) est invalide.
    """
    settings = app_config.settings

    # -------------------------------------------------------------------
    # -1) Validation du header X-Ingest-Id AVANT tout (conformité tests)
    # -------------------------------------------------------------------
    if x_ingest_id is not None and len(x_ingest_id) > 64:
        raise HTTPException(status_code=400, detail="Invalid X-Ingest-Id (too long)")

    # -------------------------------------------------------------------
    # 0) Auth : api_key optionnelle -> fallback via payload.agent_key
    # -------------------------------------------------------------------
    if api_key is None:
        # Résolution de l'API key via la valeur envoyée par l’agent.
        # On utilise une session courte et on détache l’objet pour
        # éviter les conflits de sessions plus loin.
        with open_session() as s:
            api_key = resolve_api_key_from_value(payload.agent_key or "", s)  # peut lever 401
            # ⚠️ Très important : détacher l'objet de cette session
            # pour éviter "Object ... is already attached to session X"
            s.expunge(api_key)

    # Clé "machine" technique (utilisée pour l’ID d’ingestion auto-* /
    # compat avec l’ancien mécanisme d’idempotence).
    machine_key: str = getattr(api_key, "key", "")

    # -------------------------------------------------------------------
    # 1) sent_at (déjà requis par le schéma, mais on revalide comme avant)
    # -------------------------------------------------------------------
    if payload.sent_at is None:
        # On reproduit la structure de validation Pydantic d’origine
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
    # Normalisation en UTC
    sent_at_dt = (
        sent_at_dt.astimezone(timezone.utc)
        if sent_at_dt.tzinfo
        else sent_at_dt.replace(tzinfo=timezone.utc)
    )
    # Format ISO pour enqueue_samples (sans microsecondes, suffixe Z)
    sent_at_str = sent_at_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    now = datetime.now(timezone.utc)

    # -------------------------------------------------------------------
    # 2) Fenêtre temporelle (future / trop tard)
    # -------------------------------------------------------------------
    # Cas "future" : on rejette (422)
    if (sent_at_dt - now) > timedelta(seconds=settings.INGEST_FUTURE_MAX_SECONDS):
        raise HTTPException(status_code=422, detail="collection_time in the future")

    # Cas "trop tard" : on archive mais on renvoie 202
    if (now - sent_at_dt) > timedelta(seconds=settings.INGEST_LATE_MAX_SECONDS):
        return JSONResponse({"reason": "archived"}, status_code=202)

    # -------------------------------------------------------------------
    # 3) Idempotence : ingest_id explicite (header) ou auto-généré
    # -------------------------------------------------------------------
    if x_ingest_id is not None:
        ingest_id = x_ingest_id
    else:
        # Auto-ingest-id : "auto-" + hash(client_id, machine_key, sent_at, fingerprint_metrics)
        # ⬇️ On utilise ici le payload brut (raw_metrics) pour que l’idempotence
        # reste basée sur ce que l’agent a effectivement envoyé.
        raw_metrics_for_hash = payload.raw_metrics or []
        raw_hash = _compute_ingest_id(
            str(api_key.client_id),
            machine_key,
            sent_at_dt,
            raw_metrics_for_hash,
        )
        # Tronquage à 64 caractères pour respecter la contrainte
        ingest_id = ("auto-" + raw_hash)[:64]

    # -------------------------------------------------------------------
    # 4) Enregistrement / vérification de la machine
    # -------------------------------------------------------------------
    try:
        # ✅ ensure_machine attend maintenant un UUID (api_key_id) et
        # non plus l'objet ApiKey. On lui passe donc api_key.id.
        machine = ensure_machine(payload.machine, api_key.id)
    except MachineRegistrationError as exc:
        # Empêche d'utiliser une clé sur une autre machine ou avec une
        # empreinte incohérente.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc) or "Machine not allowed for this API key",
        )

    # -------------------------------------------------------------------
    # 5) Insert idempotent dans la table "ingests"
    # -------------------------------------------------------------------
    with open_session() as session:
        created = IngestRepository(session).create_if_absent(
            client_id=api_key.client_id,
            machine_id=machine.id,
            ingest_id=ingest_id,
            sent_at=sent_at_dt,
        )

    if not created:
        # On a déjà vu ce couple (client, machine, sent_at, fingerprint métriques)
        logger.info(
            "ingest.duplicate",
            extra={"ingest_id": ingest_id, "client_id": str(api_key.client_id)},
        )
        return JSONResponse({"status": "duplicate", "ingest_id": ingest_id}, status_code=200)

    # -------------------------------------------------------------------
    # 6) Baseline & enqueue asynchrone vers Celery
    # -------------------------------------------------------------------
    # Format *canonique* des métriques côté backend : MetricInput (payload.metrics)
    # - payload.raw_metrics : format agent brut (uniquement pour idempotence / debug)
    # - payload.metrics     : métriques normalisées prêtes pour baseline + file Celery
    normalized_metrics = payload.metrics

    # Enqueue dans la file d’ingest asynchrone :
    # on pousse le format normalisé (MetricInput), qui sera aplati
    # en dict JSON-ready par enqueue_samples / _metric_to_plain.
    enqueue_samples(
        client_id=str(api_key.client_id),
        machine_id=str(machine.id),
        ingest_id=ingest_id,
        metrics=normalized_metrics,
        sent_at=sent_at_str,
    )

    # -------------------------------------------------------------------
    # 7) Retour "standard" (le routeur met status_code=202 par défaut)
    # -------------------------------------------------------------------
    return {"status": "accepted", "ingest_id": ingest_id}
