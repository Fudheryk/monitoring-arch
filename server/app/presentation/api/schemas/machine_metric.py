# server/app/presentation/api/schemas/machine_metric.py
from __future__ import annotations

"""
Schéma de présentation pour la configuration des métriques d'une machine.

Ce DTO agrège :
  - Metric (instance par machine)
  - MetricDefinitions (métadonnées globales)
  - Threshold (seuil principal)

Il est destiné à l'UI / API publique (vue "configuration des métriques").
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


# Type logique aligné sur ENUM metric_type ('numeric', 'boolean', 'string')
MetricKind = Literal["numeric", "boolean", "string"]


class MachineMetricConfig(BaseModel):
    """
    Configuration complète d'une métrique pour une machine :

      - métadonnées (name, type, group_name, vendor, description)
      - flags d'onboarding / criticité
      - état de la config d'alerting
      - baseline / dernière valeur connue
      - seuil principal (valeur, condition, sévérité)
    """

    # Identité
    id: str = Field(..., description="Identifiant UUID de la métrique (côté DB).")
    name: str = Field(..., description="Nom logique de la métrique (ex: cpu.usage_percent).")
    group_name: str = Field(..., description="Groupe fonctionnel (system, cpu, memory, ...).")
    vendor: str = Field(..., description="Vendor / source de la métrique (builtin, myapp, ...).")

    # Typage logique
    type: MetricKind = Field(
        ...,
        description="Type logique de la métrique: 'numeric', 'boolean' ou 'string'.",
    )

    # Métadonnées descriptives
    description: Optional[str] = Field(
        None,
        description="Description de la métrique (catalogue ou DB).",
    )

    # Infos issues du catalogue (metric_definitions)
    is_suggested_critical: bool = Field(
        False,
        description="Indique si la métrique est suggérée comme critique par le catalogue.",
    )
    default_condition: Optional[str] = Field(
        None,
        description="Opérateur par défaut suggéré (gt, ge, lt, le, eq, ne, ...).",
    )

    # Flags d'alerting & config
    is_alerting_enabled: bool = Field(
        ...,
        description="True si l'alerting est activé pour cette métrique sur cette machine.",
    )
    is_paused: bool = Field(
        ...,
        description="True si l'alerting est temporairement désactivé (pause) pour cette métrique.",
    )
    needs_threshold: bool = Field(
        ...,
        description="True si un seuil doit être configuré manuellement par le client.",
    )

    # Baseline & dernière valeur observée (stockées en texte dans la DB)
    baseline_value: Optional[str] = Field(
        None,
        description="Valeur de référence (baseline) utilisée pour la détection d'écarts.",
    )
    last_value: Optional[str] = Field(
        None,
        description="Dernière valeur ingérée pour cette métrique sur cette machine.",
    )

    # Seuil principal (si existant)
    threshold_value: Optional[str] = Field(
        None,
        description="Valeur du seuil principal (castée en string pour simplifier l'UI).",
    )
    threshold_condition: Optional[str] = Field(
        None,
        description="Condition du seuil (gt, ge, lt, le, eq, ne, ...).",
    )
    threshold_severity: Optional[str] = Field(
        None,
        description="Sévérité du seuil (warning, critical, ...).",
    )


__all__ = ["MachineMetricConfig", "MetricKind"]
