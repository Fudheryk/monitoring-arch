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

    # ✅ CORRECTION : Détecter le Content-Type pour choisir le bon parser
    content_type = req.headers.get("content-type", "").lower()

    # 1) Si Content-Type est JSON, parser en JSON directement
    if "application/json" in content_type:
        try:
            js = await req.json()
            if isinstance(js, dict):
                data = js
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid JSON payload: {str(e)}"
            )
    
    # 2) Sinon, essayer Form (urlencoded ou multipart)
    else:
        try:
            form = await req.form()
            data = dict(form)
        except Exception:
            # Fallback : essayer quand même JSON si form échoue
            try:
                js = await req.json()
                if isinstance(js, dict):
                    data = js
            except Exception:
                data = {}

    # ✅ Si le payload est vide, erreur explicite
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty payload"
        )

    # Helpers de conversion (uniquement pour les données FORM)
    def _as_bool(v: Any) -> bool:
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in {"1", "true", "on", "yes", "y", "t"}

    def _as_float(v: Any):
        if isinstance(v, (int, float)):
            return float(v)
        try:
            return float(v)
        except Exception:
            return None

    def _as_int(v: Any):
        if isinstance(v, int):
            return v
        try:
            return int(v)
        except Exception:
            return None

    # ✅ CORRECTION : Normaliser SEULEMENT si les valeurs sont des strings (form data)
    # Si c'est déjà du JSON, Pydantic gère la conversion automatiquement
    
    # Normalisation bool (seulement si c'est une string)
    if "alert_enabled" in data and isinstance(data["alert_enabled"], str):
        data["alert_enabled"] = _as_bool(data["alert_enabled"])

    # Normalisation float pour les valeurs numériques (seulement si string)
    for key in ("value", "threshold", "value_num"):
        if key in data and data.get(key) not in (None, ""):
            if isinstance(data[key], str):
                data[key] = _as_float(data[key])

    # Normalisation bool pour threshold_bool (seulement si string)
    for key in ("threshold_bool", "value_bool"):
        if key in data and isinstance(data[key], str):
            data[key] = _as_bool(data[key])

    # Normalisation int pour les champs de config (seulement si string)
    for key in ("consecutive_breaches", "cooldown_sec", "min_duration_sec"):
        if key in data and data.get(key) not in (None, ""):
            if isinstance(data[key], str):
                data[key] = _as_int(data[key])

    # ✅ Laisser Pydantic faire le mapping final (alias, validation)
    try:
        return CreateDefaultThresholdIn.model_validate(data)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Validation error: {str(e)}"
        )

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
