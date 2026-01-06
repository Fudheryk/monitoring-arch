from __future__ import annotations
"""
server/app/application/services/machine_status_service.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Service responsable de la mise à jour du statut des machines.

Règle de calcul (avec un seuil en SECONDES par client) :

    last_seen IS NULL          → NO_DATA
    age <= threshold_seconds   → UP
    threshold_seconds < age
        <= threshold_seconds*3 → STALE
    age > threshold_seconds*3  → DOWN

Le seuil par client est dérivé de :
    - ClientSettings.heartbeat_threshold_minutes (minutes → secondes)
    - sinon settings.METRIC_STALENESS_SECONDS
    - sinon 300s par défaut

Ce service ne gère PAS :
    - les alertes
    - les incidents
    - les notifications

Il met simplement à jour le champ Machine.status en base.

Appelé notamment depuis :
    - workers/heartbeat_tasks.py (tâche périodique)
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Dict

from sqlalchemy import select

from app.infrastructure.persistence.database.session import open_session
from app.infrastructure.persistence.database.models.machine import Machine
from app.infrastructure.persistence.repositories.client_settings_repository import (
    ClientSettingsRepository,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Politique de calcul du statut
# ---------------------------------------------------------------------------

def _compute_status(last_seen, threshold_seconds: int) -> str:
    """
    Calcule le statut d'une machine à partir de last_seen et d'un seuil en secondes.

        - last_seen is None                -> "NO_DATA"
        - age <= threshold_seconds         -> "UP"
        - age <= threshold_seconds * 3     -> "STALE"
        - sinon                            -> "DOWN"
    """
    if last_seen is None:
        return "NO_DATA"

    now = datetime.now(timezone.utc)
    age_sec = (now - last_seen).total_seconds()

    if age_sec <= threshold_seconds:
        return "UP"
    elif age_sec <= threshold_seconds * 3:
        return "STALE"
    return "DOWN"


# ---------------------------------------------------------------------------
# Mise à jour d'une machine
# ---------------------------------------------------------------------------

def update_machine_status(machine_id, heartbeat_interval: int = 60) -> Optional[str]:
    """
    Met à jour le statut d'une machine donnée.

    ⚠️ IMPORTANT :
    - On essaie d'abord d'utiliser un seuil **par client** via
      ClientSettingsRepository.get_effective_metric_staleness_seconds(client_id).
    - `heartbeat_interval` reste un fallback global (en secondes) en cas de
      problème (settings manquants, erreur DB, etc.).

    Args:
        machine_id: UUID ou str
        heartbeat_interval: valeur de secours pour le seuil (en secondes)

    Returns:
        Le nouveau statut ("UP" / "STALE" / "DOWN" / "NO_DATA")
        ou None si machine inconnue.
    """
    with open_session() as session:
        machine = session.get(Machine, machine_id)

        if not machine:
            logger.warning(
                "update_machine_status: machine inexistante",
                extra={"machine_id": str(machine_id)},
            )
            return None

        csrepo = ClientSettingsRepository(session)

        # Seuil par client avec fallback
        try:
            threshold_sec = csrepo.get_effective_metric_staleness_seconds(machine.client_id)
        except Exception:
            logger.warning(
                "update_machine_status: fallback heartbeat_interval=%s pour client_id=%s",
                heartbeat_interval,
                machine.client_id,
                exc_info=True,
            )
            threshold_sec = heartbeat_interval

        previous_status = getattr(machine, "status", None)
        new_status = _compute_status(machine.last_seen, threshold_sec)

        # Rien à faire si pas de changement
        if previous_status == new_status:
            return new_status

        machine.status = new_status
        session.add(machine)
        session.commit()

        logger.info(
            "Machine status updated",
            extra={
                "machine_id": str(machine.id),
                "client_id": str(machine.client_id),
                "old": previous_status,
                "new": new_status,
                "threshold_sec": threshold_sec,
            },
        )

        return new_status


# ---------------------------------------------------------------------------
# Mise à jour de TOUTES les machines (batch)
# ---------------------------------------------------------------------------

def update_all_machine_statuses(heartbeat_interval: int = 60) -> int:
    """
    Recalcule le statut de toutes les machines.

    Utilisé par :
        - workers/heartbeat_tasks.py via la tâche Celery "tasks.heartbeat"
        - éventuellement un endpoint/admin pour recalcul manuel

    ⚠️ IMPORTANT :
    - On utilise désormais un **seuil par client** pour la staleness :
        ClientSettingsRepository.get_effective_metric_staleness_seconds(client_id)
    - `heartbeat_interval` reste un **fallback global** en cas de problème
      (absence de settings, erreur DB, etc.), pour ne pas casser l’existant.

    Args:
        heartbeat_interval:
            valeur de secours (en secondes) si aucun paramètre client
            n’est disponible ou en cas d’erreur.

    Returns:
        Nombre de machines dont le statut a été réellement modifié.
    """
    updated = 0

    # Cache des seuils par client pour éviter des requêtes répétées
    thresholds_cache: Dict[str, int] = {}

    with open_session() as session:
        csrepo = ClientSettingsRepository(session)

        machines = session.scalars(select(Machine)).all()

        for m in machines:
            client_id = m.client_id
            key = str(client_id)

            # ───────── Seuil par client (avec cache + fallback) ─────────
            if key not in thresholds_cache:
                try:
                    # Même logique que pour les métriques "no data"
                    thresholds_cache[key] = csrepo.get_effective_metric_staleness_seconds(client_id)
                except Exception:
                    # En cas de problème (erreur DB, etc.) on se rabat sur heartbeat_interval
                    logger.warning(
                        "machine_status_service: fallback global heartbeat_interval=%s pour client_id=%s",
                        heartbeat_interval,
                        client_id,
                        exc_info=True,
                    )
                    thresholds_cache[key] = heartbeat_interval

            threshold_sec = thresholds_cache[key]

            previous_status = getattr(m, "status", None)
            new_status = _compute_status(m.last_seen, threshold_sec)

            if new_status == previous_status:
                # Aucun changement → on skip
                continue

            m.status = new_status
            updated += 1

            logger.info(
                "Machine status changed",
                extra={
                    "machine_id": str(m.id),
                    "client_id": str(client_id),
                    "old": previous_status,
                    "new": new_status,
                    "threshold_sec": threshold_sec,
                },
            )

        if updated:
            session.commit()
            logger.info(
                "[heartbeat] Statuts mis à jour pour %d machines",
                updated,
            )

    return updated
