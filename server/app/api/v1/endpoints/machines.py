from __future__ import annotations
"""
server/app/api/v1/endpoints/machines.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Endpoints Machines :
- GET  /machines
- GET  /machines/{machine_id}/detail
- GET  /machines/{machine_id}/metrics/config

Ajouts importants :
- Ajout du champ "status" pour les machines : UP / DOWN (basé sur last_seen).
- Ajout du champ "status" pour les métriques : OK / NO_DATA.
- Route de configuration métriques par machine (metrics/config).

Refacto :
- Utilise maintenant MetricInstance (et non plus Metric) partout côté lecture.
- Utilise ThresholdNew / ThresholdNewRepository pour les seuils.
"""

import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.sql import false
from sqlalchemy.orm import Session, aliased

from app.core.security import api_key_auth
from app.infrastructure.persistence.database.session import get_db

# ——————————————————————————————————————————————
# Nouveau modèle : MetricInstance (remplace Metric)
# ——————————————————————————————————————————————
from app.infrastructure.persistence.database.models.machine import Machine
from app.infrastructure.persistence.database.models.metric_instance import MetricInstance
from app.infrastructure.persistence.database.models.sample import Sample
from app.infrastructure.persistence.database.models.metric_definitions import MetricDefinitions
from app.infrastructure.persistence.database.models.incident import Incident 
from app.infrastructure.persistence.database.models.alert import Alert

# ——————————————————————————————————————————————
# Nouveau modèle de seuil (remplace Threshold)
# ——————————————————————————————————————————————
from app.infrastructure.persistence.database.models.threshold_new import ThresholdNew

from app.api.schemas.machine_detail import (
    MachineDetailResponse,
    MachineOut,
    MetricDetailOut,
    SampleOut,
    ThresholdOut,
)
from app.api.v1.serializers.machine import (
    serialize_machine_summary,
    serialize_machine_detail,
)

# ↓ Repositories adaptés : version MetricInstance + ThresholdNew
from app.infrastructure.persistence.repositories.metric_instances_repository import (
    MetricInstancesRepository,
)
from app.infrastructure.persistence.repositories.metric_definitions_repository import (
    MetricDefinitionsRepository,
)
from app.infrastructure.persistence.repositories.threshold_new_repository import (
    ThresholdNewRepository,
)

from app.presentation.api.deps import get_current_user
from app.presentation.api.schemas.machine_metric import MachineMetricConfig

router = APIRouter(prefix="/machines")

# ------------------ Configuration ------------------
NO_DATA_SECONDS = 120          # Métrique silencieuse → NO_DATA
MACHINE_DOWN_MINUTES = 3       # Machine silencieuse → DOWN


def _humanize_age(age_sec: int) -> str:
    if age_sec < 60:
        return f"{age_sec} seconde" + ("s" if age_sec >= 2 else "")
    minutes = age_sec // 60
    if minutes < 60:
        return f"{minutes} minute" + ("s" if minutes >= 2 else "")
    hours = minutes // 60
    if hours < 24:
        return f"{hours} heure" + ("s" if hours >= 2 else "")
    days = hours // 24
    if days < 7:
        return f"{days} jour" + ("s" if days >= 2 else "")
    weeks = days // 7
    if days < 31:
        return f"{weeks} semaine" + ("s" if weeks >= 2 else "")
    months = days // 30
    if months < 12:
        return f"{months} mois"
    years = days // 365
    return f"{years} an" + ("s" if years >= 2 else "")

# =====================================================================
# GET /machines — liste simple
# =====================================================================
@router.get("")
async def list_machines(
    api_key=Depends(api_key_auth),
    db: Session = Depends(get_db),
) -> list[dict]:
    """
    Liste minimale des machines pour un client :
    - id, hostname, os_type, last_seen
    - status : "UP" ou "DOWN" selon last_seen.
    """

    rows = db.scalars(
        select(Machine)
        .where(Machine.client_id == api_key.client_id)
        .order_by(Machine.registered_at.asc().nullslast(), Machine.hostname.asc())
    ).all()

    # ------------------------------------------------------------
    # OPEN incidents count par machine (1 requête, pas N+1)
    # ------------------------------------------------------------
    open_counts_rows = db.execute(
        select(
            Incident.machine_id,
            func.count(Incident.id).label("open_count"),
        )
        .where(
            Incident.client_id == api_key.client_id,
            Incident.status == "OPEN",
            Incident.machine_id.isnot(None),
        )
        .group_by(Incident.machine_id)
    ).all()

    open_counts: dict[uuid.UUID, int] = {mid: int(c) for (mid, c) in open_counts_rows}

    out: list[dict] = []
    now = datetime.now(timezone.utc)

    for m in rows:
        data = serialize_machine_summary(m)

        # Statut basé sur last_seen
        if not m.last_seen or (now - m.last_seen) > timedelta(minutes=MACHINE_DOWN_MINUTES):
            data["status"] = "DOWN"
        else:
            data["status"] = "UP"

        data["open_incidents_count"] = open_counts.get(m.id, 0)

        out.append(data)

    return out


