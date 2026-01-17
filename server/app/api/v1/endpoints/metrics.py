from __future__ import annotations
"""
server/app/api/v1/endpoints/metrics.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Endpoints Metrics (MetricInstance / Threshold) — JWT cookies (UI).

Routes :
- GET    /api/v1/metrics                          → ping JWT (debug/smoke)
- GET    /api/v1/metrics/{machine_id}             → métriques d'une machine (tenant-scoped)
- POST   /api/v1/metrics/{metric_instance_id}/thresholds/default
- PATCH  /api/v1/metrics/{metric_instance_id}/alerting
- PATCH  /api/v1/metrics/{metric_instance_id}/pause
"""

import uuid
from typing import Optional, Tuple

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas.metric_pause import TogglePauseIn
from app.domain.policies import _norm_metric_type, normalize_comparison
from app.infrastructure.persistence.database.models.machine import Machine
from app.infrastructure.persistence.database.models.metric_definitions import MetricDefinitions
from app.infrastructure.persistence.database.models.metric_instance import MetricInstance
from app.infrastructure.persistence.database.models.threshold import Threshold
from app.infrastructure.persistence.database.session import get_db
from app.presentation.api.deps import get_current_user
from app.presentation.api.schemas.threshold import CreateDefaultThresholdIn, ToggleAlertingIn

router = APIRouter(prefix="/metrics")


# ============================================================================
# Helpers internes
# ============================================================================

def _serialize_metric_instance(
    mi: MetricInstance,
    definition: Optional[MetricDefinitions],
) -> dict:
    """
    Construit la représentation "metric" renvoyée au frontend.

    Champs exposés :
    - name_effective → name (contrat UI)
    - group_name/type/vendor (depuis le catalogue si possible)
    - baseline_value/last_value (instance)
    - is_alerting_enabled/is_paused (instance)
    - is_suggested_critical (catalogue)
    """
    return {
        "id": str(mi.id),
        "name": mi.name_effective,
        "dimension_value": mi.dimension_value,
        "group_name": definition.group_name if definition else "misc",
        "baseline_value": mi.baseline_value,
        "last_value": mi.last_value,
        "needs_threshold": False,  # legacy UI
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
    Charge:
    - MetricInstance
    - Machine associée (pour vérifier l'appartenance au tenant)
    - MetricDefinitions si definition_id présent

    404 si:
    - UUID invalide
    - instance absente
    - machine absente / pas au client
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


def _require_client_id(current_user) -> object:
    """
    Petit helper local: évite de dupliquer le même check partout.
    """
    client_id = getattr(current_user, "client_id", None)
    if not client_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_client_id")
    return client_id


# ============================================================================
# GET /api/v1/metrics  → ping / smoke test (JWT)
# ============================================================================
@router.get("")
async def list_metrics_root(
    current_user=Depends(get_current_user),
) -> dict:
    """
    Ping JWT : si on arrive ici, le cookie access est valide.
    (Conserve l'usage "smoke test" sans API key.)
    """
    _ = _require_client_id(current_user)
    return {"items": [], "total": 0}


# ============================================================================
# GET /api/v1/metrics/{machine_id} → liste des métriques d'une machine
# ============================================================================
@router.get("/{machine_id}")
async def list_metrics_by_machine(
    machine_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> list[dict]:
    """
    Liste les MetricInstance d'une machine (tenant-scoped).
    """
    client_id = _require_client_id(current_user)

    try:
        mid = uuid.UUID(str(machine_id))
    except Exception:
        raise HTTPException(status_code=404, detail="Machine not found")

    machine = db.get(Machine, mid)
    if not machine or machine.client_id != client_id:
        raise HTTPException(status_code=404, detail="Machine not found")

    instances: list[MetricInstance] = db.scalars(
        select(MetricInstance)
        .where(MetricInstance.machine_id == mid)
        .order_by(MetricInstance.name_effective)
    ).all()

    def_ids = {mi.definition_id for mi in instances if mi.definition_id is not None}
    definitions_map: dict[uuid.UUID, MetricDefinitions] = {}
    if def_ids:
        defs = db.scalars(select(MetricDefinitions).where(MetricDefinitions.id.in_(def_ids))).all()
        definitions_map = {d.id: d for d in defs}

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
    payload: CreateDefaultThresholdIn = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    """
    Crée ou met à jour le seuil "default" d'une metric_instance.

    IMPORTANT:
    - Suppression de la version "DEBUG req:Request + logs body brut"
      -> on revient à un Body(...) Pydantic standard.
    - Le proxy webapp doit envoyer Content-Type: application/json.
    """
    client_id = _require_client_id(current_user)

    metric_instance, _machine, definition = _get_instance_with_machine_and_definition(
        db=db,
        metric_instance_id=metric_instance_id,
        client_id=client_id,
    )

    # Toggle alerting global éventuel via le payload
    if payload.alert_enabled is not None:
        metric_instance.is_alerting_enabled = bool(payload.alert_enabled)

    # Chercher seuil "default" existant pour cette instance
    thr: Threshold | None = db.scalars(
        select(Threshold).where(
            Threshold.metric_instance_id == metric_instance.id,
            Threshold.name == "default",
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
        mtype = _norm_metric_type(definition.type)
    else:
        # fallback: déduire du payload
        if val_num is not None:
            mtype = "number"
        elif val_bool is not None:
            mtype = "boolean"
        else:
            mtype = "string"

    # Validation + choix condition
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

    created = False
    updated = False

    if thr is None:
        thr = Threshold(
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
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    """
    Active / désactive le flag global d'alerting d'une metric_instance (tenant-scoped).
    """
    client_id = _require_client_id(current_user)

    metric_instance, _machine, definition = _get_instance_with_machine_and_definition(
        db=db,
        metric_instance_id=metric_instance_id,
        client_id=client_id,
    )

    metric_instance.is_alerting_enabled = bool(body.alert_enabled)
    db.commit()

    return {"success": True, "metric": _serialize_metric_instance(metric_instance, definition)}


# ============================================================================
# PATCH /api/v1/metrics/{metric_instance_id}/pause
# ============================================================================
@router.patch("/{metric_instance_id}/pause")
async def toggle_pause_metric(
    metric_instance_id: str,
    body: TogglePauseIn = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    """
    Met en pause ou reprend une metric_instance (tenant-scoped).

    Produit :
    - is_paused = True  → on continue de stocker les samples, mais l'évaluation
      ne doit plus déclencher d'incidents pour cette instance.
    """
    client_id = _require_client_id(current_user)

    metric_instance, _machine, definition = _get_instance_with_machine_and_definition(
        db=db,
        metric_instance_id=metric_instance_id,
        client_id=client_id,
    )

    metric_instance.is_paused = bool(body.paused)
    db.commit()

    return {"success": True, "metric": _serialize_metric_instance(metric_instance, definition)}
