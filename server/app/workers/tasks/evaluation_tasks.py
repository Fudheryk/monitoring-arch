from __future__ import annotations
"""server/app/workers/tasks/evaluation_tasks.py
~~~~~~~~~~~~~~~~~~~~~~~~
Évaluation périodique des machines.

Points clés :
- Itération **en streaming** sur les IDs (pas de chargement de tous les objets en mémoire)
- Logs explicites (nombre d'évaluations, erreurs par machine)
- Tolérance aux erreurs : une machine en erreur n'interrompt pas tout le batch
"""

from sqlalchemy import select
from celery.utils.log import get_task_logger

from app.workers.celery_app import celery
from app.infrastructure.persistence.database.session import open_session
from app.infrastructure.persistence.database.models.machine import Machine
from app.application.services.evaluation_service import evaluate_machine

logger = get_task_logger(__name__)


@celery.task(name="tasks.evaluate")
def evaluate_all() -> int:
    """
    Évalue toutes les machines et retourne le *nombre d'évaluations effectuées*.

    Remarques :
    - On ne récupère que les colonnes nécessaires (ici, `Machine.id`) pour limiter
      la mémoire et la latence.
    - `yield_per` (via `execution_options`) permet un fetch par paquets côté SQLAlchemy.
    - En cas d'exception sur UNE machine, on loggue et on continue (pas de stop global).
    """
    total = 0

    # Ouverture de session via le context manager (fermée automatiquement)
    with open_session() as session:
        # On ne sélectionne que les IDs (plus léger que de matérialiser des objets)
        stmt = select(Machine.id).execution_options(yield_per=500)  # streaming par paquets

        # `session.execute(...).scalars()` itère directement sur les UUIDs
        for machine_id in session.execute(stmt).scalars():
            try:
                # `evaluate_machine` accepte UUID/str → str ici pour rester explicite
                total += int(bool(evaluate_machine(str(machine_id))))
            except Exception as exc:  # tolérance aux erreurs par machine
                logger.exception("Échec de l'évaluation pour la machine %s: %s", machine_id, exc)

    logger.info("Évaluation terminée: %d machine(s) évaluée(s).", total)
    return total
