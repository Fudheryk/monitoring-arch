from __future__ import annotations
"""
server/app/application/services/registration_service.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Service d’enregistrement / lookup des machines.

Rôle :
- Associer une ApiKey à UNE seule Machine (clé par machine).
- Gérer la première ingestion (création + liaison) et les suivantes.
- Vérifier l'empreinte envoyée par la machine (fingerprint).

Contrat :
- ensure_machine(machine_info, api_key) :
    * retourne toujours une Machine valide appartenant au client de la clé.
    * peut lever une MachineRegistrationError (et sous-classes) en cas d’erreur métier.
"""

from typing import Any
from uuid import UUID

from app.infrastructure.persistence.database.models.machine import Machine
from app.infrastructure.persistence.database.models.api_key import ApiKey
from app.infrastructure.persistence.database.session import open_session
from app.infrastructure.persistence.repositories.machine_repository import MachineRepository


# ─────────────────────────────────────────────────────────────────────────────
# Exceptions typées pour la registration des machines
# ─────────────────────────────────────────────────────────────────────────────

class MachineRegistrationError(Exception):
    """Erreur générique liée à l'enregistrement / résolution d'une machine."""


class ApiKeyMachineBindingError(MachineRegistrationError):
    """La clé est liée à une machine invalide (client différent, machine inexistante…)."""


class MachineFingerprintMismatch(MachineRegistrationError):
    """L'empreinte envoyée ne correspond pas à celle associée à la clé."""


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de normalisation
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_machine_info(machine_info: Any) -> dict:
    """
    Normalise les infos machine dans un dict simple.

    Retourne toujours un dict du style :
        {
            "hostname": str,
            "os_type": str | None,
            "os_version": str | None,
            "fingerprint": str | None,
        }

    Accepte :
    - Pydantic v2 : .model_dump()
    - Pydantic v1 : .dict()
    - dict brut
    - objet avec attributs hostname / name / os / os_type / os_version / fingerprint
    """
    # 1) Convertir l'objet en dict "m"
    if machine_info is None:
        m = {}
    elif hasattr(machine_info, "model_dump"):      # Pydantic v2
        m = machine_info.model_dump()
    elif hasattr(machine_info, "dict"):            # Pydantic v1
        m = machine_info.dict()
    elif isinstance(machine_info, dict):
        m = machine_info
    else:
        # Fallback très permissif sur les attributs
        m = {
            "hostname": getattr(machine_info, "hostname", None),
            "name": getattr(machine_info, "name", None),
            "os": getattr(machine_info, "os", None),
            "os_type": getattr(machine_info, "os_type", None),
            "os_version": getattr(machine_info, "os_version", None),
            "fingerprint": getattr(machine_info, "fingerprint", None),
        }

    # 2) Normalisation des champs de base
    hostname_raw = m.get("hostname") or m.get("name") or "unknown"
    hostname = (hostname_raw or "unknown").strip() or "unknown"

    # Accepter soit "os_type", soit "os" venant du schéma MachineInfo
    os_type_raw = m.get("os_type") or m.get("os")
    os_type = (os_type_raw or "").strip() or None

    os_version = (m.get("os_version") or "").strip() or None

    fingerprint = (m.get("fingerprint") or "").strip() or None

    return {
        "hostname": hostname,
        "os_type": os_type,
        "os_version": os_version,
        "fingerprint": fingerprint,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Service principal
# ─────────────────────────────────────────────────────────────────────────────

def ensure_machine(machine_info: Any, api_key_id: UUID) -> Machine:
    """
    Récupère ou crée une machine pour la clé/API key donnée.

    Règles métier :

    - Une ApiKey est liée à UNE seule Machine (via api_key.machine_id).

    - Première ingestion avec cette clé :
        * si api_key.machine_id est NULL :
            - on crée la Machine (hostname, os_type, os_version, fingerprint)
            - on lie api_key.machine_id à cette machine
            - on persiste le tout (commit)

    - Ingestions suivantes :
        * on recharge la Machine via api_key.machine_id
        * on vérifie qu'elle appartient au même client
            - sinon → ApiKeyMachineBindingError
        * si une fingerprint est fournie :
            - si machine.fingerprint est NULL → on l'initialise avec cette valeur
            - si machine.fingerprint != fingerprint → MachineFingerprintMismatch

    Retour :
        - instance de Machine persistée (attachée à la session, mais utilisable hors de celle-ci).

    Exceptions :
        - ApiKeyMachineBindingError
        - MachineFingerprintMismatch
    """
    info = _normalize_machine_info(machine_info)

    with open_session() as s:
        mrepo = MachineRepository(s)
        api_key = s.get(ApiKey, api_key_id)

        if api_key is None:
            raise ApiKeyMachineBindingError("API key not found")

        # ──────────────────────────────────────────────────────────────────
        # Cas 1 : la clé est déjà liée à une machine (chemin "normal" après 1ère ingestion)
        # ──────────────────────────────────────────────────────────────────
        if api_key.machine_id is not None:
            machine = mrepo.get(api_key.machine_id)

            # Incohérence grave : la clé pointe vers une machine inexistante ou d'un autre client
            if not machine or machine.client_id != api_key.client_id:
                raise ApiKeyMachineBindingError(
                    "API key is bound to an invalid or foreign machine"
                )

            # Vérifier / initialiser l'empreinte
            fp = info.get("fingerprint")

            if not fp:
                # La machine ne fournit plus de fingerprint alors qu'un est enregistré
                raise MachineFingerprintMismatch(
                    "Missing machine fingerprint for this API key"
                )
            if machine.fingerprint != fp:
                # Empreinte différente → tentative d'utiliser la même clé sur une autre machine
                raise MachineFingerprintMismatch(
                    "Machine fingerprint mismatch for this API key"
                )

            # Mise à jour éventuelle des infos OS
            updated = False
            if info["os_type"] and machine.os_type != info["os_type"]:
                machine.os_type = info["os_type"]
                updated = True
            if info["os_version"] and machine.os_version != info["os_version"]:
                machine.os_version = info["os_version"]
                updated = True

            if updated:
                s.commit()

            return machine

        # ──────────────────────────────────────────────────────────────────
        # Cas 2 : première ingestion avec cette clé → création + liaison
        # ──────────────────────────────────────────────────────────────────
        machine = Machine(
            client_id=api_key.client_id,
            hostname=info["hostname"],
            os_type=info["os_type"],
            os_version=info["os_version"],
            fingerprint=info.get("fingerprint"),
        )
        s.add(machine)
        s.flush()  # machine.id dispo

        api_key.machine_id = machine.id
        s.add(api_key)

        s.commit()
        s.refresh(machine)

        return machine
