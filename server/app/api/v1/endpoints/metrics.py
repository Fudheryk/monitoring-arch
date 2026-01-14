from __future__ import annotations
"""
server/app/api/v1/endpoints/metrics.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Endpoints Metrics (version refacto avec MetricInstance / ThresholdNew).

Rôle principal pour le frontend :
- lister les métriques d'une machine (instances) pour affichage,
- créer / mettre à jour un seuil "default" pour une metric_instance,
- activer / désactiver l'alerting d'une metric_instance,
- mettre en pause / reprendre une metric_instance.

Résumé des routes :
- GET    /api/v1/metrics                          → smoke test / ping
- GET    /api/v1/metrics/{machine_id}             → liste des métriques (instances) d'une machine
- POST   /api/v1/metrics/{metric_instance_id}/thresholds/default
- PATCH  /api/v1/metrics/{metric_instance_id}/alerting
- PATCH  /api/v1/metrics/{metric_instance_id}/pause
"""

import uuid
from typing import Any, Dict, Tuple, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Body
from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.infrastructure.persistence.database.session import get_db
from app.infrastructure.persistence.database.models.machine import Machine
from app.infrastructure.persistence.database.models.metric_instance import MetricInstance
from app.infrastructure.persistence.database.models.metric_definitions import MetricDefinitions
from app.infrastructure.persistence.database.models.threshold_new import ThresholdNew

from app.presentation.api.schemas.threshold import (
    CreateDefaultThresholdIn,
    ToggleAlertingIn,
)
from app.api.schemas.metric_pause import TogglePauseIn
from app.domain.policies import _norm_metric_type, normalize_comparison


router = APIRouter(prefix="/metrics")


# ============================================================================
# Helpers internes
# ============================================================================

def _serialize_metric_instance(
    mi: MetricInstance,
    definition: Optional[MetricDefinitions],
) -> dict:
    """
    Construit la représentation "metric" renvoyée au frontend à partir :
    - de l'instance (MetricInstance),
    - de la définition globale (MetricDefinitions), si présente.

    On essaie de rester compatible avec l'ancien contrat d'API :
      - name              ← name_effective (nom réel côté agent)
      - group_name        ← definition.group_name (fallback "misc")
      - type              ← definition.type (fallback "string")
      - vendor            ← definition.vendor (fallback "custom")
      - is_suggested_critical ← definition.is_suggested_critical (fallback False)

    Champs purement "instance" :
      - baseline_value, last_value
      - is_alerting_enabled, is_paused
      - (optionnel) needs_threshold → ici False par défaut (flag legacy)
      - dimension_value → nouveau champ, utile pour les métriques dynamiques
    """
    return {
        "id": str(mi.id),
        "name": mi.name_effective,
        "dimension_value": mi.dimension_value,
        "group_name": definition.group_name if definition else "misc",
        "baseline_value": mi.baseline_value,
        "last_value": mi.last_value,
        "needs_threshold": False,  # flag legacy : on le garde pour compat UI
        "type": definition.type if definition else "string",
        "vendor": definition.vendor if definition else "custom",
        "is_alerting_enabled": bool(mi.is_alerting_enabled),
        "is_paused": bool(mi.is_paused),
        "is_suggested_critical": bool(definition.is_suggested_critical) if definition else False,
    }


def _get_instance_with_machine_and_definition(
    db: Session,
    metric_instance_id: str,
    client_id,
) -> Tuple[MetricInstance, Machine, Optional[MetricDefinitions]]:
    """
    Helper commun pour :
      - valider l'UUID de metric_instance_id,
      - charger la MetricInstance,
      - vérifier que la machine associée appartient bien au client,
      - charger éventuellement la MetricDefinitions.

    Soulève HTTPException(404) si :
      - metric_instance inexistante,
      - machine inexistante,
      - machine n'appartient pas au client.
    """
    try:
        mid = uuid.UUID(str(metric_instance_id))
    except Exception:
        raise HTTPException(status_code=404, detail="Metric not found")

    metric_instance: MetricInstance | None = db.get(MetricInstance, mid)
    if not metric_instance:
        raise HTTPException(status_code=404, detail="Metric not found")

    machine: Machine | None = db.get(Machine, metric_instance.machine_id)
    if not machine or machine.client_id != client_id:
        raise HTTPException(status_code=404, detail="Metric not found")

    definition: Optional[MetricDefinitions] = None
    if metric_instance.definition_id:
        definition = db.get(MetricDefinitions, metric_instance.definition_id)

    return metric_instance, machine, definition


