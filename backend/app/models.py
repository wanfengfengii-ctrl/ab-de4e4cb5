"""Pydantic request/response models for the hazard audit API."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class Gate(BaseModel):
    type: str = Field(description="One of: AND OR NOT BUF NAND NOR XOR XNOR")
    inputs: list[str] = Field(default_factory=list)
    delay_min: int = Field(ge=0)
    delay_max: int = Field(ge=0)


class AuditRequest(BaseModel):
    inputs: list[str]
    initial: dict[str, int]
    target: dict[str, int]
    gates: dict[str, Gate]
    monitors: list[str]


class ToggleCount(BaseModel):
    monitor: str
    toggles: int
    necessary: int
    end_value: int


class WitnessBatchEvent(BaseModel):
    gate: str
    value: int
    delay: int = 0


class MonitorValue(BaseModel):
    monitor: str
    value: int


class WitnessStep(BaseModel):
    time: int
    note: str = ""
    batch: list[WitnessBatchEvent]
    delays: dict[str, int] = Field(
        default_factory=dict,
        description="Delay chosen by gate id for transitions scheduled at this time",
    )
    outputs: list[MonitorValue] = Field(default_factory=list)


class Witness(BaseModel):
    monitor: str
    first_violation_time: int
    end_value: int
    toggles: int
    necessary: int
    steps: list[WitnessStep]


class HazardResult(BaseModel):
    safe: bool
    horizon: int
    earliest_violation_time: Optional[int] = None
    witnesses: list[Witness] = Field(default_factory=list)
    toggle_counts: list[ToggleCount] = Field(default_factory=list)
    reachable_states: dict[str, int] = Field(
        default_factory=dict,
        description="Number of distinct reachable states at each time key",
    )
    state_count_total: int = 0
    explored_schedules: int = 0
    timeline: list[WitnessStep] = Field(
        default_factory=list,
        description="Canonical representative timeline used for playback",
    )


class AuditResponse(BaseModel):
    valid: bool
    errors: list[str]
    result: Optional[HazardResult] = None
