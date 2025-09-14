from __future__ import annotations
"""server/app/application/services/evaluation_service.py
~~~~~~~~~~~~~~~~~~~~~~~~
Évaluation des seuils (simple) :
- lit les thresholds de la machine
- récupère le dernier sample de chaque métrique
- compare selon la condition (num/bool/str)
- ouvre/maintient une alerte (FIRING) ou résout
- planifie les notifications APRES commit
"""

import uuid

from typing import Any
import operator as op
import logging

from sqlalchemy import select, desc

from app.infrastructure.persistence.database.session import get_sync_session
from app.infrastructure.persistence.repositories.threshold_repository import ThresholdRepository
from app.infrastructure.persistence.repositories.alert_repository import AlertRepository
from app.infrastructure.persistence.repositories.incident_repository import IncidentRepository


# ---- Table d'opérateurs : accepte "gt" ou ">" (et équivalents) ----------------
OPS = {
    "gt": op.gt,  ">":  op.gt,
    "ge": op.ge,  ">=": op.ge,
    "lt": op.lt,  "<":  op.lt,
    "le": op.le,  "<=": op.le,
    "eq": op.eq,  "==": op.eq,
    "ne": op.ne,  "!=": op.ne,
}

logger = logging.getLogger(__name__)


def _match(condition: str, metric_type: str, sample_value: Any, th) -> bool:
    """
    Compare la valeur du sample avec la valeur du seuil, en fonction du type.

    - metric_type "numeric" -> compare contre th.value_num (float)
    - metric_type "bool"    -> compare contre th.value_bool (bool)
    - sinon (string)        -> compare contre th.value_str (str)
    - supporte aussi 'contains' pour les strings

    Retourne True si la condition est satisfaite.
    """
    cond = (condition or "").strip().lower()

    if metric_type == "numeric":
        # On attend une valeur numérique à comparer via OPS (gt, ge, eq, ...)
        if th.value_num is None:
            return False
        try:
            left = float(sample_value)
        except (TypeError, ValueError):
            return False
        fn = OPS.get(cond)
        return bool(fn(left, float(th.value_num))) if fn else False

    elif metric_type == "bool":
        if th.value_bool is None:
            return False
        left = bool(sample_value)
        fn = OPS.get(cond)
        return bool(fn(left, bool(th.value_bool))) if fn else False

    else:
        # Chaînes : eq/ne/contains
        right = th.value_str
        left = "" if sample_value is None else str(sample_value)
        if cond == "contains":
            return (right is not None) and (right in left)
        if right is None:
            return False
        fn = OPS.get(cond)
        return bool(fn(left, str(right))) if fn else False


def evaluate_machine(machine_id) -> int:
    """
    Évalue les seuils pour une machine et déclenche les alertes.

    - Crée/Maintient une alerte FIRING si un seuil est violé (anti-dup dans le repo).
    - Résout les alertes ouvertes associées au seuil si plus de violation.
    - Crée un incident OPEN (si absent) pour contextualiser la violation.
    - Planifie les notifications APRES le commit (découplage transport).

    Retourne:
        total_alerts (int): nombre d'alertes (créées/mises à jour) pendant cette évaluation.
    """

    # Imports locaux pour éviter les cycles (si présents)
    from app.infrastructure.persistence.database.models.sample import Sample
    from app.infrastructure.persistence.database.models.machine import Machine
    from app.infrastructure.persistence.database.models.incident import Incident

    try:
        machine_uuid = machine_id if isinstance(machine_id, uuid.UUID) else uuid.UUID(str(machine_id))
    except Exception:
        # ID invalide -> rien à faire proprement
        return 0

    total_alerts = 0
    alerts_to_notify: list[str] = []

    with get_sync_session() as session:
        trepo = ThresholdRepository(session)
        arepo = AlertRepository(session)
        irepo = IncidentRepository(session)

        machine = session.get(Machine, machine_uuid)
        client_id = machine.client_id if machine else None

        # On attend que trepo.for_machine(machine_id) retourne des tuples (threshold, metric)
        thresholds = trepo.for_machine(machine_id)

        for th, metric in thresholds:
            # Dernier sample de CETTE métrique
            row = session.scalar(
                select(Sample)
                .where(Sample.metric_id == metric.id)
                .order_by(desc(Sample.ts), desc(Sample.seq))
                .limit(1)
            )
            if not row:
                continue

            # Valeur du sample selon le type DE LA METRIC (plus fiable)
            if metric.type == "numeric":
                value = row.num_value
            elif metric.type == "bool":
                value = row.bool_value
            else:
                value = row.str_value

            # Violation du seuil ?
            breach = _match(th.condition, metric.type, value, th)

            if breach:
                msg = f"{metric.name} ({metric.type}) {th.condition}"

                # Le repo renvoie (alert, created: bool)
                alert, created = arepo.create_firing(
                    threshold_id=th.id,
                    machine_id=machine_id,
                    metric_id=metric.id,
                    severity=th.severity,
                    message=msg,
                    current_value=value,
                )

                # Politique simple : notifier uniquement à la CREATION (anti-spam côté notify)
                if created and th.severity in {"warning", "error", "critical"}:
                    alerts_to_notify.append(str(alert.id))

                # Ouvrir un incident si aucun équivalent OPEN
                if client_id:
                    existing_incident = session.scalar(
                        select(Incident).where(
                            Incident.machine_id == machine_id,
                            Incident.title == f"Threshold breach on {metric.name}",
                            Incident.status == "OPEN",
                        ).limit(1)
                    )
                    if not existing_incident:
                        irepo.open(
                            client_id=client_id,
                            title=f"Threshold breach on {metric.name}",
                            severity=th.severity,
                            machine_id=machine_id,
                            description=msg,
                        )

                total_alerts += 1

            else:
                # Plus de violation -> résoudre les alertes ouvertes de ce seuil
                # (Adapter le repo si besoin d'un scope plus fin)
                arepo.resolve_open_for_threshold(th.id)

        # Commit avant planification des notifications pour garantir la visibilité en DB
        session.commit()

    # Planifier les notifications APRÈS commit
    if alerts_to_notify:
        try:
            from app.workers.tasks.notification_tasks import notify_alert
            for alert_id in alerts_to_notify:
                # En prod : route éventuelle vers la queue 'notify'
                # notify_alert.apply_async(args=[alert_id], queue="notify")
                notify_alert.delay(alert_id)
            logger.info("Notifications planifiées", extra={"count": len(alerts_to_notify)})
        except Exception as e:  # pragma: no cover (défensif)
            logger.error(
                "Échec planification notifications",
                extra={"error": str(e)},
                exc_info=True
            )

    return total_alerts
