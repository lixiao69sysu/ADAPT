"""A bounded, advisory decision workspace for the policy model."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field
from vita.data_model.message import UserMessage

from agent.v2.memory import PreferenceBelief
from agent.v2.observation import CandidateObservation, ObservationStore


class DecisionWorkspace(BaseModel):
    current_instruction: str = ""
    explicit_constraints: list[str] = Field(default_factory=list)
    current_turn_corrections: list[str] = Field(default_factory=list)
    relevant_beliefs: list[PreferenceBelief] = Field(default_factory=list)
    candidate_evidence: list[CandidateObservation] = Field(default_factory=list)
    candidate_differences: dict[str, list[str]] = Field(default_factory=dict)
    unresolved_uncertainties: list[str] = Field(default_factory=list)
    available_operations: list[str] = Field(default_factory=list)
    recent_operation_states: list[dict[str, Any]] = Field(default_factory=list)

    def render(self, char_budget: int = 7000) -> str:
        payload = self.model_dump(mode="json", exclude_none=True)
        rendered = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        if len(rendered) <= char_budget:
            return rendered
        compact = {
            "current_instruction": self.current_instruction,
            "explicit_constraints": self.explicit_constraints[-8:],
            "current_turn_corrections": self.current_turn_corrections[-3:],
            "relevant_beliefs": [
                item.model_dump(mode="json") for item in self.relevant_beliefs[:4]
            ],
            "candidate_evidence": [
                item.model_dump(mode="json") for item in self.candidate_evidence[-8:]
            ],
            "available_operations": self.available_operations,
            "recent_operation_states": self.recent_operation_states[-5:],
        }
        return json.dumps(compact, ensure_ascii=False, indent=2, default=str)[:char_budget]


def build_workspace(
    *,
    instruction: str,
    messages: list[Any],
    observation_store: ObservationStore,
    beliefs: list[PreferenceBelief],
    tools: list[Any],
    operation_states: list[dict[str, Any]],
    explicit_constraints: list[str] | None = None,
) -> DecisionWorkspace:
    user_turns = [
        str(message.content)
        for message in messages
        if isinstance(message, UserMessage) and message.content
    ]
    corrections = user_turns[-2:] if len(user_turns) > 1 else []
    latest = list(observation_store._latest.values())
    differences: dict[str, list[str]] = {}
    if len(latest) > 1:
        keys = set().union(*(item.observed_fields.keys() for item in latest))
        for key in sorted(keys):
            values = {
                json.dumps(item.observed_fields.get(key), ensure_ascii=False, default=str)
                for item in latest
                if key in item.observed_fields
            }
            if len(values) > 1:
                differences[str(key)] = sorted(values)[:8]
    return DecisionWorkspace(
        current_instruction=instruction,
        explicit_constraints=list(explicit_constraints or []),
        current_turn_corrections=corrections,
        relevant_beliefs=beliefs,
        candidate_evidence=latest[-20:],
        candidate_differences=differences,
        available_operations=[str(getattr(tool, "name", tool)) for tool in tools],
        recent_operation_states=operation_states[-8:],
    )
