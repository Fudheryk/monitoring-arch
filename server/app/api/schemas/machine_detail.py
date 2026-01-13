# server/app/api/schemas/machine_detail.py

from typing import Optional
from pydantic import BaseModel


class SampleOut(BaseModel):
        ts: str
        ts_epoch: int
        age_sec: int | None = None
        age_human: str | None = None
        value_type: str
        num_value: float | None = None
        bool_value: bool | None = None
        str_value: str | None = None


class ThresholdOut(BaseModel):
    id: str
    name: str
    condition: str
    severity: str
    is_active: bool
    value_num: float | None = None
    value_bool: bool | None = None
    value_str: str | None = None
    consecutive_breaches: int
    cooldown_sec: int
    min_duration_sec: int


class MetricDetailOut(BaseModel):
    id: str
    name: str
    # type: str
    # unit: Optional[str] = None
    group_name: str | None = None
    is_alerting_enabled: bool
    needs_threshold: bool
    last_sample: Optional[SampleOut] = None
    default_condition: Optional[str] = None
    default_threshold: Optional[ThresholdOut] = None
    status: str
    is_firing: bool = False
    is_paused: bool
    is_suggested_critical: bool = False
    description: Optional[str] = None


class MachineOut(BaseModel):
    id: str
    hostname: str
    os_type: Optional[str] = None
    os_version: Optional[str] = None
    last_seen: Optional[str] = None
    is_active: bool
    registered_at: str
    unregistered_at: Optional[str] = None
    status: str  # UP / DOWN


class MachineDetailResponse(BaseModel):
    machine: MachineOut
    metrics: list[MetricDetailOut]
