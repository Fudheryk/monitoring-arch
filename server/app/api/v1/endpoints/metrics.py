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
from app.domain.policies import _norm_metric_type


router = APIRouter(prefix="/metrics")


def _normalize_comparison(op: Optional[str]) -> Optional[str]:
    """
    P0: Normalise les opérateurs reçus (UI prototype / saisie humaine) vers le standard API.
    Standard API attendu partout: gt, ge, lt, le, eq, ne, contains, not_contains, regex

    Accepte aussi:
      - gte/lte  (prototype)
      - symboles (>=, <=, >, <, ==, !=)
    """
    if op is None:
        return None
    s = str(op).strip().lower()
    if s == "":
        return None

    aliases = {
        "gte": "ge",
        "lte": "le",
        ">=": "ge",
        "<=": "le",
        ">": "gt",
        "<": "lt",
        "==": "eq",
        "!=": "ne",
    }
    return aliases.get(s, s)

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


async def _parse_default_threshold_payload(req: Request) -> CreateDefaultThresholdIn:
    """
    Parse le payload pour POST /{metric_instance_id}/thresholds/default.

    - Supporte JSON (corps standard) et form-urlencoded (soumission de formulaire HTML).
    - Normalise :
        * alert_enabled      → bool
        * value / threshold  → float (value_num)
        * threshold_bool     → bool
        * consecutive_breaches, cooldown_sec, min_duration_sec → int

    Retourne un objet Pydantic `CreateDefaultThresholdIn` prêt à l'emploi.
    """
    data: Dict[str, Any] = {}

    # 1) Tentative JSON
    try:
        js = await req.json()
        if isinstance(js, dict):
            data = js
    except Exception:
        data = {}

    # 2) Fallback FORM
    if not data:
        try:
            form = await req.form()
            data = dict(form)
        except Exception:
            data = {}

    # Helpers de conversion
    def _as_bool(v: Any) -> bool:
        return str(v).strip().lower() in {"1", "true", "on", "yes", "y", "t"}

    def _as_float(v: Any):
        try:
            return float(v)
        except Exception:
            return None

    def _as_int(v: Any):
        try:
            return int(v)
        except Exception:
            return None

    # Normalisation bool
    if "alert_enabled" in data:
        data["alert_enabled"] = _as_bool(data["alert_enabled"])

    # Normalisation float pour les valeurs numériques (value / threshold / value_num)
    for key in ("value", "threshold", "value_num"):
        if key in data and data.get(key) not in (None, ""):
            data[key] = _as_float(data[key])

    # Normalisation bool pour le threshold_bool / value_bool
    if "threshold_bool" in data:
        data["threshold_bool"] = _as_bool(data["threshold_bool"])
    if "value_bool" in data:
        data["value_bool"] = _as_bool(data["value_bool"])

    # Normalisation int pour les champs de config
    for key in ("consecutive_breaches", "cooldown_sec", "min_duration_sec"):
        if key in data and data.get(key) not in (None, ""):
            data[key] = _as_int(data[key])

    # Laisse les strings (threshold_str / value_str) telles quelles.
    # Pydantic va faire le mapping final (alias, types, etc.).
    return CreateDefaultThresholdIn.model_validate(data)


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
    d’appartenance client).

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
    req: Request,
    api_key=Depends(security.api_key_auth),
    db: Session = Depends(get_db),
) -> dict:
    """
    Crée ou met à jour le seuil "default" d'une metric_instance.

    Comportement :
    - Vérifie que la metric_instance appartient bien au client de la clé.
    - Normalise le type (number/bool/string) pour vérifier la cohérence
      des champs et de l'opérateur (en se basant sur MetricDefinitions.type
      si disponible).
    - Upsert (insert ou update) le ThresholdNew "default".
    - Met à jour metric_instance.is_alerting_enabled si `alert_enabled` est fourni.
    """
    payload = await _parse_default_threshold_payload(req)

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
    cmp_in = _normalize_comparison(payload.comparison)

    def _normalize_db_condition(v: Optional[str]) -> Optional[str]:
        return _normalize_comparison(v)

    allowed = {
        "number": {"gt", "ge", "lt", "le", "eq", "ne"},
        "boolean": {"eq", "ne"},
        "string": {"eq", "ne", "contains", "not_contains", "regex"},
    }

    # Détermination de la famille de type ("number" / "boolean" / "string")
    if definition is not None:
        # definition.type est déjà "numeric" / "boolean" / "string"
        raw_type = definition.type
        mtype = _norm_metric_type(raw_type)  # → "number" | "boolean" | "string"
    else:
        # Fallback : on se base sur la valeur fournie
        if val_num is not None:
            mtype = "number"
        elif val_bool is not None:
            mtype = "boolean"
        else:
            mtype = "string"

    # Validation par type de métrique
    if mtype == "number":
        # Seuil numérique requis
        if val_num is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="threshold (float) required for numeric metric",
            )
        # Opérateur par défaut : "gt" si on n'a rien en base
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
        # Création d'un nouveau seuil "default"
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
        # Mise à jour du seuil existant
        if cond != thr.condition:
            thr.condition = cond
            updated = True

        # On switche le champ de valeur en fonction du type
        if next_value_num is not None and next_value_num != thr.value_num:
            thr.value_num, thr.value_bool, thr.value_str = next_value_num, None, None
            updated = True
        if next_value_bool is not None and next_value_bool != thr.value_bool:
            thr.value_num, thr.value_bool, thr.value_str = None, next_value_bool, None
            updated = True
        if next_value_str is not None and next_value_str != thr.value_str:
            thr.value_num, thr.value_bool, thr.value_str = None, None, next_value_str
            updated = True

        # Champs additionnels
        for field in ("severity", "consecutive_breaches", "cooldown_sec", "min_duration_sec"):
            new_val = getattr(payload, field)
            if new_val is not None and new_val != getattr(thr, field):
                setattr(thr, field, new_val)
                updated = True

    db.commit()

    # Flag "updated" pour que le frontend sache si quelque chose a vraiment changé
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

    # Re-sérialisation avec la définition
    metric_dict = _serialize_metric_instance(metric_instance, definition)

    return {
        "success": True,
        "updated": updated_flag,
        "metric": metric_dict,
        "threshold": {
            "id": str(thr.id),
            "name": thr.name,
            "condition": _normalize_comparison(thr.condition),
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
