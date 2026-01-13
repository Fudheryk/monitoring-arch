# server/app/scripts/run_evaluations_for_machine.py
from __future__ import annotations
import sys
from sqlalchemy import select

from app.application.services.metric_freshness_service import check_metrics_no_data
from app.application.services.evaluation_service import evaluate_machine
from app.infrastructure.persistence.database.session import open_session
from app.infrastructure.persistence.database.models.machine import Machine


def main(hostname: str) -> None:
    print(f"[run_evaluations] start for hostname={hostname}")

    # 1) NO-DATA (machine DOWN/UP, Metric no data, etc.)
    stale = check_metrics_no_data()
    print(f"[run_evaluations] check_metrics_no_data() → {stale} stale metrics")

    # 2) Seuils pour la machine cible
    with open_session() as s:
        machine = s.scalar(select(Machine).where(Machine.hostname == hostname))
        if not machine:
            print(f"[run_evaluations] machine '{hostname}' not found")
            return

        alerts = evaluate_machine(machine.id)
        print(f"[run_evaluations] evaluate_machine({machine.id}) → {alerts} alerts processed")

    print("[run_evaluations] done")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m app.scripts.run_evaluations_for_machine <hostname>")
        raise SystemExit(1)
    main(sys.argv[1])