# ============================================================================
# GET /api/v1/metrics  → simple ping / smoke test
# ============================================================================
@router.get("")
async def list_metrics_root(
    api_key=Depends(security.api_key_auth),
    db: Session = Depends(get_db),
) -> dict:
    """
    Smoke test minimal pour vérifier que le module est joignable.

    Peut aussi servir plus tard à lister les métriques du client en global
    (toutes machines confondues) si besoin produit.
    """
    return {"items": [], "total": 0}


# ============================================================================
# GET /api/v1/metrics/{machine_id} → liste des métriques d'une machine
# ============================================================================
@router.get("/{machine_id}")
async def list_metrics_by_machine(
    machine_id: str,
    api_key=Depends(security.api_key_auth),
    db: Session = Depends(get_db),
) -> list[dict]:
    """
    Liste les métriques (instances) d'une machine donnée (avec contrôle strict
    d'appartenance client).

    Réponse typique pour le web app :

    [
      {
        "id": "...",
        "name": "cpu.usage_percent",
        "dimension_value": null ou "eth0" / "/",
        "group_name": "system",
        "baseline_value": "0.2",
        "last_value": "0.8",
        "needs_threshold": false,
        "vendor": "builtin",
        "type": "numeric",
        "is_alerting_enabled": true,
        "is_paused": false,
        "is_suggested_critical": false,
      },
      ...
    ]
    """
    # Validation/binding de l'UUID
    try:
        mid = uuid.UUID(str(machine_id))
    except Exception:
        raise HTTPException(status_code=404, detail="Machine not found")

    # Vérification machine + ownership
    machine = db.get(Machine, mid)
    if not machine or machine.client_id != api_key.client_id:
        raise HTTPException(status_code=404, detail="Machine not found")

    # Récupération des instances pour cette machine
    instances: list[MetricInstance] = db.scalars(
        select(MetricInstance)
        .where(MetricInstance.machine_id == mid)
        .order_by(MetricInstance.name_effective)
    ).all()

    # Chargement des définitions associées (en un seul coup)
    def_ids = {mi.definition_id for mi in instances if mi.definition_id is not None}
    definitions_map: dict[uuid.UUID, MetricDefinitions] = {}
    if def_ids:
        defs = db.scalars(
            select(MetricDefinitions).where(MetricDefinitions.id.in_(def_ids))
        ).all()
        definitions_map = {d.id: d for d in defs}

    # On renvoie une liste de dicts simples facilement consommables par le frontend
    return [
        _serialize_metric_instance(
            mi,
            definitions_map.get(mi.definition_id) if mi.definition_id else None,
        )
        for mi in instances
    ]


