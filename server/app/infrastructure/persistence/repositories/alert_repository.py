from __future__ import annotations
"""server/app/infrastructure/persistence/repositories/alert_repository.py
~~~~~~~~~~~~~~~~~~~~~~~~
Repo alerts.
"""
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from app.infrastructure.persistence.database.models.alert import Alert
import datetime as dt
import uuid


class AlertStatus:
    """Statuts possibles des alertes."""
    FIRING = "FIRING"
    RESOLVED = "RESOLVED"


class AlertRepository:
    """Repository pour la gestion des alertes."""

    def __init__(self, session: Session):
        """Initialise le repository avec une session SQLAlchemy."""
        self.s = session

    def create_firing(
        self,
        *,
        threshold_id,
        machine_id,
        metric_instance_id,
        severity,
        message,
        current_value
    ) -> tuple["Alert", bool]:
        """
        Créer ou mettre à jour une alerte en état FIRING.

        Args:
            threshold_id: ID du seuil déclencheur
            machine_id: ID de la machine concernée
            metric_instance_id: ID de la métrique (conservé pour compatibilité)
            severity: Niveau de sévérité
            message: Message d'alerte
            current_value: Valeur actuelle ayant déclenché l'alerte

        Returns:
            (alert, created): l'alerte créée ou mise à jour + bool indiquant si elle vient d'être créée
        """
        # ⚠️ Le schéma DB définit alerts.current_value en TEXT NOT NULL → on force en str
        current_value_str = "" if current_value is None else str(current_value)

        # Vérifie si une alerte FIRING existe déjà (même threshold + machine)
        existing = self.s.scalar(
            select(Alert).where(
                Alert.threshold_id == threshold_id,
                Alert.machine_id == machine_id,
                Alert.metric_instance_id == metric_instance_id,
                Alert.status == AlertStatus.FIRING,
            ).limit(1)
        )

        if existing:
            # Mise à jour de l'alerte existante
            existing.message = message
            existing.current_value = current_value_str
            # garder l'origine (ne pas toucher triggered_at si l'alerte continue)
            existing.triggered_at = existing.triggered_at
            # Politique de sévérité : on applique la nouvelle valeur
            # (adapter ici si vous souhaitez éviter un "downgrade" de sévérité)
            existing.severity = severity

            self.s.add(existing)
            # ✅ S'assurer que l'ID est disponible immédiatement pour l'appelant (envoi notif, etc.)
            self.s.flush()
            return existing, False  # <- déjà existante, pas une création

        # Création d'une nouvelle alerte
        a = Alert(
            # ✅ Générer l'UUID côté app pour garantir un id non-null avant flush/commit
            id=uuid.uuid4(),
            threshold_id=threshold_id,
            machine_id=machine_id,
            metric_instance_id=metric_instance_id,
            status=AlertStatus.FIRING,
            current_value=current_value_str,
            message=message,
            severity=severity,
            triggered_at=dt.datetime.now(dt.timezone.utc),
            # created_at est géré par défaut DB, on peut le laisser vide
        )
        self.s.add(a)
        # ✅ Rendez l'ID utilisable tout de suite (enqueue tâche, logs, etc.)
        self.s.flush()
        return a, True  # <- nouvelle alerte créée

    def resolve_open_for_threshold(self, threshold_id) -> int:
        """
        Résoudre toutes les alertes ouvertes pour un seuil donné.

        Args:
            threshold_id: ID du seuil à résoudre

        Returns:
            Nombre d'alertes résolues
        """
        stmt = (
            update(Alert)
            .where(
                Alert.threshold_id == threshold_id,
                Alert.status != AlertStatus.RESOLVED
            )
            .values(
                status=AlertStatus.RESOLVED,
                resolved_at=dt.datetime.now(dt.timezone.utc)
            )
        )

        try:
            result = self.s.execute(stmt)
            # Optionnel : ne pas commit ici si un UoW/Service gère la transaction
            return result.rowcount or 0
        except SQLAlchemyError:
            self.s.rollback()
            raise

    def resolve_open_for_threshold_instance(
        self,
        threshold_id,
        machine_id,
        metric_instance_id,
        *,
        now: dt.datetime | None = None,
    ) -> int:
        """
        Résout l'alerte (ou les alertes) ouverte(s) pour un triplet
        (threshold_id, machine_id, metric_instance_id).

        ⚠️ IMPORTANT : évite de résoudre toutes les machines.
        """
        if now is None:
            now = dt.datetime.now(dt.timezone.utc)

        stmt = (
            update(Alert)
            .where(
                Alert.threshold_id == threshold_id,
                Alert.machine_id == machine_id,
                Alert.metric_instance_id == metric_instance_id,
                Alert.status == AlertStatus.FIRING,
            )
            .values(
                status=AlertStatus.RESOLVED,
                resolved_at=now,
            )
        )

        try:
            result = self.s.execute(stmt)
            return result.rowcount or 0
        except SQLAlchemyError:
            self.s.rollback()
            raise
