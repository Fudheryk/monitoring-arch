from __future__ import annotations
"""server/app/application/services/baseline_service.py
~~~~~~~~~~~~~~~~~~~~~~~~
Initialisation "baseline" au premier passage :
- crée les métriques manquantes
- initialise baseline_value si absente
- crée des thresholds par défaut si absents (idempotent)
"""

import uuid
from typing import Iterable

from sqlalchemy import select, func

from app.infrastructure.persistence.database.session import get_sync_session
from app.infrastructure.persistence.repositories.metric_repository import MetricRepository
from app.infrastructure.persistence.database.models.threshold import Threshold

# Seuils par défaut par nom de métrique.
# Format : { metric_name: (condition, value_num, severity) }
# NB: on utilise 'gt' (cohérent avec ta migration 0002) ;
#     si ton évaluateur comprend aussi '>', pas de souci.
DEFAULT_THRESHOLDS: dict[str, tuple[str, float, str]] = {
    "cpu_load": ("gt", 3.0, "warning"),
    # Exemple(s) à activer si besoin :
    # "memory_usage": ("gt", 0.80, "warning"),
    # "disk_usage":   ("gt", 0.90, "warning"),
}


def _coerce_str(val) -> str | None:
    """Convertit prudemment une valeur en str (pour baseline_value)."""
    if val is None:
        return None
    try:
        return str(val)
    except Exception:
        return None


def init_if_first_seen(machine, metrics_inputs: Iterable) -> None:
    """
    Pour chaque métrique reçue :
      - crée la Metric si absente (via MetricRepository)
      - initialise baseline_value si elle est encore vide
      - ajoute un Threshold par défaut si configuré et absent
    Cette fonction est idempotente.
    """
    with get_sync_session() as session:
        mrepo = MetricRepository(session)

        for mi in metrics_inputs:
            # On attend des champs au format du schéma d'ingest pydantic : name/type/unit/value
            name = getattr(mi, "name", None)
            mtype = getattr(mi, "type", None)
            unit = getattr(mi, "unit", None)
            value = getattr(mi, "value", None)

            if not name:
                # métrique sans nom -> on ignore silencieusement
                continue

            # 1) Créer/retourner la Metric
            metric = mrepo.get_or_create(machine.id, name, mtype, unit)
            # flush pas nécessaire si get_or_create fait déjà l'insert, mais inoffensif
            session.flush()

            # 2) Baseline si encore vide
            if getattr(metric, "baseline_value", None) in (None, ""):
                bv = _coerce_str(value)
                if bv is not None:
                    metric.baseline_value = bv

            # 3) Threshold par défaut si configuré pour cette métrique
            if name in DEFAULT_THRESHOLDS:
                cond, value_num, severity = DEFAULT_THRESHOLDS[name]

                # Existe-t-il déjà un threshold pour CETTE metric ?
                already = session.scalar(
                    select(func.count())
                    .select_from(Threshold)
                    .where(Threshold.metric_id == metric.id)
                )
                if not already:
                    session.add(
                        Threshold(
                            id=uuid.uuid4(),
                            metric_id=metric.id,
                            name=f"default:{name}:{cond}{value_num}",
                            condition=cond,              # 'gt', 'lt', ...
                            value_num=value_num,         # on utilise la colonne num
                            severity=severity,           # 'warning' par défaut
                            is_active=True,
                            consecutive_breaches=1,
                            cooldown_sec=0,
                            min_duration_sec=0,
                        )
                    )

        session.commit()
