# server/app/api/schemas/ingest.py
from __future__ import annotations
"""
Schémas d’ingestion – format "agent" unifié.

Objectif de ce module
---------------------
- Accepter un format "agent" souple (metadata/machine/metrics),
- Normaliser vers un modèle unique `IngestRequest` consommé par le backend :
    - machine     : MachineInfo (OBLIGATOIRE)
    - metrics     : list[MetricInput] (format canonique backend)
    - sent_at     : datetime (UTC)
    - raw_metrics : copie brute des métriques (debug / idempotence)

Règles de normalisation
-----------------------
- Metrics :
  * id    ← nom | id | name (géré par AgentMetricIn, exposé via `metric_in.name`)
  * value ← valeur | value  (géré par AgentMetricIn, exposé via `metric_in.value`)
  * type  ← "numeric" | "boolean" | "string"
    - si fourni (et normalisé) par AgentMetricIn -> conservé
    - sinon -> inféré depuis la valeur (bool/int/float/str)

- sent_at :
  * priorité à `sent_at` si fourni explicitement dans le body
  * sinon `metadata.collection_time` si présent
  * sinon now UTC

- vendor :
  * transmis tel quel depuis l’agent
  * les éventuels defaults ("builtin") / normalisations sont gérés plus loin
"""

from datetime import datetime, timezone
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field, ValidationError, root_validator
from pydantic import TypeAdapter

# Type interne pour le moteur d’évaluation / stockage
MetricType = Literal["boolean", "numeric", "string"]


class MachineInfo(BaseModel):
    """
    Représentation normalisée d'une machine.

    Remarques :
    - `hostname` est obligatoire (mode strict).
    - `os` peut contenir "Linux", "Windows", "FreeBSD", etc.
    - `tags` permet de stocker des infos libres (distribution, arch, etc.).
    - `fingerprint` est optionnel (peut venir de machine.fingerprint ou metadata.*).
    """
    hostname: str
    os: Optional[str] = None
    tags: dict[str, Any] = Field(default_factory=dict)
    fingerprint: Optional[str] = None


class MetricInput(BaseModel):
    """
    Métrique normalisée prête à être persistée / évaluée.

    - id    : identifiant logique stable (ex: "system.hostname", "cpu.usage_percent")
    - type  : "boolean" | "numeric" | "string"
    - value : valeur typée correspondante (bool, float ou str)

    Métadonnées optionnelles (UI / onboarding / classification) :
    - unit            : unité ("%", "bytes", ...)
    - alert_enabled   : hint (optionnel) utilisé côté seuils/onboarding
    - group_name      : regroupement logique ("system", "docker", ...)
    - description     : texte explicatif (UI)
    - is_critical     : criticité vue par l’agent (hint)
    - vendor          : origine ("builtin", "aws", "custom-foo", ...)
    """
    id: str
    type: MetricType
    value: Union[bool, float, str]

    unit: Optional[str] = None
    alert_enabled: Optional[bool] = None

    group_name: Optional[str] = None
    description: Optional[str] = None
    is_critical: bool = False
    vendor: Optional[str] = None