# =====================================================================
# GET /machines/{machine_id}/detail
# =====================================================================
@router.get("/{machine_id}/detail", response_model=MachineDetailResponse)
async def get_machine_detail(
    machine_id: str,
    api_key=Depends(api_key_auth),
    db: Session = Depends(get_db),
) -> MachineDetailResponse:
    """
    Vue complète utilisée par le Dashboard :
    - Infos machine
    - Status machine UP/DOWN
    - Liste des métriques (MetricInstance) avec :
        * dernier sample
        * seuil "default" éventuel (ThresholdNew)
        * statut métrique : "OK" / "NO_DATA"

    NOTE: on ne renvoie volontairement PAS `type` ni `unit`.
    """

    # 1) Parse UUID
    try:
        mid = machine_id if isinstance(machine_id, uuid.UUID) else uuid.UUID(str(machine_id))
    except Exception:
        raise HTTPException(status_code=404, detail="Machine not found")

    # 2) Multi-tenant via api_key.client_id
    machine: Machine | None = db.get(Machine, mid)
    if not machine or machine.client_id != api_key.client_id:
        raise HTTPException(status_code=404, detail="Machine not found")

    # 3) Machine + status
    machine_dict = serialize_machine_detail(machine)
    now = datetime.now(timezone.utc)
    machine_dict["status"] = (
        "DOWN"
        if (not machine.last_seen or (now - machine.last_seen) > timedelta(minutes=MACHINE_DOWN_MINUTES))
        else "UP"
    )
    open_count = db.scalar(
        select(func.count(Incident.id)).where(
            Incident.client_id == api_key.client_id,
            Incident.machine_id == mid,
            Incident.status == "OPEN",
        )
    ) or 0
    machine_dict["open_incidents_count"] = int(open_count)
    machine_out = MachineOut(**machine_dict)

    # 4) Metrics instances
    metrics: list[MetricInstance] = db.scalars(
        select(MetricInstance)
        .where(MetricInstance.machine_id == mid)
        .order_by(MetricInstance.name_effective)
    ).all()

    metric_instance_ids = [m.id for m in metrics]

    # ------------------------------------------------------------
    # ✅ Flags "breach" / "firing" par métrique (1 requête batch)
    # ------------------------------------------------------------
    firing_metric_ids: set[uuid.UUID] = set()

    if metric_instance_ids:
        firing_rows = db.execute(
            select(Alert.metric_instance_id)
            .where(
                Alert.machine_id == mid,
                Alert.status == "FIRING",
                Alert.metric_instance_id.isnot(None),
                Alert.metric_instance_id.in_(metric_instance_ids),
            )
            .distinct()
        ).all()

        firing_metric_ids = {r[0] for r in firing_rows}

    # 4bis) Charger les définitions associées (batch)
    # definitions_by_id = {definition_id: MetricDefinitions}
    definitions_by_id: dict[uuid.UUID, MetricDefinitions] = {}
    definition_ids = {m.definition_id for m in metrics if m.definition_id is not None}
    if definition_ids:
        defs = db.scalars(
            select(MetricDefinitions).where(MetricDefinitions.id.in_(definition_ids))
        ).all()
        definitions_by_id = {d.id: d for d in defs}

    # 5) Derniers samples (1 par metric_instance) - en 1 requête (pas N+1)
    subq = (
        select(
            Sample.metric_instance_id.label("mid"),
            Sample.ts,
            Sample.seq,
            Sample.value_type,
            Sample.num_value,
            Sample.bool_value,
            Sample.str_value,
            func.row_number().over(
                partition_by=Sample.metric_instance_id,
                order_by=(Sample.ts.desc(), Sample.seq.desc()),
            ).label("rn"),
        )
        .where(Sample.metric_instance_id.in_(metric_instance_ids))
        .subquery()
    )

    rows = db.execute(select(subq).where(subq.c.rn == 1)).all()

    last_samples: dict[uuid.UUID, SampleOut] = {}
    for r in rows:
        age_sec = max(0, int((now - r.ts).total_seconds()))
        last_samples[r.mid] = SampleOut(
            ts=r.ts.isoformat(),
            ts_epoch=int(r.ts.timestamp()),
            age_sec=age_sec,
            age_human=_humanize_age(age_sec),
            value_type=r.value_type,
            num_value=r.num_value,
            bool_value=r.bool_value,
            str_value=r.str_value,
        )


    # 6) Seuils "default"
    default_thresholds: dict[uuid.UUID, ThresholdOut] = {}
    if metric_instance_ids:
        rows = db.scalars(
            select(ThresholdNew)
            .where(
                ThresholdNew.metric_instance_id.in_(metric_instance_ids),
                ThresholdNew.name == "default",
            )
        ).all()
        for t in rows:
            default_thresholds[t.metric_instance_id] = ThresholdOut(
                id=str(t.id),
                name=t.name,
                condition=t.condition,
                severity=t.severity,
                is_active=bool(t.is_active),
                value_num=t.value_num,
                value_bool=t.value_bool,
                value_str=t.value_str,
                consecutive_breaches=t.consecutive_breaches,
                cooldown_sec=t.cooldown_sec,
                min_duration_sec=t.min_duration_sec,
            )

    # 7) Construire la réponse métriques
    metrics_out: list[MetricDetailOut] = []
    for mt in metrics:
        
        last = last_samples.get(mt.id)

        if not last:
            continue
        else:
            ts = datetime.fromisoformat(last.ts.replace("Z", "+00:00"))
            metric_status = "NO_DATA" if (now - ts) > timedelta(seconds=NO_DATA_SECONDS) else "OK"

        # Définition (si reliée)
        d = definitions_by_id.get(mt.definition_id) if mt.definition_id else None
        is_suggested_critical = bool(d.is_suggested_critical) if d else False
        description = d.description if d and d.description else None
        default_condition = d.default_condition if d and d.default_condition else None
        group_name = d.group_name if d else "misc"


        is_firing = (mt.id in firing_metric_ids)

        metrics_out.append(
            MetricDetailOut(
                id=str(mt.id),
                name=mt.name_effective,
                group_name=group_name,
                is_alerting_enabled=bool(mt.is_alerting_enabled),
                needs_threshold=bool(mt.needs_threshold),
                last_sample=last,
                default_threshold=default_thresholds.get(mt.id),
                default_condition=default_condition,
                status=metric_status,
                is_firing=is_firing,
                is_paused=bool(mt.is_paused),
                is_suggested_critical=is_suggested_critical,
                description=description,
            )
        )

    return MachineDetailResponse(machine=machine_out, metrics=metrics_out)


