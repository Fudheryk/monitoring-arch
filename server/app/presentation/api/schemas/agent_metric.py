# server/app/presentation/api/schemas/agent_metric.py

from __future__ import annotations
"""
Schemas d'entrée pour les métriques envoyées par l'agent.

Objectif :
- Avoir un format *minimal* côté client (agent),
- Tolérer les alias FR/EN : nom / name, valeur / value, groupe / group_name, etc.
- Distinguer 2 "familles" de métriques :
    * BuiltinMetricIn  : métriques connues du catalogue backend (vendor = "builtin")
    * VendorMetricIn   : métriques d'un vendor tiers (vendor != "builtin")
"""

from typing import Any, Literal, Optional, Union

from pydantic import (
    BaseModel,
    Field,
    AliasChoices,
    field_validator,
    ConfigDict,
    model_validator,
)


# ─────────────────────────────────────────────────────────────
# Base commune à TOUTES les métriques agent
# ─────────────────────────────────────────────────────────────

class AgentMetricBase(BaseModel):
    """
    Schéma générique de métrique venant de l'agent.

    Champs principaux :
        name          ← alias: "nom", "name", "id"
        value         ← alias: "valeur", "value"
        group_name    ← alias: "groupe", "group", "group_name"
        type          ← "numeric" / "boolean" / "string" (optionnel pour builtin)
        vendor        ← ex: "builtin", "acme.nginx", "myapp" (optionnel → "builtin" plus tard)
        unit          ← ex: "%", "MB", "s" (optionnel)
        is_critical   ← criticité suggérée (optionnel)
        description   ← texte libre (optionnel)
        alert_enabled ← boolean d’activation initiale optionnel
    """

    # On ignore silencieusement les champs en trop pour rester tolérant
    model_config = ConfigDict(extra="ignore")

    # Identité logique de la métrique
    name: str = Field(
        ...,
        validation_alias=AliasChoices("nom", "name", "id"),
        description="Identifiant logique de la métrique (ex: 'system.os', 'cpu.usage_percent').",
    )

    # Valeur brute (numérique, bool ou string) – typée plus tard côté backend
    value: Any = Field(
        ...,
        validation_alias=AliasChoices("valeur", "value"),
        description="Valeur actuelle de la métrique.",
    )

    # Groupe logique (system, security, docker, services, ...)
    group_name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("groupe", "group_name", "group"),
        description="Groupe fonctionnel de la métrique (system, security, docker, services, ...).",
    )

    # Type logique – peut être omis pour builtin (le backend saura le déduire)
    type: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("type", "metric_type"),
        description="Type logique: 'numeric', 'boolean' ou 'string'.",
    )

    # Vendor – optionnel, défaut = "builtin" plus tard
    vendor: Optional[str] = Field(
        default=None,
        description="Nom du vendor (ex: 'builtin', 'acme.nginx', 'myapp').",
    )

    # Divers champs optionnels
    unit: Optional[str] = Field(
        default=None,
        description="Unité éventuelle (%, MB, s, ...).",
    )

    is_critical: Optional[bool] = Field(
        default=None,
        validation_alias=AliasChoices("is_critical", "critical"),
        description="Criticité suggérée par l'agent.",
    )

    description: Optional[str] = Field(
        default=None,
        description="Description de la métrique.",
    )

    alert_enabled: Optional[bool] = Field(
        default=None,
        validation_alias=AliasChoices("alert_enabled", "enabled"),
        description="Flag d'activation initiale de l'alerting pour cette métrique.",
    )

    # ───────────── Normalisations simples ─────────────

    @field_validator("type")
    @classmethod
    def _norm_type(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        v = v.strip().lower()
        # tolérer des variantes "number", "boolean", etc.
        if v in {"number", "numeric", "float", "integer", "int"}:
            return "numeric"
        if v in {"bool", "boolean"}:
            return "boolean"
        if v in {"str", "string", "text"}:
            return "string"
        return v

    @field_validator("group_name")
    @classmethod
    def _norm_group(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        return v.strip().lower()

    @field_validator("vendor")
    @classmethod
    def _norm_vendor(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        return v.strip().lower()

    @field_validator("description")
    @classmethod
    def _empty_desc_to_none(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        return v or None


# ─────────────────────────────────────────────────────────────
# 1) BuiltinMetricIn : métriques du catalogue interne
# ─────────────────────────────────────────────────────────────

class BuiltinMetricIn(AgentMetricBase):
    """
    Métrique builtin (présente dans le catalogue backend).

    Hypothèses :
        - vendor est "builtin" ou absent (on force "builtin").
        - type / group_name / description / is_critical peuvent être
          surchargés par le catalogue enregistrée en base de données (metric_definitions).
    """

    # On restreint vendor à "builtin" ou None à l'entrée.
    vendor: Optional[Literal["builtin"]] = Field(
        default=None,
        description="Pour les builtin, vendor est toujours 'builtin' (ou None → forcé).",
    )

    @model_validator(mode="after")
    def _ensure_builtin_vendor(self) -> "BuiltinMetricIn":
        # Si l'agent ne met rien, on force "builtin"
        if self.vendor is None:
            object.__setattr__(self, "vendor", "builtin")
        return self


# ─────────────────────────────────────────────────────────────
# 2) VendorMetricIn : métriques d'un vendor tiers
# ─────────────────────────────────────────────────────────────

class VendorMetricIn(AgentMetricBase):
    """
    Métrique fournie par un vendor tiers (plugin, intégration, etc.).

    Contraintes :
        - vendor est requis et DOIT être différent de "builtin".
        - group_name est requis (pour ranger la métrique dans l'UI).
        - type est requis (numeric/bool/string).
    """

    vendor: str = Field(
        ...,
        description="Nom du vendor (ex: 'acme.nginx', 'myapp'). Ne doit pas être 'builtin'.",
    )

    group_name: str = Field(
        ...,
        description="Groupe fonctionnel (system, security, docker, app, ...).",
    )

    type: str = Field(
        ...,
        description="Type logique: 'numeric', 'boolean' ou 'string'.",
    )

    @model_validator(mode="after")
    def _validate_vendor(self) -> "VendorMetricIn":
        if self.vendor.strip().lower() == "builtin":
            raise ValueError("VendorMetricIn.vendor ne doit pas être 'builtin'.")
        return self


# ─────────────────────────────────────────────────────────────
# Alias pratique : une métrique agent peut être builtin ou vendor
# ─────────────────────────────────────────────────────────────

AgentMetricIn = Union[BuiltinMetricIn, VendorMetricIn]