class IngestRequest(BaseModel):
    """
    Requête d’ingestion normalisée côté backend.

    Ce modèle est ce que les endpoints / services internes doivent consommer.

    Il encapsule :
    - machine      : informations machine (OBLIGATOIRE)
    - metrics      : liste de métriques typées (format canonique backend)
    - sent_at      : moment de la collecte (UTC)
    - raw_metrics  : copie brute des métriques reçues (debug / idempotence)

    NOTE (mode strict) :
    - aucune clé API n’est acceptée/transportée dans le payload.
    """
    machine: MachineInfo
    metrics: list[MetricInput]
    sent_at: datetime

    # Copie brute des métriques envoyées par le client (pour traçabilité / debug / hash).
    # Exclue de la sérialisation pour éviter d'exposer/dupliquer inutilement côté réponses.
    raw_metrics: Optional[list[dict]] = Field(default=None, exclude=True)

    @root_validator(pre=True)
    def _from_agent_payload(cls, v: dict) -> dict:
        """
        Convertit le payload "agent" brut en structure normalisée.

        Étapes :
        1) stocke v["metrics"] dans raw_metrics (format agent brut, non modifié)
        2) sent_at :
           - utilise v["sent_at"] si présent,
           - sinon metadata.collection_time,
           - sinon now UTC
        3) machine :
           - DOIT exister et contenir hostname
           - si absent, on met {} pour provoquer une erreur claire sur machine.hostname
        4) fingerprint :
           - propage machine.fingerprint depuis metadata si présent (sans inventer d'identité)
        5) normalise les métriques via AgentMetricIn → MetricInput (format canonique backend)
        """
        if not isinstance(v, dict):
            return v

        # 1) Conserver les métriques brutes pour debug / idempotence
        raw_metrics = list(v.get("metrics") or [])
        v["raw_metrics"] = raw_metrics

        md = v.get("metadata") or {}

        # 2) sent_at : priorité au champ explicite, sinon metadata.collection_time, sinon now UTC
        if not v.get("sent_at"):
            coll = md.get("collection_time")
            if coll:
                # Laisser Pydantic parser la valeur (ISO 8601 str -> datetime)
                v["sent_at"] = coll
        if "sent_at" not in v or v.get("sent_at") in (None, ""):
            v["sent_at"] = datetime.now(timezone.utc)

        # 3) Machine : stricte (pas de hostname de secours basé sur une clé)
        if not v.get("machine"):
            # On force un dict vide pour que l'erreur pointe "machine.hostname" (422 clair)
            v["machine"] = {}

        # 4) Empreinte machine : machine.fingerprint OU metadata.machine_fingerprint|fingerprint
        machine = v.get("machine") or {}
        fp = (
            machine.get("fingerprint")
            or md.get("machine_fingerprint")
            or md.get("fingerprint")
        )
        if fp:
            machine["fingerprint"] = fp
            v["machine"] = machine

        # 5) Normalisation des métriques via AgentMetricIn → MetricInput (format canonique backend)
        # Import local pour éviter les cycles éventuels.
        from app.presentation.api.schemas.agent_metric import AgentMetricIn

        agent_metric_adapter = TypeAdapter(AgentMetricIn)
        norm_metrics: list[dict] = []

        for m in raw_metrics:
            try:
                # AgentMetricIn gère :
                # - alias nom/name/id -> name
                # - valeur/value -> value
                # - groupe/group/group_name -> group_name
                # - normalisation type/vendor/description...
                metric_in = agent_metric_adapter.validate_python(m)
            except ValidationError:
                # Métrique illisible / non conforme -> on ignore (comportement tolérant)
                continue

            value = metric_in.value

            # Type logique canonique :
            # - si fourni par l’agent (et normalisé), on le garde
            # - sinon, on infère depuis la valeur
            if getattr(metric_in, "type", None) is not None:
                typ: MetricType = metric_in.type  # "numeric" | "boolean" | "string"
            else:
                if isinstance(value, bool):
                    typ = "boolean"
                elif isinstance(value, (int, float)):
                    typ = "numeric"
                else:
                    typ = "string"

            # Criticité -> bool (hint)
            is_crit = bool(metric_in.is_critical) if metric_in.is_critical is not None else False

            norm_metrics.append(
                {
                    "id": metric_in.name,
                    "type": typ,
                    "value": value,
                    "unit": metric_in.unit,
                    "alert_enabled": metric_in.alert_enabled,
                    "group_name": metric_in.group_name,
                    "description": metric_in.description,
                    "is_critical": is_crit,
                    "vendor": metric_in.vendor,
                }
            )

        v["metrics"] = norm_metrics

        return v

    class Config:
        # On ignore tout champ inconnu (ex: "analysis") pour rester compatible agent.
        extra = "ignore"
        # Autorise les alias si AgentMetricIn / payload en utilise (Pydantic v2+).
        validate_by_name = True