# ============================================================================
# POST /api/v1/metrics/{metric_instance_id}/thresholds/default
# ============================================================================
@router.post("/{metric_instance_id}/thresholds/default")
async def upsert_default_threshold(
    metric_instance_id: str,
    req: Request,  # ✅ Temporairement pour debug
    api_key=Depends(security.api_key_auth),
    db: Session = Depends(get_db),
) -> dict:
    """
    Crée ou met à jour le seuil "default" d'une metric_instance.
    VERSION DEBUG : Logs pour comprendre pourquoi le body est null
    """
    # ✅ DEBUG : Voir ce que FastAPI reçoit
    import logging
    logger = logging.getLogger(__name__)
    
    logger.error(f"🔍 Content-Type: {req.headers.get('content-type')}")
    logger.error(f"🔍 All Headers: {dict(req.headers)}")
    
    # Lire le body brut
    try:
        body_bytes = await req.body()
        logger.error(f"🔍 Body raw bytes: {body_bytes}")
        logger.error(f"🔍 Body decoded: {body_bytes.decode('utf-8')}")
    except Exception as e:
        logger.error(f"❌ Error reading body: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to read body: {str(e)}")
    
    # Parser avec Pydantic
    try:
        body_str = body_bytes.decode('utf-8')
        import json
        data = json.loads(body_str)
        logger.error(f"🔍 Parsed JSON data: {data}")
        
        payload = CreateDefaultThresholdIn.model_validate(data)
        logger.error(f"✅ Pydantic payload OK: comparison={payload.comparison}, value_num={payload.value_num}")
    except json.JSONDecodeError as e:
        logger.error(f"❌ JSON decode error: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")
    except Exception as e:
        logger.error(f"❌ Pydantic validation error: {e}")
        raise HTTPException(status_code=422, detail=str(e))
    
    # ============================================================
    # À partir d'ici, code normal avec payload validé
    # ============================================================
    
    # Chargement instance + machine + définition, avec contrôle de client
    metric_instance, machine, definition = _get_instance_with_machine_and_definition(
        db=db,
        metric_instance_id=metric_instance_id,
        client_id=api_key.client_id,
    )

    # Toggle alerting global éventuel via le payload
    if payload.alert_enabled is not None:
        metric_instance.is_alerting_enabled = bool(payload.alert_enabled)

    # Chercher seuil "default" existant pour cette instance
    mid = metric_instance.id
    thr: ThresholdNew | None = db.scalars(
        select(ThresholdNew).where(
            ThresholdNew.metric_instance_id == mid,
            ThresholdNew.name == "default",
        )
    ).first()

    # Normalisation de type pour la logique de validation
    val_num, val_bool, val_str = payload.value_num, payload.value_bool, payload.value_str
    cmp_in = normalize_comparison(payload.comparison)

    def _normalize_db_condition(v: Optional[str]) -> Optional[str]:
        return normalize_comparison(v)

    allowed = {
        "number": {"gt", "ge", "lt", "le", "eq", "ne"},
        "boolean": {"eq", "ne"},
        "string": {"eq", "ne", "contains", "not_contains", "regex"},
    }

    # Détermination de la famille de type ("number" / "boolean" / "string")
    if definition is not None:
        raw_type = definition.type
        mtype = _norm_metric_type(raw_type)
    else:
        if val_num is not None:
            mtype = "number"
        elif val_bool is not None:
            mtype = "boolean"
        else:
            mtype = "string"

    # Validation par type de métrique
    if mtype == "number":
        if val_num is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="threshold (float) required for numeric metric",
            )
        cond = cmp_in or (_normalize_db_condition(thr.condition) if thr else "gt")
        if cond not in allowed["number"]:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"invalid comparison for numeric: {cond}",
            )
        next_value_num, next_value_bool, next_value_str = val_num, None, None

    elif mtype == "boolean":
        if val_bool is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="threshold_bool required for boolean metric",
            )
        cond = cmp_in or (_normalize_db_condition(thr.condition) if thr else "eq")
        if cond not in allowed["boolean"]:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"invalid comparison for bool: {cond}",
            )
        next_value_num, next_value_bool, next_value_str = None, val_bool, None

    else:  # string
        if val_str is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="threshold_str required for string metric",
            )
        cond = cmp_in or (_normalize_db_condition(thr.condition) if thr else "eq")
        if cond not in allowed["string"]:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"invalid comparison for string: {cond}",
            )
        next_value_num, next_value_bool, next_value_str = None, None, val_str

    # Upsert du threshold
    created = False
    updated = False

    if thr is None:
        thr = ThresholdNew(
            id=uuid.uuid4(),
            metric_instance_id=metric_instance.id,
            name="default",
            condition=cond,
            value_num=next_value_num,
            value_bool=next_value_bool,
            value_str=next_value_str,
            severity=payload.severity or "warning",
            is_active=True,
            consecutive_breaches=payload.consecutive_breaches or 1,
            cooldown_sec=payload.cooldown_sec or 0,
            min_duration_sec=payload.min_duration_sec or 0,
        )
        db.add(thr)
        created = True
    else:
        if cond != thr.condition:
            thr.condition = cond
            updated = True

        if next_value_num is not None and next_value_num != thr.value_num:
            thr.value_num, thr.value_bool, thr.value_str = next_value_num, None, None
            updated = True
        if next_value_bool is not None and next_value_bool != thr.value_bool:
            thr.value_num, thr.value_bool, thr.value_str = None, next_value_bool, None
            updated = True
        if next_value_str is not None and next_value_str != thr.value_str:
            thr.value_num, thr.value_bool, thr.value_str = None, None, next_value_str
            updated = True

        for field in ("severity", "consecutive_breaches", "cooldown_sec", "min_duration_sec"):
            new_val = getattr(payload, field)
            if new_val is not None and new_val != getattr(thr, field):
                setattr(thr, field, new_val)
                updated = True

    db.commit()

    payload_touched = any(
        v is not None
        for v in (
            payload.value_num,
            payload.value_bool,
            payload.value_str,
            payload.comparison,
            payload.severity,
            payload.consecutive_breaches,
            payload.cooldown_sec,
            payload.min_duration_sec,
        )
    )
    updated_flag = created or updated or (payload.alert_enabled is not None) or payload_touched

    metric_dict = _serialize_metric_instance(metric_instance, definition)

    return {
        "success": True,
        "updated": updated_flag,
        "metric": metric_dict,
        "threshold": {
            "id": str(thr.id),
            "name": thr.name,
            "condition": normalize_comparison(thr.condition),
            "value_num": thr.value_num,
            "value_bool": thr.value_bool,
            "value_str": thr.value_str,
            "severity": thr.severity,
            "is_active": thr.is_active,
            "consecutive_breaches": thr.consecutive_breaches,
            "cooldown_sec": thr.cooldown_sec,
            "min_duration_sec": thr.min_duration_sec,
        },
    }

