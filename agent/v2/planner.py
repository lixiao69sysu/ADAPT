"""Model-authored, advisory plans for open-world decisions."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from vita.data_model.message import SystemMessage, UserMessage
from vita.utils.llm_utils import generate

from agent.v2.observation import ObservationStore
from agent.v2.workspace import DecisionWorkspace


class CandidateAssessment(BaseModel):
    candidate_id: str
    assessment: str


class MaterialUncertainty(BaseModel):
    dimension: str
    question: str
    counterfactual_choices: list[str] = Field(default_factory=list)
    expected_decision_change: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ActionPlan(BaseModel):
    goal: str = ""
    explicit_constraints: list[str] = Field(default_factory=list)
    relevant_preferences: list[str] = Field(default_factory=list)
    material_unknowns: list[str] = Field(default_factory=list)
    candidate_assessment: list[CandidateAssessment] = Field(default_factory=list)
    proposed_next_action: str = "fall_back_to_stock_policy"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    material_uncertainty: MaterialUncertainty | None = None
    warnings: list[str] = Field(default_factory=list)

    def render(self, include_voi: bool = False) -> str:
        payload = self.model_dump(mode="json", exclude_none=not include_voi)
        if not include_voi:
            payload.pop("material_uncertainty", None)
        return json.dumps(payload, ensure_ascii=False, indent=2)


_PLANNER_PROMPT = """You are an advisory planner for a consumer agent.
Use only the supplied workspace. Treat current user instructions and current-turn
corrections as higher priority than historical beliefs. Historical beliefs are
soft evidence, never hard constraints. Do not invent IDs, inventory, dates,
addresses, order state, or tools. Return one JSON object matching this schema:
{goal: string, explicit_constraints: string[], relevant_preferences: string[],
material_unknowns: string[], candidate_assessment: [{candidate_id, assessment}],
proposed_next_action: string, confidence: number 0..1,
material_uncertainty: null or {dimension, question, counterfactual_choices: string[],
expected_decision_change, confidence}}.
A material uncertainty is valid only if different plausible answers would change
the selected candidate or operation. The executor owns the final decision."""


class ModelPlanner:
    """Generate and ground a plan without executing any tool."""

    def plan(
        self,
        workspace: DecisionWorkspace,
        *,
        model: str | None,
        llm_args: dict[str, Any] | None,
        observations: ObservationStore,
    ) -> ActionPlan:
        if model is None:
            return ActionPlan(warnings=["planner model is not configured"])
        args = deepcopy(llm_args or {})
        args.pop("tools", None)
        response = generate(
            model=model,
            messages=[
                SystemMessage(role="system", content=_PLANNER_PROMPT),
                UserMessage(role="user", content=workspace.render()),
            ],
            **args,
        )
        content = str(getattr(response, "content", "") or "")
        try:
            plan = ActionPlan.model_validate(_parse_json(content))
        except (ValueError, ValidationError, json.JSONDecodeError) as error:
            return ActionPlan(warnings=[f"planner output rejected: {error}"])

        grounded: list[CandidateAssessment] = []
        for assessment in plan.candidate_assessment:
            if observations.contains(assessment.candidate_id):
                grounded.append(assessment)
            else:
                plan.warnings.append(
                    f"removed unobserved candidate id: {assessment.candidate_id}"
                )
        plan.candidate_assessment = grounded
        if plan.confidence < 0.35:
            plan.proposed_next_action = "fall_back_to_stock_policy"
        return plan


def _parse_json(content: str) -> dict[str, Any]:
    stripped = content.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1)
    if not stripped.startswith("{"):
        start, end = stripped.find("{"), stripped.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("planner did not return a JSON object")
        stripped = stripped[start : end + 1]
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise TypeError("planner JSON must be an object")
    return parsed
