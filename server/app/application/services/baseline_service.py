from __future__ import annotations
"""
server/app/application/services/baseline_service.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Initialisation "baseline" + seuils auto lors du premier passage.

Version refactorisée pour MetricInstance + Threshold + ThresholdTemplate.

Ordre logique appliqué :

1) On parse les métriques reçues via AgentMetricIn
2) On récupère les MetricInstance existantes (créées par process_samples)
3) On applique les métadonnées du catalogue (metric_definitions)
4) → SI des ThresholdTemplate existent pour cette métrique :
        - création automatique des Threshold basés sur les templates
        - activation de is_alerting_enabled
        - ignore totalement percent-like
5) → SINON pas de template :
        - fallback "percent-like"
6) Initialise baseline_value si absente
7) Si payload indique alert_enabled → force la valeur
"""

import uuid
from typing import Iterable, Any, Optional

from uuid import UUID

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select, func

from app.core.config import settings
from app.infrastructure.persistence.database.session import open_session
from app.infrastructure.persistence.database.models.metric_instance import MetricInstance
from app.infrastructure.persistence.database.models.metric_definitions import MetricDefinitions
from app.infrastructure.persistence.database.models.threshold import Threshold
from app.infrastructure.persistence.database.models.threshold_template import ThresholdTemplate
from app.infrastructure.persistence.database.models.machine import Machine

from app.infrastructure.persistence.repositories.metric_definitions_repository import (
    MetricDefinitionsRepository,
)
from app.infrastructure.persistence.repositories.threshold_template_repository import (
    ThresholdTemplateRepository,
)

from app.presentation.api.schemas.agent_metric import AgentMetricIn


# ------------------------------------------------------------------------------
# Helpers existants inchangés (normalisation nom/type/percent-like)
# ------------------------------------------------------------------------------

def _get(m: Any, *keys, default=None):
    """Accès tolérant aux dicts / objets Pydantic / objets simples."""
    if isinstance(m, dict):
        for k in keys:
            if k in m:
                return m[k]
        return default
    # Pydantic v2 : .model_dump() existe ; ici on tente getattr simple
    for k in keys:
        v = getattr(m, k, None)
        if v is not None:
            return v
    return default


def _norm_unit(unit: Any) -> Optional[str]:
    if unit is None:
        return None
    try:
        s = str(unit).strip().lower()
        return s or None
    except Exception:
        return None


_PERCENTY_NAMES = {"cpu_load", "memory_usage", "disk_usage", "swap_usage"}


def _is_percent_like(name: Optional[str], unit: Optional[str]) -> bool:
    u = _norm_unit(unit)

    # 1) Unité explicite
    if u in {"%", "percent", "pourcent"}:
        return True

    # 2) Heuristique sur le nom
    if isinstance(name, str):
        lower = name.lower()
        if "percent" in lower or "pourcent" in lower or "%" in lower:
            return True
        if name in _PERCENTY_NAMES:
            return True
        if u == "ratio" and name in _PERCENTY_NAMES:
            return True

    return False


def _default_threshold_value(
    name: Optional[str],
    unit: Optional[str],
    default_percent: float,
) -> float:
    """
    Choisit la valeur du seuil par défaut selon l’unité / le nom :
    - ratio -> 0.9
    - percent / % -> default_percent (ex: 90)
    - heuristique par nom si unité absente :
        cpu_load         -> 0.9 (ratio)
        memory/disk/swap -> default_percent (percent)
    - fallback -> default_percent
    """
    u = _norm_unit(unit)
    if u == "ratio":
        return 0.9
    if u in {"%", "percent"}:
        return float(default_percent)

    if name == "cpu_load":
        return 0.9
    if name in {"memory_usage", "disk_usage", "swap_usage"}:
        return float(default_percent)

    return float(default_percent)


def _coerce_str(val) -> Optional[str]:
    """Convertit prudemment une valeur en str (pour baseline_value)."""
    if val is None:
        return None
    try:
        return str(val)
    except Exception:
        return None


def _metric_type_from_value(value: Any) -> str:
    """
    Infère un type logique 'numeric' / 'boolean' / 'string' à partir de la valeur brute.

    Règles simples et sûres :
      - bool       -> 'boolean'
      - int/float  -> 'numeric'
      - tout le reste -> 'string'
    """
    # bool doit être testé AVANT int, car bool est un sous-type de int en Python
    if isinstance(value, bool):
        return "boolean"

    if isinstance(value, (int, float)):
        return "numeric"

    return "string"


