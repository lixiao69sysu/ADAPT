"""Observable failure attribution for ADAPT's execution boundary.

The classifier consumes only the public decision surface and validation
messages.  It never uses evaluator output or hidden task annotations.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FailureOwner(str, Enum):
    FRAMEWORK = "framework"
    MODEL_POLICY = "model_policy"
    VALIDATOR = "validator"
    ENVIRONMENT = "environment"
    HARNESS = "harness"


@dataclass(frozen=True)
class FailureAttribution:
    owner: FailureOwner
    stage: str
    reason: str


_VALIDATOR_MARKERS = (
    "candidate",
    "constraint",
    "inventory",
    "stock",
    "id provenance",
    "expects ",
    "conflicts",
    "not observed",
    "not a current candidate",
    "preference",
)


def attribute_preflight_failure(
    problems: list[str] | tuple[str, ...],
    *,
    allowed_tool_count: int,
    proposed_call_count: int,
) -> FailureAttribution:
    """Locate the earliest observable layer that prevented execution."""

    normalized = " | ".join(str(problem).casefold() for problem in problems)
    if allowed_tool_count == 0:
        return FailureAttribution(
            FailureOwner.FRAMEWORK,
            "decision_surface",
            "no executable tool was exposed for the current phase",
        )
    if proposed_call_count == 0:
        return FailureAttribution(
            FailureOwner.MODEL_POLICY,
            "proposal",
            "the model emitted no tool call despite an executable decision surface",
        )
    if "not exposed" in normalized or "required argument" in normalized or "type" in normalized:
        return FailureAttribution(
            FailureOwner.MODEL_POLICY,
            "proposal",
            "the model ignored the exposed tool contract",
        )
    if any(marker in normalized for marker in _VALIDATOR_MARKERS):
        return FailureAttribution(
            FailureOwner.VALIDATOR,
            "preflight",
            "an exposed proposal failed deterministic semantic validation",
        )
    return FailureAttribution(
        FailureOwner.MODEL_POLICY,
        "proposal",
        "the proposed action violated an execution invariant",
    )
