"""Bounded online procedural learning from agent-visible trajectory events.

This module never edits source code or model weights.  It compiles repeated,
typed runtime failures into auditable policies that become active only in a
later subtask for the same user instance.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CapabilityTarget(str, Enum):
    MISSING_INFORMATION_DETECTION = "missing_information_detection"
    PREFERENCE_TO_CANDIDATE_GROUNDING = "preference_to_candidate_grounding"
    CANDIDATE_TO_ACTION_EXECUTION = "candidate_to_action_execution"
    LONG_HORIZON_CONSISTENCY = "long_horizon_consistency"


class PolicyEffect(str, Enum):
    LIMIT_REPEATED_SEARCH = "limit_repeated_search"
    FORCE_CREATE_AFTER_COMPLIANT_CANDIDATE = (
        "force_create_after_compliant_candidate"
    )
    REQUIRE_PAYMENT_COMPLETION = "require_payment_completion"
    FORBID_REDUNDANT_CANDIDATE_QUESTION = (
        "forbid_redundant_candidate_question"
    )
    FORBID_QUESTION_WITH_TOOL = "forbid_question_with_tool"
    REQUIRE_MAX_PREFERENCE_COVERAGE = "require_max_preference_coverage"


@dataclass(frozen=True)
class _RuleTemplate:
    capability_target: CapabilityTarget
    failure_cluster: str
    proposed_change: str
    effect: PolicyEffect
    evidence_threshold: int


_RULE_TEMPLATES = {
    "repeat_search": _RuleTemplate(
        CapabilityTarget.LONG_HORIZON_CONSISTENCY,
        "normalized search is repeated after usable observations",
        "reduce the same search-family budget for later related subtasks",
        PolicyEffect.LIMIT_REPEATED_SEARCH,
        2,
    ),
    "missed_write": _RuleTemplate(
        CapabilityTarget.CANDIDATE_TO_ACTION_EXECUTION,
        "authorized task ends despite an executable compliant candidate",
        "transition deterministically from compliant candidate to create",
        PolicyEffect.FORCE_CREATE_AFTER_COMPLIANT_CANDIDATE,
        1,
    ),
    "unpaid_order": _RuleTemplate(
        CapabilityTarget.CANDIDATE_TO_ACTION_EXECUTION,
        "created order remains unpaid without resolving authorization",
        "require an explicit payment completion check after create",
        PolicyEffect.REQUIRE_PAYMENT_COMPLETION,
        1,
    ),
    "candidate_choice_reask": _RuleTemplate(
        CapabilityTarget.MISSING_INFORMATION_DETECTION,
        "authorized task asks the candidate-selection question again",
        "forbid candidate reconfirmation after an executable candidate exists",
        PolicyEffect.FORBID_REDUNDANT_CANDIDATE_QUESTION,
        1,
    ),
    "mixed_question_tool": _RuleTemplate(
        CapabilityTarget.CANDIDATE_TO_ACTION_EXECUTION,
        "assistant combines a user question with a tool proposal",
        "require a single question or a single action, never both",
        PolicyEffect.FORBID_QUESTION_WITH_TOOL,
        1,
    ),
    "preference_undercoverage": _RuleTemplate(
        CapabilityTarget.PREFERENCE_TO_CANDIDATE_GROUNDING,
        "proposed candidate covers fewer observable preference atoms than available alternatives",
        "restrict later related writes to the maximum observable preference-coverage set",
        PolicyEffect.REQUIRE_MAX_PREFERENCE_COVERAGE,
        1,
    ),
}

_ALLOWED_DOMAINS = {"delivery", "instore", "ota", "general"}
_ALLOWED_FACETS = {
    "hotel", "train", "flight", "attraction", "taxi", "travel",
    "beverage", "restaurant", "retail", "wellness", "entertainment",
    "service", "general",
}


def _bounded_scope(value: str, allowed: set[str]) -> str:
    normalized = (value or "general").strip().lower()
    return normalized if normalized in allowed else "general"


@dataclass(frozen=True)
class RuntimePolicyRule:
    capability_target: str
    domain: str
    facet: str
    failure_class: str
    failure_cluster: str
    proposed_change: str
    effect: str
    evidence_count: int
    confidence: float
    learned_at_subtask: int
    active_from_subtask: int
    forbidden_specificity: tuple[str, ...] = (
        "user_id",
        "product_id",
        "task_id",
        "target marker",
        "rubric text",
    )

    @property
    def key(self) -> tuple[str, str, str]:
        return self.domain, self.facet, self.failure_class


@dataclass(frozen=True)
class CompiledRuntimePolicy:
    max_searches_per_family: int = 2
    force_decision_after_candidates: bool = False
    require_payment_completion_check: bool = False
    forbid_redundant_candidate_question: bool = False
    forbid_question_with_tool: bool = False
    require_max_preference_coverage: bool = False
    active_capabilities: tuple[str, ...] = ()


class RuntimePolicyStore:
    """Per-user policy state learned from a closed observable event vocabulary."""

    def __init__(self, user_id: str | None = None) -> None:
        self.user_id = user_id or ""
        self.subtask_index = 0
        self._rules: dict[tuple[str, str, str], RuntimePolicyRule] = {}

    def begin_subtask(self, user_id: str | None = None) -> None:
        bound_user = user_id or ""
        if bound_user != self.user_id:
            self.reset(bound_user)
        self.subtask_index += 1

    def observe(self, domain: str, facet: str, failure_class: str) -> RuntimePolicyRule | None:
        """Learn only from a typed failure; arbitrary trace payload is not accepted."""
        template = _RULE_TEMPLATES.get(failure_class)
        if template is None:
            return None
        key = (
            _bounded_scope(domain, _ALLOWED_DOMAINS),
            _bounded_scope(facet, _ALLOWED_FACETS),
            failure_class,
        )
        previous = self._rules.get(key)
        evidence_count = 1 if previous is None else previous.evidence_count + 1
        learned_at = self.subtask_index
        active_from = (
            learned_at + 1
            if previous is None
            else min(previous.active_from_subtask, learned_at + 1)
        )
        confidence = min(0.95, 0.55 + 0.1 * evidence_count)
        rule = RuntimePolicyRule(
            capability_target=template.capability_target.value,
            domain=key[0],
            facet=key[1],
            failure_class=failure_class,
            failure_cluster=template.failure_cluster,
            proposed_change=template.proposed_change,
            effect=template.effect.value,
            evidence_count=evidence_count,
            confidence=confidence,
            learned_at_subtask=learned_at,
            active_from_subtask=active_from,
        )
        self._rules[key] = rule
        return rule

    def policy(self, domain: str, facet: str) -> CompiledRuntimePolicy:
        active = [
            rule
            for rule in self._rules.values()
            if rule.domain in {domain, "general"}
            and rule.facet in {facet, "general"}
            and self.subtask_index >= rule.active_from_subtask
            and rule.evidence_count
            >= _RULE_TEMPLATES[rule.failure_class].evidence_threshold
        ]
        effects = {rule.effect for rule in active}
        capabilities = tuple(sorted({rule.capability_target for rule in active}))
        return CompiledRuntimePolicy(
            max_searches_per_family=(
                1
                if PolicyEffect.LIMIT_REPEATED_SEARCH.value in effects
                else 2
            ),
            force_decision_after_candidates=(
                PolicyEffect.FORCE_CREATE_AFTER_COMPLIANT_CANDIDATE.value
                in effects
            ),
            require_payment_completion_check=(
                PolicyEffect.REQUIRE_PAYMENT_COMPLETION.value in effects
            ),
            forbid_redundant_candidate_question=(
                PolicyEffect.FORBID_REDUNDANT_CANDIDATE_QUESTION.value
                in effects
            ),
            forbid_question_with_tool=(
                PolicyEffect.FORBID_QUESTION_WITH_TOOL.value in effects
            ),
            require_max_preference_coverage=(
                PolicyEffect.REQUIRE_MAX_PREFERENCE_COVERAGE.value in effects
            ),
            active_capabilities=capabilities,
        )

    def rules(self) -> list[RuntimePolicyRule]:
        return sorted(self._rules.values(), key=lambda rule: rule.key)

    def reset(self, user_id: str | None = None) -> None:
        self.user_id = user_id or ""
        self.subtask_index = 0
        self._rules.clear()


class RuntimePolicyAdapter:
    """Apply a compiled policy to deterministic runtime controls."""

    @staticmethod
    def apply(policy: CompiledRuntimePolicy, runtime, ledger) -> None:
        ledger.max_searches_per_family = policy.max_searches_per_family
        runtime.force_decision_after_candidates = (
            policy.force_decision_after_candidates
        )
        runtime.require_payment_completion_check = (
            policy.require_payment_completion_check
        )
        runtime.forbid_redundant_candidate_question = (
            policy.forbid_redundant_candidate_question
        )
        runtime.forbid_question_with_tool = policy.forbid_question_with_tool
        ledger.require_max_preference_coverage = (
            policy.require_max_preference_coverage
        )