def _resolve_catalog_meta(
    name: str,
    vendor: str,
    mc_repo: "MetricDefinitionsRepository",
) -> MetricDefinitions | None:
    """
    Résout les métadonnées d'une métrique à partir du catalogue en base
    (table `metric_definitions`), en gérant :

      - la correspondance exacte (name + vendor)
      - quelques familles dynamiques builtin :
          * disk[<mountpoint>].*   (ex: disk[/].usage_percent)
          * <unit>.service (ex: sshd.service)
          * network.<iface>.*      (ex: network.eth0.bytes_recv)
          * temperature.coretemp.<number>.* (ex: temperature.coretemp.0.current)
    """

    # 1) Correspondance exacte (name + vendor)
    meta = mc_repo.get_by_name_and_vendor(name=name, vendor=vendor)
    if meta is not None:
        return meta

    # Les familles dynamiques ne concernent actuellement que les métriques builtin
    if vendor != "builtin":
        return None

    # 2) Famille dynamique : partitions disque
    if name.startswith("disk["):
        try:
            _, rest = name.split("].", 1)
        except ValueError:
            pass
        else:
            generic_key = f"disk[<mountpoint>].{rest}"
            meta = mc_repo.get_by_name_and_vendor(name=generic_key, vendor="builtin")
            if meta is not None:
                return meta

    # 3) Famille dynamique : services systemd
    if name.endswith(".service"):
        meta = mc_repo.get_by_name_and_vendor(
            name="<unit>.service",
            vendor="builtin",
        )
        if meta is not None:
            return meta

    # 4) Famille dynamique : network interface
    if name.startswith("network."):
        try:
            parts = name.split(".", 2)
            if len(parts) == 3:
                metric_suffix = parts[2]
                generic_key = f"network.<iface>.{metric_suffix}"
                meta = mc_repo.get_by_name_and_vendor(name=generic_key, vendor="builtin")
                if meta is not None:
                    return meta
        except (ValueError, IndexError):
            pass

    # 5) Famille dynamique : temperature coretemp
    if name.startswith("temperature.coretemp."):
        try:
            parts = name.split(".", 3)
            if len(parts) == 4:
                metric_suffix = parts[3]
                generic_key = f"temperature.coretemp.<number>.{metric_suffix}"
                meta = mc_repo.get_by_name_and_vendor(name=generic_key, vendor="builtin")
                if meta is not None:
                    return meta
        except (ValueError, IndexError):
            pass

    return None


# ------------------------------------------------------------------------------
# Initialisation "baseline" + seuils auto + intégration ThresholdTemplate
# ------------------------------------------------------------------------------

