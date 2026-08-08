"""Serializable data contracts for the controlled evolution loop."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class Diagnosis:
    code: str
    evidence: str
    confidence: float


@dataclass(frozen=True)
class BadCase:
    case_id: str
    task_id: str
    subtask_index: int
    instruction: str
    reward: float
    termination_reason: str
    skills: list[str] = field(default_factory=list)
    memory_snapshot: str = ""
    tool_call_count: int = 0
    search_call_count: int = 0
    max_identical_calls: int = 0
    tool_error_count: int = 0
    paid: bool = False
    diagnoses: list[Diagnosis] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CandidateStrategy:
    strategy_id: str
    diagnosis_code: str
    mutation_kind: Literal["config", "policy"]
    component: str
    changes: dict[str, Any]
    rationale: str
    risk: str
    supporting_case_ids: list[str] = field(default_factory=list)
    status: Literal["pending", "promoted", "rejected"] = "pending"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Scorecard:
    sample_count: int
    avg_reward: float
    bad_case_success_rate: float
    regression_success_rate: float
    invalid_tool_rate: float
    loop_rate: float
    avg_tokens: float | None = None
    avg_duration_seconds: float | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Scorecard":
        return cls(**value)


@dataclass(frozen=True)
class GateDecision:
    promoted: bool
    reasons: list[str]

