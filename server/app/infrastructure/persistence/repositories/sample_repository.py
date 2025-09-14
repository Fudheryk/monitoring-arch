from __future__ import annotations
"""server/app/infrastructure/persistence/repositories/sample_repository.py
~~~~~~~~~~~~~~~~~~~~~~~~
Repo samples (write_batch).
"""
from datetime import datetime, timezone
from sqlalchemy import insert
from sqlalchemy.orm import Session
from app.infrastructure.persistence.database.models.metric import Metric
from app.infrastructure.persistence.database.models.sample import Sample




class SampleRepository:
    def __init__(self, session: Session):
        self.s = session

    def write_batch(self, *, machine_id: str, metrics_payload: list[dict], sent_at: str | None) -> None:
        ts = datetime.now(timezone.utc) if sent_at is None else datetime.fromisoformat(sent_at.replace("Z","+00:00"))
        metric_ids = {m.name: m.id for m in self.s.query(Metric).filter(Metric.machine_id == machine_id).all()}
        rows: list[dict] = []
        for i, m in enumerate(metrics_payload):
            mid = metric_ids.get(m["name"])
            if not mid:
                continue
            vtype = m["type"]
            base = {"metric_id": mid, "ts": ts, "seq": i, "value_type": vtype}
            if vtype == "numeric":
                base["num_value"] = float(m["value"])
            elif vtype == "bool":
                base["bool_value"] = bool(m["value"])
            else:
                base["str_value"] = str(m["value"])
            rows.append(base)
        if rows:
            self.s.execute(insert(Sample), rows)
            self.s.commit()