def init_if_first_seen(
    machine_id: str | UUID,
    metrics_inputs: Iterable[Any],
) -> None:
    """
    Version MetricInstance + Threshold + ThresholdTemplate.

    Règles d'or :

    1) On ne crée PAS de MetricInstance ici (elles viennent de ingest_tasks.process_samples)
    2) Si metric_definitions + threshold_templates existent → PRIORITÉ ABSOLUE
    3) Sinon fallback percent-like (si numeric et "percent-like")
    4) On initialise baseline_value sur la première valeur reçue pour chaque métrique
       (y compris vendors tiers), si elle est encore vide.
    """

    default_percent = float(getattr(settings, "DEFAULT_PERCENT_THRESHOLD", 90.0))
    metric_adapter = TypeAdapter(AgentMetricIn)

    with open_session() as session:

        # On vérifie que la machine existe bien
        machine = session.get(Machine, machine_id)
        if not machine:
            return

        mc_repo = MetricDefinitionsRepository(session)
        tpl_repo = ThresholdTemplateRepository(session)

        # On parcourt les métriques reçues (payload normalisé côté ingest)
        for raw in metrics_inputs or []:

            # 1) Parsing générique via AgentMetricIn (robuste à différents formats)
            try:
                metric_in = metric_adapter.validate_python(raw)
            except ValidationError:
                # On ignore les métriques invalides sans casser l'init globale
                continue

            name = metric_in.name
            value = metric_in.value
            if not name:
                # Pas de nom → on ne peut rien faire
                continue

            vendor = (metric_in.vendor or "builtin").strip().lower()
            unit = metric_in.unit
            alert_enabled = metric_in.alert_enabled
            suggested_critical = (
                bool(metric_in.is_critical) if metric_in.is_critical is not None else False
            )

            # 2) Définition catalogue (peut être None, surtout pour vendors tiers)
            #    Gère aussi les patterns dynamiques (network.<iface>.*, disk[<mp>].*, <unit>.service)
            meta = _resolve_catalog_meta(name=name, vendor=vendor, mc_repo=mc_repo)

            # 3) Type logique de la métrique
            if meta and meta.type:
                # On fait confiance au type défini dans le catalogue si présent
                mtype = meta.type
            else:
                # Sinon : type issu de l'agent, ou inféré à partir de la valeur
                mtype = metric_in.type or _metric_type_from_value(value)

            # 4) Récupération des MetricInstance existantes pour cette machine + nom effectif
            #    → couvre builtin + vendors tiers + dimensions (name_effective porte le nom réel)
            instances: list[MetricInstance] = session.scalars(
                select(MetricInstance).where(
                    MetricInstance.machine_id == machine.id,
                    MetricInstance.name_effective == name,
                )
            ).all()

            if not instances:
                # Normalement ne devrait pas arriver : process_samples les crée avant d'appeler init_if_first_seen
                continue

            # 5) Si metric_definitions existe → rechercher d'éventuels templates
            templates: list[ThresholdTemplate] = []
            if meta is not None:
                templates = tpl_repo.get_for_definition(meta.id)

            use_templates = len(templates) > 0

            # 6) TRAITEMENT INSTANCE PAR INSTANCE
            for inst in instances:

                # 6.1) Initialisation de la baseline si vide
                #      → première valeur reçue devient baseline_value
                if inst.baseline_value in (None, ""):
                    bv = _coerce_str(value)
                    if bv is not None:
                        inst.baseline_value = bv

                # 6.2) Forcer alert_enabled si le payload le fournit explicitement
                if alert_enabled is not None:
                    try:
                        inst.is_alerting_enabled = bool(alert_enabled)
                    except Exception:
                        # Ne jamais casser pour un bool bancal
                        pass

                # 6.3) Si on a des templates, ils ont priorité ABSOLUE
                if use_templates:
                    # On regarde si des Threshold existent déjà pour cette instance
                    existing_count = session.scalar(
                        select(func.count())
                        .select_from(Threshold)
                        .where(Threshold.metric_instance_id == inst.id)
                    ) or 0

                    if existing_count == 0:
                        # Création des thresholds depuis les templates
                        for tpl in templates:
                            t = Threshold(
                                id=uuid.uuid4(),
                                metric_instance_id=inst.id,
                                name=tpl.name,
                                condition=tpl.condition,
                                value_num=tpl.value_num,
                                value_bool=tpl.value_bool,
                                value_str=tpl.value_str,
                                severity=tpl.severity,
                                is_active=True,
                                consecutive_breaches=tpl.consecutive_breaches,
                                cooldown_sec=tpl.cooldown_sec,
                                min_duration_sec=tpl.min_duration_sec,
                            )
                            session.add(t)

                        # On active l’alerting automatiquement si des templates existent
                        inst.is_alerting_enabled = True

                    # IMPORTANT :
                    # Si des templates existent, on **ignore totalement** la logique percent-like.
                    # On passe donc à l'instance suivante.
                    continue

                # 6.4) Sinon → Fallback percent-like pour les métriques numériques de type "pourcentage"
                #      (ex: cpu.usage_percent, memory.usage_percent, etc.)
                if mtype == "numeric" and _is_percent_like(name, unit):
                    exists = session.scalar(
                        select(func.count())
                        .select_from(Threshold)
                        .where(
                            Threshold.metric_instance_id == inst.id,
                            Threshold.name == "default",
                        )
                    ) or 0

                    if exists == 0:
                        # Création d'un threshold "default" générique
                        session.add(
                            Threshold(
                                id=uuid.uuid4(),
                                metric_instance_id=inst.id,
                                name="default",
                                condition="gt",
                                value_num=_default_threshold_value(
                                    name, unit, default_percent
                                ),
                                severity="warning",
                                is_active=True,
                                consecutive_breaches=1,
                                cooldown_sec=300,
                                min_duration_sec=0,
                            )
                        )
                        inst.is_alerting_enabled = True

                # 6.5) Les autres cas (non percent-like, non couverts par des templates)
                #      ne génèrent pas de seuil auto pour l’instant.
                #      → baseline_value reste néanmoins initialisée si c'était la première valeur.

        # 7) Commit global des baselines + thresholds créés
        session.commit()
