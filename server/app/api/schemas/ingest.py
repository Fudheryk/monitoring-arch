# server/app/api/schemas/ingest.py
from __future__ import annotations
"""
Schémas d’ingestion – format "agent" unifié.

Règles clés :

- Le client envoie un JSON de la forme :

  {
    "metadata": {
      "generator": "...",
      "version": "0.4",
      "schema_version": "1.0",
      "collection_time": "2025-08-07T15:06:05.722442Z",
      "key": "<API_KEY_CLIENT>"
    },
    "machine": {
      "hostname": "smarthack.net",
      "os": "Linux",
      "distribution": "CentOS Linux 7 (Core)",
      "architecture": "x86_64"
    },
    "metrics": [
      {
        "id": "system_001",
        "nom": "system.hostname",
        "valeur": "smarthack.net",
        "type": "string",
        "groupe": "system",
        "is_critical": false,
        "vendor": "builtin"
      },
      ...
    ],
    "analysis": []
  }

- La responsabilité de ce module est :
  * d’accepter ce format brut,
  * de le normaliser vers un modèle unique `IngestRequest` :
      - machine    : MachineInfo
      - metrics    : list[MetricInput]
      - sent_at    : datetime en UTC (fallback = maintenant si absent)
      - agent_key  : clé API éventuelle issue de metadata.key
      - raw_metrics: copie brute des métriques pour debug / logs

- Normalisation des métriques :
  * id   ← nom | id | name      (c’est le *nom logique* de la métrique : system.hostname, cpu.usage_percent, ...)
  * type entrant    : "number" | "string" | "boolean"
  * type normalisé  : "numeric" | "string" | "boolean"
  * value ← valeur | value

- Comportement sur sent_at :
  * Si metadata.collection_time est fourni → utilisé comme sent_at
  * Sinon sent_at = datetime.utcnow() (UTC)

- Notion de vendor :
  * Le champ `vendor` est simplement transmis tel quel depuis le payload agent.
  * Le fallback et la normalisation ("builtin" par défaut, lower, strip, etc.) sont faits
    plus tard dans la couche domaine (ex: baseline_service).
"""

from typing import Any, Literal, Optional, Union
from datetime import datetime, timezone

from pydantic import BaseModel, Field, root_validator, ValidationError, TypeAdapter


# Type interne pour le moteur d’évaluation / stockage
MetricType = Literal["boolean", "numeric", "string"]


class MachineInfo(BaseModel):
    """Représentation normalisée d'une machine.

    Remarque :
    - `hostname` est obligatoire.
    - `os` peut contenir "Linux", "Windows", "FreeBSD", etc.
    - `tags` permet de stocker des infos libres (distribution, arch, etc.).
    """
    hostname: str
    os: Optional[str] = None
    tags: dict[str, Any] = Field(default_factory=dict)
    fingerprint: Optional[str] = None


