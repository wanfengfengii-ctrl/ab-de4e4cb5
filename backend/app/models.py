"""Request/response schemas for the hazard audit API."""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

GateType = Literal["AND", "OR", "NOT", "NAND", "NOR", "XOR", "XNOR", "BUF"]


class Gate(BaseModel):
    type: GateType
    inputs: List[str] = Field(default_factory=list)


class DelayRange(BaseModel):
    # Inclusive integer delay interval in abstract time units, >= 0.
    min: int = Field(ge=0)
    max: int = Field(ge=0)


class AuditRequest(BaseModel):
    initial: Dict[str, int]
    target: Dict[str, int]
    gates: Dict[str, Gate]
    delays: Dict[str, DelayRange]
    monitors: List[str]

    def normalized(self) -> "AuditRequest":
        """Coerce truthy/0 bit values and drop duplicate monitor entries."""
        def bits(d: Dict[str, int]) -> Dict[str, int]:
            return {k: 1 if v else 0 for k, v in d.items()}

        return AuditRequest(
            initial=bits(self.initial),
            target=bits(self.target),
            gates=self.gates,
            delays=self.delays,
            monitors=list(dict.fromkeys(self.monitors)),
        )


class WitnessStep(BaseModel):
    time: int
    events: List[str]                       # "g3: 0->1 (d=2)"
    state: Dict[str, int]                   # full signal vector after batch
    active_events: List[str]                # pending inertial events


class WitnessTrace(BaseModel):
    transitions: List[WitnessStep]
    explanation: str


class ViolationInfo(BaseModel):
    time: int
    gate: str
    monitor: str


class TimelineLayer(BaseModel):
    time: int
    reachable_states: int


class AuditResponse(BaseModel):
    safe: bool
    horizon: int
    timeline: List[TimelineLayer]
    total_states: int
    initial_state: Dict[str, int]   # inputs + gate outputs before the switch
    target_inputs: Dict[str, int]
    witness: WitnessTrace           # canonical execution used for replay
    violation: Optional[ViolationInfo] = None


class ErrorResponse(BaseModel):
    error: str
    code: str
    location: Optional[str] = None
