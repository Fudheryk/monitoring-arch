from __future__ import annotations
"""server/app/application/services/evaluation_service.py
~~~~~~~~~~~~~~~~~~~~~~~~
Évaluation des seuils (simple).
"""
from typing import Any
from app.infrastructure.persistence.database.session import get_sync_session
from app.infrastructure.persistence.repositories.threshold_repository import ThresholdRepository
from app.infrastructure.persistence.repositories.alert_repository import AlertRepository
from app.infrastructure.persistence.repositories.incident_repository import IncidentRepository
from sqlalchemy import select, desc

def _match(condition: str, metric_type: str, sample_value: Any, th) -> bool:
    """
    Compare la valeur de sample à la valeur du seuil selon le type/condition.
    """
    if metric_type == "numeric":
        try:
            v = float(sample_value)
        except (TypeError, ValueError):
            return False
        if condition == "gt" and th.value_num is not None: return v > th.value_num
        if condition == "ge" and th.value_num is not None: return v >= th.value_num
        if condition == "lt" and th.value_num is not None: return v < th.value_num
        if condition == "le" and th.value_num is not None: return v <= th.value_num
        if condition == "eq" and th.value_num is not None: return v == th.value_num
        if condition == "ne" and th.value_num is not None: return v != th.value_num
    elif metric_type == "bool":
        v = bool(sample_value)
        if condition == "eq" and th.value_bool is not None: return v == th.value_bool
        if condition == "ne" and th.value_bool is not None: return v != th.value_bool
    else:
        v = "" if sample_value is None else str(sample_value)
        if condition == "eq" and th.value_str is not None: return v == th.value_str
        if condition == "ne" and th.value_str is not None: return v != th.value_str
        if condition == "contains" and th.value_str is not None: return th.value_str in v
    return False


def evaluate_machine(machine_id: str) -> int:
    """
    Évalue les seuils pour une machine et déclenche les alertes.
    - Crée/Met à jour une alerte FIRING si un seuil est violé.
    - Résout les alertes ouvertes du seuil sinon.
    - Planifie les notifications APRÈS le commit (découplage).
      → La tâche notify_alert implémente l'anti-spam (rappel périodique configurable).

    Retourne:
        total_alerts (int): nombre d'alertes (créées ou mises à jour) pendant cette évaluation.
    """
    from app.infrastructure.persistence.database.models.sample import Sample
    from app.infrastructure.persistence.database.models.metric import Metric
    from app.infrastructure.persistence.database.models.machine import Machine
    from app.infrastructure.persistence.database.models.incident import Incident
    import logging

    logger = logging.getLogger(__name__)
    total_alerts = 0
    alerts_to_notify: list[str] = []

    with get_sync_session() as session:
        trepo = ThresholdRepository(session)
        arepo = AlertRepository(session)
        irepo = IncidentRepository(session)

        machine = session.get(Machine, machine_id)
        client_id = machine.client_id if machine else None

        # thresholds.for_machine(machine_id) renvoie (threshold, metric) pour CETTE machine
        thresholds = trepo.for_machine(machine_id)

        for th, metric in thresholds:
            # Récupérer le dernier sample de CETTE métrique (scopée machine via metric.id)
            row = session.scalar(
                select(Sample)
                .where(Sample.metric_id == metric.id)
                .order_by(desc(Sample.ts), desc(Sample.seq))
                .limit(1)
            )
            if not row:
                continue

            # Lire la valeur selon LE TYPE DE LA MÉTRIQUE (plus fiable que row.value_type)
            if metric.type == "numeric":
                value = row.num_value
            elif metric.type == "bool":
                value = row.bool_value
            else:
                value = row.str_value

            # Test de violation
            breach = _match(th.condition, metric.type, value, th)

            if breach:
                msg = f"{metric.name} ({metric.type}) {th.condition} threshold"

                # create_firing renvoie (alert, created: bool)
                alert, created = arepo.create_firing(
                    threshold_id=th.id,
                    machine_id=machine_id,
                    metric_id=metric.id,
                    severity=th.severity,
                    message=msg,
                    current_value=value,
                    # idéal: stocker sample_ts/sample_seq si le modèle le permet
                )

                # Politique simple: notifier seulement à la création (anti-spam côté notification)
                if created and th.severity in {"warning", "critical"}:
                    alerts_to_notify.append(str(alert.id))

                # Création d'incident si aucun incident OPEN équivalent
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
                # NB: Le repository actuel résout "toutes" les alertes ouvertes pour ce seuil,
                #     sans filtrer par machine/metric. Adapter le repo si besoin d'un scope plus fin.
                arepo.resolve_open_for_threshold(th.id)

        # Commit avant planification des notifications pour garantir la visibilité en DB.
        session.commit()

    # Planifier les notifications APRÈS commit
    if alerts_to_notify:
        try:
            from app.workers.tasks.notification_tasks import notify_alert
            for alert_id in alerts_to_notify:
                # si vous routez sur une queue dédiée:
                # notify_alert.apply_async(args=[alert_id], queue="notify")
                notify_alert.delay(alert_id)
            logger.info("Notifications planifiées", extra={"count": len(alerts_to_notify)})
        except Exception as e:
            logger.error(
                "Échec planification notifications",
                extra={"error": str(e)},
                exc_info=True
            )

    return total_alerts
