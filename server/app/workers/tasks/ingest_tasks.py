from __future__ import annotations
"""
server/app/workers/tasks/ingest_tasks.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Ingestion asynchrone + évaluation.

CORRECTIONS :
1. Ajout d'une fonction _parse_metric_dimensions() pour extraire les dimensions
   des noms de métriques (network.<iface>.*, disk[<mp>].*, service.<unit>.*)
2. Modification de process_samples() pour résoudre les définitions correctement
3. Gestion de seq dans SampleRepository (déjà fait avec ON CONFLICT DO NOTHING)
"""

from datetime import datetime, timezone
from typing import Any, Iterable
import re

from app.infrastructure.persistence.database.session import open_session

from app.infrastructure.persistence.database.models.machine import Machine
from app.infrastructure.persistence.database.models.metric_definitions import MetricDefinitions

from app.infrastructure.persistence.repositories.sample_repository import SampleRepository
from app.infrastructure.persistence.repositories.metric_instances_repository import MetricInstancesRepository
from app.infrastructure.persistence.repositories.metric_definitions_repository import MetricDefinitionsRepository

from app.workers.celery_app import celery
from app.application.services.baseline_service import init_if_first_seen, _resolve_catalog_meta
from app.application.services.evaluation_service import evaluate_machine
from app.domain.policies import _norm_metric_type


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de sérialisation et de normalisation
# ─────────────────────────────────────────────────────────────────────────────

def _metric_to_plain(m: Any) -> dict:
    """Convertit n'importe quoi en dict JSON-ready."""
    if hasattr(m, "model_dump"):
        return m.model_dump()
    if hasattr(m, "dict"):
        return m.dict()
    if isinstance(m, dict):
        return m
    return {
        "id": getattr(m, "id", None),
        "name": getattr(m, "name", None),
        "type": getattr(m, "type", None),
        "value": getattr(m, "value", None),
        "unit": getattr(m, "unit", None),
        "alert_enabled": getattr(m, "alert_enabled", None),
        "group_name": getattr(m, "group_name", None),
        "description": getattr(m, "description", None),
        "is_suggested_critical": getattr(m, "is_suggested_critical", None),
        "is_critical": getattr(m, "is_critical", None),
        "vendor": getattr(m, "vendor", None),
    }