# ============================================================================
# PATCH /api/v1/metrics/{metric_instance_id}/alerting
# ============================================================================
@router.patch("/{metric_instance_id}/alerting")
async def toggle_alerting(
    metric_instance_id: str,
    body: ToggleAlertingIn = Body(...),
    api_key=Depends(security.api_key_auth),
    db: Session = Depends(get_db),
) -> dict:
    """
    Active / désactive le flag global d'alerting d'une metric_instance.
    """
    metric_instance, machine, definition = _get_instance_with_machine_and_definition(
        db=db,
        metric_instance_id=metric_instance_id,
        client_id=api_key.client_id,
    )

    metric_instance.is_alerting_enabled = bool(body.alert_enabled)
    db.commit()

    metric_dict = _serialize_metric_instance(metric_instance, definition)

    return {
        "success": True,
        "metric": metric_dict,
    }


# ============================================================================
# PATCH /api/v1/metrics/{metric_instance_id}/pause
# ============================================================================
@router.patch("/{metric_instance_id}/pause")
async def toggle_pause_metric(
    metric_instance_id: str,
    body: TogglePauseIn = Body(...),
    api_key=Depends(security.api_key_auth),
    db: Session = Depends(get_db),
) -> dict:
    """
    Met en pause ou reprend une metric_instance.

    Idée produit :
    - is_paused = True  → on CONTINUE de stocker les samples, mais
      evaluation_service ne doit plus déclencher d'incidents pour cette instance.
    - is_paused = False → comportement normal.
    """
    metric_instance, machine, definition = _get_instance_with_machine_and_definition(
        db=db,
        metric_instance_id=metric_instance_id,
        client_id=api_key.client_id,
    )

    metric_instance.is_paused = bool(body.paused)
    db.commit()

    metric_dict = _serialize_metric_instance(metric_instance, definition)

    return {
        "success": True,
        "metric": metric_dict,
    }