class MetricInput(BaseModel):
    """Métrique normalisée prête à être persistée / évaluée.

    - id    : identifiant logique stable (ex: "system.hostname", "docker.containers_running")
    - type  : bool | numeric | string (type logique utilisé par le backend)
    - value : valeur typée correspondante (bool, float ou str)

    Métadonnées optionnelles :
    - unit            : unité éventuelle ("%", "bytes", "count", ...)
    - alert_enabled   : drapeau d’activation (optionnel, utilisé côté seuils)
    - group_name      : regroupement logique ("system", "docker", "logs", ...)
    - description     : texte explicatif (UI)
    - is_critical     : criticité telle que vue par l’agent (hint / suggestion)
    - vendor          : origine fonctionnelle de la métrique ("builtin", "aws", "custom-foo", ...)
                        (le fallback à "builtin" est géré plus loin dans la couche domaine)
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
    """Requête d’ingestion normalisée côté backend.

    Ce modèle est ce que les endpoints / services internes doivent consommer.
    Il encapsule :
    - machine      : informations minimales sur la machine
    - metrics      : liste de métriques typées (MetricInput = format canonique backend)
    - sent_at      : moment de la collecte (UTC)
    - agent_key    : clé API éventuellement extraite de metadata.key
    - raw_metrics  : copie brute des métriques reçues (pour debug / logs / idempotence)
    """

    machine: Optional[MachineInfo] = None
    metrics: list[MetricInput]
    sent_at: datetime
    agent_key: Optional[str] = None

    # Copie brute des métriques envoyées par le client (pour traçabilité / debug / hash)
    raw_metrics: Optional[list[dict]] = Field(default=None, exclude=True)

    @root_validator(pre=True)
    def _from_agent_payload(cls, v: dict) -> dict:
        """Convertit le payload "agent" brut en structure normalisée.

        Étapes :
        - stocke v["metrics"] dans raw_metrics (format agent brut, non modifié)
        - extrait metadata.collection_time → sent_at (fallback = now UTC)
        - extrait metadata.key → agent_key (si présent)
        - si machine absente, construit un hostname de secours basé sur la clé
        - propage machine.fingerprint depuis metadata si présent
        - normalise chaque entrée de metrics via AgentMetricIn :
          * name        → MetricInput.id
          * value       → MetricInput.value (bool/float/str)
          * type        → MetricInput.type ("numeric" / "boolean" / "string")
                          (normalisé par AgentMetricIn, ou inféré depuis value si absent)
          * group_name  → MetricInput.group_name (alias groupe/group/group_name)
          * unit, description, is_critical, alert_enabled, vendor : recopiés
        """
        from app.presentation.api.schemas.agent_metric import AgentMetricIn  # import local pour éviter les cycles

        if not isinstance(v, dict):
            return v

        # 1) Conserver les métriques brutes pour le debug / idempotence
        raw_metrics = list(v.get("metrics") or [])
        v["raw_metrics"] = raw_metrics

        md = v.get("metadata") or {}

        # 2) sent_at : on privilégie metadata.collection_time si sent_at non fourni
        if not v.get("sent_at"):
            coll = md.get("collection_time")
            if coll:
                # Laisser Pydantic parser la valeur (ISO 8601 string → datetime)
                v["sent_at"] = coll

        # 3) agent_key : priorité au champ explicite, puis metadata.key
        if not v.get("agent_key"):
            v["agent_key"] = md.get("key")

        # 4) Machine : si absente, on essaie de créer un hostname de secours basé sur la clé
        if not v.get("machine"):
            key = md.get("key") or ""
            suffix = key[-6:] if isinstance(key, str) and len(key) >= 6 else "unknown"
            v["machine"] = {"hostname": f"key-{suffix}"}

        # 4bis) Empreinte machine : machine.fingerprint ou metadata.fingerprint
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
        norm_metrics: list[dict] = []

        # AgentMetricIn est un Union[BuiltinMetricIn, VendorMetricIn],
        # on doit donc passer par un TypeAdapter pour le valider.
        from app.presentation.api.schemas.agent_metric import AgentMetricIn  # import local pour éviter les cycles
        agent_metric_adapter = TypeAdapter(AgentMetricIn)

        for m in raw_metrics:
            try:
                # AgentMetricIn gère :
                # - les alias nom/name/id → name
                # - valeur/value → value
                # - groupe/group/group_name → group_name
                # - normalisation de type/vendor/description...
                metric_in = agent_metric_adapter.validate_python(m)
            except ValidationError:
                # métrique illisible / non conforme → on l’ignore
                continue

            value = metric_in.value

            # Type logique canonique pour MetricInput :
            # - si l’agent l’a fourni (et normalisé par AgentMetricIn), on le garde
            # - sinon, on infère à partir de la valeur
            if metric_in.type is not None:
                typ: MetricType = metric_in.type  # "numeric" | "boolean" | "string"
            else:
                if isinstance(value, bool):
                    typ = "boolean"
                elif isinstance(value, (int, float)):
                    typ = "numeric"
                else:
                    typ = "string"

            # Criticité vue par l’agent (hint) → bool
            is_crit = (
                bool(metric_in.is_critical)
                if metric_in.is_critical is not None
                else False
            )

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

        # 6) sent_at auto si manquant / None (évite les 422 pour absence de date)
        if "sent_at" not in v or v.get("sent_at") in (None, ""):
            v["sent_at"] = datetime.now(timezone.utc)

        return v

    class Config:
        extra = "ignore"
        # On autorise les alias pour laisser Pydantic parser proprement les datetime, etc.
        validate_by_name = True