def _serialize_sent_at(v: Any) -> Any:
    """Évite d'envoyer un objet datetime non sérialisable vers Celery."""
    if isinstance(v, datetime):
        return (
            v.astimezone(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
    return v


# ─────────────────────────────────────────────────────────────────────────────
# 🔥 NOUVEAU : Parsing des dimensions dynamiques
# ─────────────────────────────────────────────────────────────────────────────

import re


def _parse_metric_dimensions(name: str) -> tuple[str, str]:
    """
    Extrait le pattern "catalogue" (definition_pattern) et la valeur de dimension
    (dimension_value) à partir d'un nom de métrique *effectif* reçu de l’agent.

    Pourquoi c’est critique ?
    - La contrainte unique DB côté `metric_instances` est :
        (machine_id, definition_id, dimension_value)
      Donc pour les familles dynamiques (disk, network, services), il faut un
      `dimension_value` NON VIDE, sinon toutes les instances d’une même famille
      entrent en collision (UniqueViolation) et/ou s’écrasent.

    Exemples attendus (IMPORTANT: on renvoie la *valeur*, pas le nom de la dimension) :
        "cpu.usage_percent"
            -> ("cpu.usage_percent", "")

        "network.eth0.bytes_sent"
            -> ("network.<iface>.bytes_sent", "eth0")

        "disk[/var/log].usage_percent"
            -> ("disk[<mountpoint>].usage_percent", "/var/log")

        "sshd.service"
            -> ("<unit>.service", "sshd")
        
        "temperature.coretemp.0.current"
            -> ("temperature.coretemp.<number>.current", "0")

        "demo.bash.custom_metric"
            -> ("demo.bash.custom_metric", "")

    Returns:
        (definition_pattern, dimension_value)
    """

    # -----------------------------
    # 1) Famille dynamique DISK
    #    ex: disk[/var].usage_percent
    # -----------------------------
    disk_match = re.match(r"^disk\[([^\]]+)\]\.(.+)$", name)
    if disk_match:
        mountpoint = disk_match.group(1)  # ex: "/var/log"
        suffix = disk_match.group(2)      # ex: "usage_percent"
        # Pattern catalogue = disk[<mountpoint>].<suffix>
        return (f"disk[<mountpoint>].{suffix}", mountpoint)

    # -----------------------------
    # 2) Famille dynamique NETWORK
    #    ex: network.eth0.bytes_sent
    # -----------------------------
    network_match = re.match(r"^network\.([^.]+)\.(.+)$", name)
    if network_match:
        iface = network_match.group(1)   # ex: "eth0"
        suffix = network_match.group(2)  # ex: "bytes_sent"
        # Pattern catalogue = network.<iface>.<suffix>
        return (f"network.<iface>.{suffix}", iface)

    # -----------------------------
    # 3) Famille dynamique SERVICES (systemd)
    #    ex: ssh.service, fwupd.service, apt-daily-upgrade.service
    #
    #    ⚠️ Ici ton agent envoie un nom NATIF : "<unit>.service"
    #    donc on ne doit PAS chercher "service.<unit>.service.*"
    #    mais bien matcher le seed: "<unit>.service"
    # -----------------------------
    service_match = re.match(r"^(.+)\.service$", name)
    if service_match:
        unit = service_match.group(1)  # ex: "ssh", "fwupd", "apt-daily-upgrade"
        return ("<unit>.service", unit)
    
    # -----------------------------
    # 4) Famille dynamique TEMPERATURE (cœurs CPU)
    #    ex: temperature.coretemp.0.current
    # -----------------------------
    temp_match = re.match(r"^temperature\.coretemp\.([^.]+)\.(.+)$", name)
    if temp_match:
        number = temp_match.group(1)   # ex: "0"
        suffix = temp_match.group(2)    # ex: "current"
        # Pattern catalogue = temperature.coretemp.<number>.<suffix>
        return (f"temperature.coretemp.<number>.{suffix}", number)

    # -----------------------------
    # 5) Pas de dimension dynamique
    # -----------------------------
    return (name, "")


def _norm_metric(m: dict) -> dict:
    """
    Normalise une métrique dans une forme canonique.
    
    Sortie attendue :
      - id                    : identifiant logique (nom effectif de la métrique)
      - type                  : 'numeric' | 'boolean' | 'string'
      - value                 : valeur brute
      - unit                  : optionnelle
      - alert_enabled         : optionnelle
      - group_name            : pour affichage / config
      - description           : optionnelle
      - is_suggested_critical : boolean
      - vendor                : optionnel (ex: builtin, agent, plugin, ...)
    """
    # 1) identifiant logique
    ident = m.get("id") or m.get("name") or m.get("nom")

    # 2) type normalisé
    raw_type = m.get("type")
    norm_family = _norm_metric_type(raw_type)
    typ = "numeric" if norm_family == "number" else norm_family

    # 3) valeur
    val = m.get("value") if "value" in m else m.get("valeur")

    # 4) group_name
    group_name = (
        m.get("group_name")
        or m.get("group")
        or m.get("groupe")
    )

    # 5) criticité
    if "is_suggested_critical" in m:
        is_suggested_critical = bool(m.get("is_suggested_critical"))
    else:
        is_suggested_critical = bool(m.get("is_critical", False))

    # 6) vendor
    vendor = (m.get("vendor") or "").strip().lower() or None

    return {
        "id": ident,
        "type": typ,
        "value": val,
        "unit": m.get("unit"),
        "alert_enabled": m.get("alert_enabled"),
        "group_name": group_name,
        "description": m.get("description"),
        "is_suggested_critical": is_suggested_critical,
        "vendor": vendor,
    }


def _norm_metrics(metrics: Iterable[dict]) -> list[dict]:
    """Applique _norm_metric à chaque entrée."""
    out: list[dict] = []
    for m in (metrics or []):
        if not isinstance(m, dict):
            m = _metric_to_plain(m)
        out.append(_norm_metric(m))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# API publique
# ─────────────────────────────────────────────────────────────────────────────

def enqueue_samples(
    *,
    client_id: str,
    machine_id: str,
    ingest_id: str,
    metrics: Iterable[Any],
    sent_at: Any,
) -> None:
    """Planifie l'ingestion asynchrone."""
    metrics_payload = [_metric_to_plain(m) for m in (metrics or [])]
    sent_at_payload = _serialize_sent_at(sent_at)

    process_samples.apply_async(
        args=[client_id, machine_id, ingest_id, metrics_payload, sent_at_payload],
        queue="ingest",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tâche Celery
# ─────────────────────────────────────────────────────────────────────────────

@celery.task(name="tasks.ingest")
def process_samples(
    client_id: str,
    machine_id: str,
    ingest_id: str,
    metrics_payload: list[dict],
    sent_at: str | None,
) -> None:
    """
    Écrit les samples puis déclenche l'évaluation de la machine.

    Objectifs :
    - Créer / retrouver les MetricInstance avec la clé DB correcte :
        (machine_id, definition_id, dimension_value)
    - Résoudre les définitions avec support des patterns dynamiques :
        - disk[<mountpoint>].*
        - network.<iface>.*
        - <unit>.service (services systemd)
        - temperature.coretemp.<number>.current
    - Initialiser baseline_value au premier passage
    - Appeler init_if_first_seen uniquement pour les thresholds
    """

    normalized = _norm_metrics(metrics_payload)

    with open_session() as session:
        srepo = SampleRepository(session)
        mi_repo = MetricInstancesRepository(session)
        mdef_repo = MetricDefinitionsRepository(session)

        # -------------------------
        # 1) Timestamp d'ingestion
        # -------------------------
        if isinstance(sent_at, str):
            try:
                ts_dt = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))
            except Exception:
                ts_dt = datetime.now(timezone.utc)
        else:
            ts_dt = datetime.now(timezone.utc)

        # -------------------------
        # 2) Update Machine.last_seen
        # -------------------------
        machine = session.get(Machine, machine_id)
        if machine:
            machine.last_seen = ts_dt

        # -------------------------
        # 3) Résolution defs + upsert MetricInstance
        # -------------------------
        for m in normalized:
            name_effective = m.get("id")
            if not name_effective:
                continue

            # 3.1) Pattern + dimension_value (CRUCIAL pour éviter collisions DB)
            definition_pattern, dimension_value = _parse_metric_dimensions(name_effective)
            dim = (dimension_value or "").strip()

            # 3.2) vendor : IMPORTANT
            #     - Si ton agent n'envoie rien, on doit considérer builtin par défaut
            #       sinon _resolve_catalog_meta() n'appliquera jamais les patterns dynamiques.
            vendor = (m.get("vendor") or "builtin").strip().lower()

            # 3.3) Résolution catalogue (inclut patterns dynamiques POUR builtin)
            #     NB: _resolve_catalog_meta fait déjà "exact match" + dynamiques builtin.
            definition: MetricDefinitions | None = _resolve_catalog_meta(
                name=definition_pattern,
                vendor=vendor,
                mc_repo=mdef_repo,
            )

            # 3.4) Si vendor non-builtin et pas trouvé, on tente builtin (fallback utile pour plugins)
            if definition is None and vendor != "builtin":
                definition = _resolve_catalog_meta(
                    name=definition_pattern,
                    vendor="builtin",
                    mc_repo=mdef_repo,
                )

            # 3.5) get_or_create doit respecter la contrainte unique DB.
            #     -> pour les services, definition_id = "<unit>.service"
            #     -> dimension_value = "sshd" / "fwupd" / ...
            metric_instance = mi_repo.get_or_create(
                machine_id=machine_id,
                definition=definition,
                name_effective=name_effective,
                dimension_value=dim,
            )

            # 3.6) Stocker la dernière valeur (stringifiée)
            raw_value = m.get("value")
            str_value = "" if raw_value is None else str(raw_value)
            metric_instance.last_value = str_value
            metric_instance.updated_at = ts_dt

            # 3.7) baseline_value : init au premier passage
            if getattr(metric_instance, "baseline_value", None) in (None, "") and str_value:
                metric_instance.baseline_value = str_value

            # 3.8) Alert enabled si payload le fournit
            if m.get("alert_enabled") is not None:
                try:
                    metric_instance.is_alerting_enabled = bool(m["alert_enabled"])
                except Exception:
                    pass

            # 3.9) IMPORTANT: évite d'écraser des métadonnées catalogue.
            #     Si ton modèle MetricInstance n'a PAS ces colonnes, ça ne fera rien.
            #     Si elles existent, on ne les remplit que si vides.
            mtype = (m.get("type") or "string").strip().lower()
            if hasattr(metric_instance, "type") and not getattr(metric_instance, "type", None):
                metric_instance.type = mtype

            # (Optionnel) group_name: en général on le dérive du catalogue côté API,
            # donc pas nécessaire de le persister ici.
            # Mais si tu as vraiment une colonne group_name, remplis-la depuis definition si dispo.
            if hasattr(metric_instance, "group_name") and not getattr(metric_instance, "group_name", None):
                metric_instance.group_name = (definition.group_name if definition else (m.get("group_name") or "misc"))

            if hasattr(metric_instance, "vendor") and not getattr(metric_instance, "vendor", None):
                metric_instance.vendor = vendor

            if hasattr(metric_instance, "unit") and not getattr(metric_instance, "unit", None) and m.get("unit"):
                metric_instance.unit = m.get("unit")

            if hasattr(metric_instance, "is_suggested_critical"):
                if m.get("is_suggested_critical") is not None and not getattr(metric_instance, "is_suggested_critical", False):
                    metric_instance.is_suggested_critical = bool(m["is_suggested_critical"])

            # 3.10) Remplacer l'id logique par l'UUID réel pour write_batch
            m["id"] = str(metric_instance.id)

        # -------------------------
        # 4) Écriture des samples
        # -------------------------
        srepo.write_batch(
            machine_id=machine_id,
            metrics_payload=normalized,
            sent_at=sent_at,
        )

        session.commit()

    # -------------------------
    # 5) Threshold init (templates / percent-like)
    # -------------------------
    init_if_first_seen(
        machine_id=machine_id,
        metrics_inputs=normalized,
    )

    # -------------------------
    # 6) Évaluation
    # -------------------------
    evaluate_machine(machine_id)
