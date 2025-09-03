from __future__ import annotations
"""server/app/workers/tasks/ingest_tasks.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Ingestion asynchrone + évaluation immédiate de la machine.
"""

from app.infrastructure.persistence.database.session import get_sync_session
from app.infrastructure.persistence.repositories.sample_repository import SampleRepository
from app.workers.celery_app import celery
from app.application.services.evaluation_service import evaluate_machine


def enqueue_samples(*, client_id: str, machine_id: str, ingest_id: str, metrics, sent_at: str | None) -> None:
    """Place un lot d'échantillons en file (idempotence à ajouter plus tard si souhaité)."""
    celery.send_task(
        "tasks.ingest",
        args=[client_id, machine_id, ingest_id, [m.model_dump() for m in metrics], sent_at],
    )


@celery.task(name="tasks.ingest")
def process_samples(client_id: str, machine_id: str, ingest_id: str, metrics_payload: list[dict], sent_at: str | None) -> None:
    """Écrit les samples et déclenche l'évaluation de la machine."""
    with get_sync_session() as session:
        srepo = SampleRepository(session)
        srepo.write_batch(machine_id=machine_id, metrics_payload=metrics_payload, sent_at=sent_at)
    evaluate_machine(machine_id)
