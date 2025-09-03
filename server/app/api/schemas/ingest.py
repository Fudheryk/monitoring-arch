from __future__ import annotations
"""server/app/api/schemas/ingest.py
~~~~~~~~~~~~~~~~~~~~~~~~
Schémas ingestion.
"""
from typing import Any, Literal
from pydantic import BaseModel, Field

from typing import Any, Literal
from pydantic import BaseModel, Field

MetricType = Literal["bool", "numeric", "string"]

class MachineInfo(BaseModel):
    hostname: str
    os: str | None = None
    tags: dict[str, Any] = Field(default_factory=dict)

class MetricInput(BaseModel):
    name: str
    type: MetricType
    value: bool | float | str
    unit: str | None = None
    alert_enabled: bool | None = None

class IngestRequest(BaseModel):
    machine: MachineInfo
    metrics: list[MetricInput]
    sent_at: str | None = None
