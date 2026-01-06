from __future__ import annotations
"""server/app/infrastructure/persistence/repositories/incident_repository.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Repository pour la gestion des incidents.

Principes :
- Le repo **reçoit** une Session SQLAlchemy gérée par l'appelant
  (endpoint via `Depends(get_db)` ou tâche/service via `open_session()`).
- Il ne crée ni ne ferme la session, et **ne commit pas** :
  c'est le rôle du service / worker appelant.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import UUID
from typing import cast

from sqlalchemy import select, desc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.infrastructure.persistence.database.models.incident import Incident, IncidentType
from app.infrastructure.persistence.database.models.sample import Sample


from app.infrastructure.persistence.repositories.client_settings_repository import ClientSettingsRepository


def _dedup_key_for(*, incident_type: IncidentType, machine_id: UUID | None, metric_instance_id: UUID | None, http_target_id: UUID | None) -> str:
    """
    ✅ dedup_key stable (NE DOIT PAS inclure title ni incident_number).
    Doit correspondre à la "réalité métier" de ce qui constitue un incident unique.
    """
    if incident_type == IncidentType.NO_DATA_MACHINE:
        return f"no_data_machine:machine:{machine_id}"
    if incident_type == IncidentType.NO_DATA_METRIC:
        return f"no_data_metric:mi:{metric_instance_id}"
    if incident_type == IncidentType.BREACH:
        return f"breach:mi:{metric_instance_id}"
    if incident_type == IncidentType.HTTP_FAILURE:
        return f"http_failure:http:{http_target_id}"
    return f"generic:{incident_type}:m:{machine_id}:mi:{metric_instance_id}:h:{http_target_id}"


class IncidentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    # =========================================================================
    # OUVERTURE D'INCIDENTS (helpers typés)
    # =========================================================================

    def open_breach_incident(
        self,
        *,
        client_id: UUID,
        machine_id: UUID,
        metric_instance_id: UUID,
        title: str,
        severity: str = "warning",
        description: Optional[str] = None,
    ) -> tuple[Incident, bool]:
        """
        Ouvre un incident de type BREACH (seuil dépassé) dédupliqué (atomiquement)
        par (client_id, machine_id, metric_instance_id, incident_type=BREACH).

        Retourne (incident, created) :
          - created=True  -> nouvel incident créé
          - created=False -> incident OPEN existant réutilisé

        Notes :
          - Utilise self.open() (méthode générique) pour conserver l'atomicité
            via contrainte UNIQUE partielle côté DB (OPEN).
        """
        return self.open(
            client_id=client_id,
            incident_type=IncidentType.BREACH,
            severity=severity,
            title=title,
            dedup_key=_dedup_key_for(incident_type=IncidentType.BREACH, machine_id=machine_id, metric_instance_id=metric_instance_id, http_target_id=None),
            machine_id=machine_id,
            metric_instance_id=metric_instance_id,
            description=description,
        )

    def open_nodata_metric_incident(
        self,
        *,
        client_id: UUID,
        machine_id: UUID,
        metric_instance_id: UUID,
        title: str,
        severity: str = "error",
        description: Optional[str] = None,
    ) -> tuple[Incident, bool]:
        """
        Ouvre un incident de type NO_DATA_METRIC (métrique sans données)
        dédupliqué (atomiquement) par (client_id, machine_id, metric_instance_id, incident_type).

        ⚠️ Important :
          - Ces incidents ne doivent JAMAIS interférer avec les incidents BREACH.
            D'où l'intérêt d'un helper typé.
        """
        return self.open(
            client_id=client_id,
            incident_type=IncidentType.NO_DATA_METRIC,
            severity=severity,
            title=title,
            dedup_key=_dedup_key_for(incident_type=IncidentType.NO_DATA_METRIC, machine_id=machine_id, metric_instance_id=metric_instance_id, http_target_id=None),
            machine_id=machine_id,
            metric_instance_id=metric_instance_id,
            description=description,
        )

    def open_nodata_machine_incident(
        self,
        *,
        client_id: UUID,
        machine_id: UUID,
        title: str,
        severity: str = "critical",
        description: Optional[str] = None,
    ) -> tuple[Incident, bool]:
        """
        Ouvre un incident de type NO_DATA_MACHINE (machine ne communique plus)
        dédupliqué (atomiquement) par (client_id, machine_id, metric_instance_id=None, incident_type).

        Notes :
          - metric_instance_id est explicitement None : incident global machine.
        """
        return self.open(
            client_id=client_id,
            incident_type=IncidentType.NO_DATA_MACHINE,
            severity=severity,
            title=title,
            dedup_key=_dedup_key_for(incident_type=IncidentType.NO_DATA_MACHINE, machine_id=machine_id, metric_instance_id=None, http_target_id=None),
            machine_id=machine_id,
            metric_instance_id=None,
            description=description,
        )

    # =========================================================================
    # RÉSOLUTION D'INCIDENTS (générique + helpers typés)
    # =========================================================================

    def resolve_open_by_machine_and_metric(
        self,
        *,
        client_id: UUID,
        machine_id: UUID,
        metric_instance_id: UUID | None,
        incident_type: IncidentType | str | None = None,
    ) -> Incident | None:
        """
        Résout (status='RESOLVED') UN incident OPEN correspondant.

        Pourquoi ce patch ?
          - Avant : cette méthode résolvait n'importe quel incident OPEN pour
            (client_id, machine_id, metric_instance_id) sans filtrer par type,
            ce qui pouvait fermer un incident BREACH par erreur lors d'une logique
            NO_DATA (ou inversement) -> flapping "ouvre/ferme" observé.

        Paramètres :
          - metric_instance_id :
              * UUID  -> incident lié à une métrique
              * None  -> incident global machine
          - incident_type :
              * si fourni -> on filtre explicitement (recommandé)
              * si None   -> comportement "legacy" (à éviter dans le nouveau code)

        Retour :
          - Incident mis à jour si trouvé
          - None sinon
        """
        q = select(Incident).where(
            Incident.client_id == client_id,
            Incident.machine_id == machine_id,
            Incident.metric_instance_id == metric_instance_id,
            Incident.status == "OPEN",
        )

        # ✅ Filtrage crucial : empêche de résoudre le mauvais type d'incident
        if incident_type is not None:
            q = q.where(Incident.incident_type == incident_type)

        inc = self.db.scalar(q.limit(1))
        if not inc:
            return None

        now = datetime.now(timezone.utc)
        inc.status = "RESOLVED"
        inc.resolved_at = now
        inc.updated_at = now
        self.db.flush()
        return inc

    def resolve_open_breach_incident(
        self,
        *,
        client_id: UUID,
        machine_id: UUID,
        metric_instance_id: UUID,
    ) -> Incident | None:
        """
        Résout uniquement un incident OPEN de type BREACH pour une métrique donnée.

        Usage attendu :
          - evaluation_service.py : lorsqu'un seuil n'est plus violé.
        """
        return self.resolve_open_by_machine_and_metric(
            client_id=client_id,
            machine_id=machine_id,
            metric_instance_id=metric_instance_id,
            incident_type=IncidentType.BREACH,
        )

    def resolve_open_nodata_metric_incident(
        self,
        *,
        client_id: UUID,
        machine_id: UUID,
        metric_instance_id: UUID,
    ) -> Incident | None:
        """
        Résout uniquement un incident OPEN de type NO_DATA_METRIC pour une métrique donnée.

        Usage attendu :
          - metric_freshness_service.py : lorsqu'une métrique redevient fraîche.
        """
        return self.resolve_open_by_machine_and_metric(
            client_id=client_id,
            machine_id=machine_id,
            metric_instance_id=metric_instance_id,
            incident_type=IncidentType.NO_DATA_METRIC,
        )

    def resolve_open_nodata_machine_incident(
        self,
        *,
        client_id: UUID,
        machine_id: UUID,
    ) -> Incident | None:
        """
        Résout uniquement un incident OPEN de type NO_DATA_MACHINE (incident global machine).

        Usage attendu :
          - metric_freshness_service.py : lorsqu'une machine recommence à envoyer des données.
        """
        return self.resolve_open_by_machine_and_metric(
            client_id=client_id,
            machine_id=machine_id,
            metric_instance_id=None,
            incident_type=IncidentType.NO_DATA_MACHINE,
        )

    # =========================================================================
    # MÉTHODE GÉNÉRIQUE EXISTANTE (inchangée) - utilisée par open_* helpers
    # =========================================================================

    def open(
        self,
        *,
        client_id: UUID,
        incident_type: IncidentType | str,
        severity: str,
        title: str,
        dedup_key: str,
        machine_id: Optional[UUID] = None,
        metric_instance_id: Optional[UUID] = None,
        http_target_id: Optional[UUID] = None,
        description: Optional[str] = None,
    ) -> tuple["Incident", bool]:
        """
        Ouvre un incident OPEN dédupliqué (atomiquement) par
        les contraintes uniques partielles OPEN côté DB,
        basées sur (client_id, incident_type, <scope_id>, dedup_key).

        Retourne (incident, created) :
          - created=True  -> nouvel incident créé
          - created=False -> incident OPEN existant réutilisé

        Implémentation atomique :
          - INSERT + flush
          - en cas de course : IntegrityError via contrainte UNIQUE partielle côté DB
          - rollback puis reload de l'incident OPEN existant
        """
        # Normalise incident_type (évite enum vs str fragile, surtout SQLite)
        if isinstance(incident_type, str):
            incident_type = IncidentType(incident_type)

        now = datetime.now(timezone.utc)

        inc = Incident(
            client_id=client_id,
            incident_type=incident_type,
            dedup_key=dedup_key,
            title=title,
            severity=severity,
            machine_id=machine_id,
            metric_instance_id=metric_instance_id,
            http_target_id=http_target_id,
            description=description,
            status="OPEN",
            created_at=now,
            updated_at=now,
        )
        self.db.add(inc)

        try:
            self.db.flush()
            # ✅ Important : pour récupérer incident_number (trigger) et toute modif DB-side
            # (SQLAlchemy ne rafraîchit pas automatiquement après un trigger)
            self.db.refresh(inc)
            return inc, True

        except IntegrityError:
            # ✅ rollback requis avant toute nouvelle requête
            self.db.rollback()

            existing_q = select(Incident).where(
                Incident.client_id == client_id,
                Incident.incident_type == incident_type,
                Incident.status == "OPEN",
            )

            # Scope + dedup_key (doit matcher les uniques partiels)
            if http_target_id is not None:
                existing_q = existing_q.where(Incident.http_target_id == http_target_id, Incident.dedup_key == dedup_key)
            elif metric_instance_id is not None:
                existing_q = existing_q.where(Incident.metric_instance_id == metric_instance_id, Incident.dedup_key == dedup_key)
            elif machine_id is not None:
                existing_q = existing_q.where(Incident.machine_id == machine_id, Incident.dedup_key == dedup_key)
            else:
                existing_q = existing_q.where(Incident.dedup_key == dedup_key)
 
            existing = self.db.scalar(existing_q.limit(1))
            if existing:
                existing.updated_at = datetime.now(timezone.utc)
                self.db.flush()
                self.db.refresh(existing)
                return existing, False

            # Si ce n'est pas notre contrainte unique qui a déclenché, on propage
            raise

    def resolve(self, inc: Incident) -> Incident:
        """
        Résout un incident (quel que soit son type) à partir de l'objet Incident ORM.

        Utilisation :
          - metric_freshness_service.check_metrics_no_data() phase 3
            pour résoudre des incidents NO_DATA_MACHINE devenus obsolètes.

        Note :
          - Ne commit pas (responsabilité de l'appelant).
        """
        now = datetime.now(timezone.utc)
        inc.status = "RESOLVED"
        inc.resolved_at = now
        inc.updated_at = now
        self.db.flush()
        return inc

    # =========================================================================
    # HTTP CHECKS (incidents liés à un http_target_id)
    # =========================================================================
    def open_http_check(
        self,
        *,
        client_id: UUID,
        http_target_id: UUID,
        title: str,
        severity: str = "warning",
        description: Optional[str] = None,
    ) -> tuple[Incident, bool]:
        """
        Ouvre un incident HTTP_FAILURE dédupliqué via dedup_key
        (aligné sur les uniques OPEN côté DB).
        """

        return self.open(
            client_id=client_id,
            incident_type=IncidentType.HTTP_FAILURE,
            severity=severity or "warning",
            title=title,
            dedup_key=_dedup_key_for(incident_type=IncidentType.HTTP_FAILURE, machine_id=None, metric_instance_id=None, http_target_id=http_target_id),
            machine_id=None,
            metric_instance_id=None,
            http_target_id=http_target_id,
            description=description,
        )

    def resolve_open_by_http_target(
        self,
        *,
        client_id: UUID,
        http_target_id: UUID,
    ) -> bool:
        """
        Résout (status='RESOLVED') l'incident OPEN pour (client_id, http_target_id), s'il existe.

        Retourne:
            True  si un incident a été modifié
            False si aucun incident OPEN correspondant
        """

        dedup_key = _dedup_key_for(
            incident_type=IncidentType.HTTP_FAILURE,
            machine_id=None,
            metric_instance_id=None,
            http_target_id=http_target_id,
        )
        inc = self.db.scalar(
            select(Incident)
            .where(
                Incident.client_id == client_id,
                Incident.incident_type == IncidentType.HTTP_FAILURE,
                Incident.http_target_id == http_target_id,
                Incident.dedup_key == dedup_key,
                Incident.status == "OPEN",
            )
            .limit(1)
        )

        if not inc:
            return False

        now = datetime.now(timezone.utc)
        inc.status = "RESOLVED"
        inc.resolved_at = now
        inc.updated_at = now
        self.db.flush()
        return True

    def list_open_incidents(
        self,
        client_id: UUID,
        created_within_seconds: int | None = None,
    ) -> list[Incident]:
        """
        Liste les incidents OPEN pour un client.

        Args:
            client_id:
                UUID du client.

            created_within_seconds:
                - si fourni : ne retourner que les incidents créés dans les
                  N dernières secondes (utile pour les cascades / regroupements récents)
                - si None  : retourne TOUS les incidents OPEN, quelle que soit
                  leur ancienneté (utile pour les reminders périodiques).

        Returns:
            Liste d'incidents OPEN triés par created_at DESC.

        Examples:
            # Tous les incidents ouverts (pour reminders groupés)
            all_open = repo.list_open_incidents(client_id)

            # Incidents créés dans les 5 dernières minutes (cascades)
            recent = repo.list_open_incidents(client_id, created_within_seconds=300)
        """
        query = select(Incident).where(
            Incident.client_id == client_id,
            Incident.status == "OPEN",
        )

        if created_within_seconds is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=created_within_seconds)
            query = query.where(Incident.created_at >= cutoff)

        return list(self.db.scalars(query.order_by(Incident.created_at.desc())))

    # =========================================================================
    # MÉTHODES HISTORIQUES GÉNÉRIQUES — COMPATIBILITÉ CONSERVÉE
    # =========================================================================

    def resolve_all_metric_nodata_incidents(
            self,
            client_id: UUID,
            machine_id: UUID,
        ) -> int:
            """
            Résout TOUS les incidents "Metric no data: <metric>" ouverts sur une machine.

            Appelé dans la logique MACHINE-DOWN pour éviter :
            - doublons d'incidents
            - incohérence d'état
            - pollution de l'historique

            Retourne : nombre d'incidents résolus.
            """

            now = datetime.now(timezone.utc)

            incidents = list(self.db.scalars(
                select(Incident)
                .where(
                    Incident.client_id == client_id,
                    Incident.machine_id == machine_id,
                    Incident.status == "OPEN",
                    Incident.incident_type == IncidentType.NO_DATA_METRIC,
                )
            ))

            for inc in incidents:
                inc.status = "RESOLVED"
                inc.resolved_at = now
                inc.updated_at = now

            if incidents:
                self.db.flush()

            return len(incidents)

    def list_open_machine_nodata_incidents(self) -> list[Incident]:
        """
        Retourne tous les incidents ouverts de type « Machine not sending data ».

        Utilisé par metric_freshness_service.check_metrics_no_data() pour
        resynchroniser l'état des incidents globaux machine avec la réalité :
        - machine supprimée,
        - plus de métriques candidates,
        - etc.
        """
        q = (
            select(Incident)
            .where(
                Incident.incident_type == IncidentType.NO_DATA_MACHINE,
                Incident.status == "OPEN",
            )
        )
        return list(self.db.scalars(q))

    def auto_resolve_stale_threshold_incidents(
            self,
            *,
            max_age_hours: int = 24,
            dry_run: bool = False,
            limit: int = 500,
        ) -> int:
        """
        Résout automatiquement les incidents threshold OPEN quand la donnée est stale
        depuis trop longtemps.

        Conditions :
        - Incident OPEN de type BREACH
        - Utilise directement metric_instance_id (toujours présent sur un BREACH)
        - Vérifie le dernier Sample.ts (source de vérité)
        - Si sample_ts est stale vs staleness_threshold_sec (client settings)
        ET incident ouvert depuis > max_age_hours -> résolution auto.

        Args:
            max_age_hours: durée minimale d'ouverture avant auto-resolve
            dry_run: si True, ne modifie rien (compte seulement)
            limit: limite pour éviter de traiter un volume énorme d'un coup

        Returns:
            Nombre d'incidents résolus (ou qui seraient résolus en dry_run)
        """
        now = datetime.now(timezone.utc)
        min_open_age = timedelta(hours=max_age_hours)

        # 1) Charger les incidents BREACH OPEN avec leur metric_instance_id
        incidents = list(
            self.db.scalars(
                select(Incident)
                .where(
                    Incident.status == "OPEN",
                    Incident.incident_type == IncidentType.BREACH,
                    Incident.metric_instance_id.is_not(None),
                )
                .order_by(Incident.created_at.asc())
                .limit(limit)
            )
        )

        if not incidents:
            return 0

        csrepo = ClientSettingsRepository(self.db)
        resolved_count = 0

        for inc in incidents:
            # Sécurité : si created_at manquant, on skip
            if not inc.created_at:
                continue

            # 2) Vérifier l'âge d'ouverture
            if (now - inc.created_at) < min_open_age:
                continue

            # 3) Récupérer metric_instance_id (garanti présent sur un BREACH)
            metric_instance_id = inc.metric_instance_id

            # 4) Charger seuil staleness du client
            if not inc.client_id:
                continue
            staleness_threshold_sec = csrepo.get_effective_metric_staleness_seconds(inc.client_id)

            # 5) Dernier sample ts (source de vérité)
            last_sample = self.db.scalar(
                select(Sample)
                .where(Sample.metric_instance_id == metric_instance_id)
                .order_by(desc(Sample.ts), desc(Sample.seq))
                .limit(1)
            )

            # Pas de sample = plutôt NO_DATA ; on ne masque pas ça ici
            if not last_sample or not last_sample.ts:
                continue

            sample_ts = last_sample.ts
            if sample_ts.tzinfo is None:
                sample_ts = sample_ts.replace(tzinfo=timezone.utc)
            else:
                sample_ts = sample_ts.astimezone(timezone.utc)

            age_sec = (now - sample_ts).total_seconds()

            # 6) Si stale -> auto-resolve
            if age_sec > float(staleness_threshold_sec):
                reason = (
                    f"Auto-resolved: threshold data stale "
                    f"(last_sample_ts={sample_ts.isoformat()}, "
                    f"age_sec={int(age_sec)}, staleness_threshold_sec={staleness_threshold_sec})."
                )

                if dry_run:
                    resolved_count += 1
                    continue

                # Ajoute la raison dans description
                existing_desc = (inc.description or "").strip()
                inc.description = (existing_desc + "\n\n" + reason).strip()

                inc.status = "RESOLVED"
                inc.resolved_at = now
                inc.updated_at = now

                self.db.flush()
                resolved_count += 1

        return resolved_count