# =====================================================================
# GET /machines/{machine_id}/metrics/config
# =====================================================================
@router.get(
    "/{machine_id}/metrics/config",
    response_model=list[MachineMetricConfig],
)
async def get_machine_metric_config(
    machine_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> list[MachineMetricConfig]:
    """
    Retourne la configuration des métriques pour une machine donnée (MetricInstance) :
    - métadonnées (nom effectif, groupe, vendor, type, description)
    - indicateurs d'onboarding (is_suggested_critical, default_condition)
    - flags de config (is_alerting_enabled, is_paused, needs_threshold)
    - baseline / last_value
    - seuil primaire issu de ThresholdNew (si existe)
    """

    # 1) Vérification existence + multi-tenant
    machine: Machine | None = db.get(Machine, machine_id)
    if (
        not machine
        or not hasattr(current_user, "client_id")
        or machine.client_id != current_user.client_id
    ):
        raise HTTPException(status_code=404, detail="Machine not found")

    # 2) Repositories adaptés
    repo_metric = MetricInstancesRepository(db)
    repo_catalog = MetricDefinitionsRepository(db)
    repo_threshold = ThresholdNewRepository(db)

    # 3) Chargement toutes métriques de la machine
    metrics: list[MetricInstance] = repo_metric.get_by_machine(machine_id)

    results: list[MachineMetricConfig] = []

    for m in metrics:
        # Résolution du catalogue
        catalog = repo_catalog.get_by_name_and_vendor(
            name=m.name_effective,
            vendor=getattr(m, "vendor", "builtin"),
        )

        # Seuil primaire sur MetricInstance
        th: ThresholdNew | None = repo_threshold.get_primary_for_metric(m.id)

        # Fallbacks métadonnées
        group_name = getattr(m, "group_name", None) or (catalog.group_name if catalog else None)
        vendor = getattr(m, "vendor", None) or (catalog.vendor if catalog else "builtin")
        mtype = getattr(m, "type", None) or (catalog.type if catalog else "string")
        description = (
            getattr(m, "description", None)
            or (catalog.description if catalog else None)
        )

        is_suggested_critical = getattr(m, "is_suggested_critical", None)
        if is_suggested_critical is None and catalog is not None:
            is_suggested_critical = bool(catalog.is_suggested_critical)
        if is_suggested_critical is None:
            is_suggested_critical = False

        default_condition = getattr(catalog, "default_condition", None) if catalog else None

        # Construction réponse UI
        results.append(
            MachineMetricConfig(
                id=str(m.id),
                name=m.name_effective,
                group_name=group_name,
                vendor=vendor,
                type=mtype,
                description=description,

                is_suggested_critical=is_suggested_critical,
                default_condition=default_condition,

                is_alerting_enabled=m.is_alerting_enabled,
                is_paused=m.is_paused,
                needs_threshold=m.needs_threshold,
                baseline_value=m.baseline_value,
                last_value=m.last_value,

                threshold_value=(
                    str(th.value_num) if th and th.value_num is not None else None
                ),
                threshold_condition=th.condition if th else None,
                threshold_severity=th.severity if th else None,
            )
        )

    return results
