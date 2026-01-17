from __future__ import annotations
"""
server/app/presentation/api/schemas/threshold.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Schémas Pydantic (v2) pour les endpoints liés aux seuils (thresholds).

Objectifs
---------
- Offrir une compatibilité *entrante* avec l'ancien front (et avec des formulaires)
  via des alias :
  - `threshold`       -> value_num
  - `threshold_bool`  -> value_bool
  - `threshold_str`   -> value_str
  - (pour certains tests) `value` -> value_num
- Centraliser les modèles utilisés par les endpoints /metrics.

Important
---------
La validation *métier* dépend du type de la métrique (number/bool/string) et
est faite **dans l'endpoint**, qui connaît ce type à l’exécution.

⚠️ Remarque :
Ce module est volontairement agnostique du modèle ORM utilisé (Threshold) :
il décrit uniquement la "forme" JSON échangée par l’API.
"""

from typing import Optional
from pydantic import BaseModel, Field, AliasChoices, field_validator

from app.domain.policies import normalize_comparison


# ---------------------------------------------------------------------------
# Objets "sortants" (si tu veux typer certaines réponses)
# ---------------------------------------------------------------------------

class ThresholdOut(BaseModel):
    id: str
    name: str
    condition: str
    value_num: float | None = None
    value_bool: bool | None = None
    value_str: str | None = None
    severity: str
    is_active: bool = True
    consecutive_breaches: int = 1
    cooldown_sec: int = 0
    min_duration_sec: int = 0


class MetricOut(BaseModel):
    id: str
    name: str
    type: str
    is_alerting_enabled: bool


# ---------------------------------------------------------------------------
# Objets "entrants" (payloads)
# ---------------------------------------------------------------------------

class CreateDefaultThresholdIn(BaseModel):
    """
    Création/Upsert du seuil "par défaut" d'une métrique.

    Champs de valeur (un ET un seul selon metric.type) :
      - value_num   (alias acceptés : "threshold", **"value"**)
      - value_bool  (alias : "threshold_bool")
      - value_str   (alias : "threshold_str")

    La cohérence entre type de métrique et champ de valeur utilisé
    (num/bool/str) est contrôlée dans l’endpoint, pas ici.
    """
    alert_enabled: Optional[bool] = None

    # alias "condition" accepté (anciens payloads)
    comparison: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("comparison", "condition"),
    )

    # pour couvrir les tests hérités : "value" mappe vers value_num
    value_num: Optional[float] = Field(
        default=None,
        validation_alias=AliasChoices("value_num", "threshold", "value"),
    )
    value_bool: Optional[bool] = Field(
        default=None,
        validation_alias=AliasChoices("value_bool", "threshold_bool"),
    )
    value_str: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("value_str", "threshold_str"),
    )

    severity: Optional[str] = None
    consecutive_breaches: Optional[int] = None
    cooldown_sec: Optional[int] = None
    min_duration_sec: Optional[int] = None

    @field_validator("comparison")
    @classmethod
    def _norm_comparison(cls, v: Optional[str]) -> Optional[str]:
        # Normalisation en minuscules sans espaces parasites
        return normalize_comparison(v)

    @field_validator("severity")
    @classmethod
    def _norm_severity(cls, v: Optional[str]) -> Optional[str]:
        # Normalisation en minuscules sans espaces parasites
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("value_str")
    @classmethod
    def _empty_to_none(cls, v: Optional[str]) -> Optional[str]:
        # Transforme les chaînes vides ou "  " en None
        if v is None:
            return None
        return v.strip() or None


class ToggleAlertingIn(BaseModel):
    """
    Bascule du flag global d’alerte d’une metric/metric_instance.
    Compatible avec {"enabled": ...} (ancien) et {"alert_enabled": ...} (nouveau).
    """
    alert_enabled: bool = Field(
        validation_alias=AliasChoices("alert_enabled", "enabled")
    )
