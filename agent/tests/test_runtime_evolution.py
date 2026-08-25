"""Tests for frozen-code, per-user runtime procedural learning."""

from __future__ import annotations

from dataclasses import asdict

import pytest

from agent.decision import Candidate, CandidateLedger, TaskSpec
from agent.runtime import (
    QuestionGate,
    RuntimePhase,
    RuntimePolicyAdapter,
    RuntimePolicyStore,
    TaskRuntime,
    ToolErrorLedger,
    TrajectoryEvidenceSource,
)


STATE_FAILURE = TrajectoryEvidenceSource.EXPLICIT_STATE_FAILURE


def test_rule_is_learned_now_but_activates_on_later_subtask_only():
    store = RuntimePolicyStore("user-a")
    store.begin_subtask("user-a")
    rule = store.observe(
        "delivery", "retail", "missed_write", evidence_source=STATE_FAILURE
    )
    assert rule is not None
    assert rule.active_from_subtask == 2
    assert not store.policy("delivery", "retail").force_decision_after_candidates

    store.begin_subtask("user-a")
    assert store.policy("delivery", "retail").force_decision_after_candidates


def test_repeat_search_requires_repeated_evidence_and_changes_budget():
    store = RuntimePolicyStore("user-a")
    store.begin_subtask("user-a")
    store.observe(
        "delivery", "beverage", "repeat_search", evidence_source=STATE_FAILURE
    )
    store.begin_subtask("user-a")
    assert store.policy("delivery", "beverage").max_searches_per_family == 2
    store.observe(
        "delivery", "beverage", "repeat_search", evidence_source=STATE_FAILURE
    )
    store.begin_subtask("user-a")
    assert store.policy("delivery", "beverage").max_searches_per_family == 1


def test_policy_is_facet_scoped():
    store = RuntimePolicyStore("user-a")
    store.begin_subtask("user-a")
    store.observe(
        "delivery", "beverage", "missed_write", evidence_source=STATE_FAILURE
    )
    store.begin_subtask("user-a")
    assert store.policy("delivery", "beverage").force_decision_after_candidates
    assert not store.policy("delivery", "retail").force_decision_after_candidates
    assert not store.policy("ota", "hotel").force_decision_after_candidates


def test_switching_user_clears_all_learned_rules():
    store = RuntimePolicyStore("user-a")
    store.begin_subtask("user-a")
    store.observe(
        "delivery", "retail", "missed_write", evidence_source=STATE_FAILURE
    )
    store.begin_subtask("user-a")
    assert store.rules()

    store.begin_subtask("user-b")
    assert store.user_id == "user-b"
    assert store.subtask_index == 1
    assert store.rules() == []
    assert not store.policy("delivery", "retail").force_decision_after_candidates


def test_rule_declares_capability_and_contains_no_case_specific_evidence():
    store = RuntimePolicyStore("user-a")
    store.begin_subtask("user-a")
    rule = store.observe(
        "delivery", "retail", "missed_write", evidence_source=STATE_FAILURE
    )
    payload = asdict(rule)
    rendered = repr(payload)
    assert payload["capability_target"] == "candidate_to_action_execution"
    assert payload["failure_cluster"]
    assert payload["proposed_change"]
    assert "user-a" not in rendered
    assert "P99999" not in rendered
    assert "target_product_ids" not in rendered


def test_unknown_case_specific_scope_is_collapsed_to_general():
    store = RuntimePolicyStore("user-a")
    store.begin_subtask("user-a")
    rule = store.observe(
        "U999999", "P99999", "missed_write", evidence_source=STATE_FAILURE
    )
    assert rule.domain == "general"
    assert rule.facet == "general"
    assert "U999999" not in repr(asdict(rule))
    assert "P99999" not in repr(asdict(rule))


def test_hidden_evaluator_payload_cannot_be_passed_to_learner():
    store = RuntimePolicyStore("user-a")
    store.begin_subtask("user-a")
    with pytest.raises(TypeError):
        store.observe(
            "delivery",
            "retail",
            "missed_write",
            evidence_source=STATE_FAILURE,
            reward=1.0,
        )
    assert store.observe(
        "delivery", "retail", "rubric", evidence_source=STATE_FAILURE
    ) is None
    assert store.rules() == []


def test_policy_adapter_changes_deterministic_candidate_to_action_transition():
    store = RuntimePolicyStore("user-a")
    store.begin_subtask("user-a")
    store.observe(
        "delivery", "retail", "missed_write", evidence_source=STATE_FAILURE
    )
    store.begin_subtask("user-a")

    runtime = TaskRuntime.begin(TaskSpec.compile("推荐一款鼠标"))
    runtime.authorization.create_authorized = True
    runtime.authorization.candidate_choice_authorized = False
    ledger = CandidateLedger()
    RuntimePolicyAdapter.apply(store.policy("delivery", "retail"), runtime, ledger)
    runtime.observe_candidates(2, execution_ready=True)
    assert runtime.phase == RuntimePhase.READY_TO_CREATE


def test_framework_self_diagnostics_do_not_create_hard_question_policy():
    store = RuntimePolicyStore("user-a")
    store.begin_subtask("user-a")
    assert store.observe(
        "delivery",
        "retail",
        "candidate_choice_reask",
        evidence_source=STATE_FAILURE,
    ) is None
    store.begin_subtask("user-a")

    runtime = TaskRuntime.begin(TaskSpec.compile("推荐一款鼠标"))
    runtime.authorization.create_authorized = True
    runtime.authorization.candidate_choice_authorized = False
    runtime.phase = RuntimePhase.SELECT
    runtime.execution_ready = True
    RuntimePolicyAdapter.apply(
        store.policy("delivery", "retail"), runtime, CandidateLedger()
    )
    decision = QuestionGate().evaluate("要选第一款还是第二款？", runtime)
    assert decision.allowed


def test_preference_undercoverage_never_becomes_a_hard_runtime_policy():
    store = RuntimePolicyStore("user-a")
    store.begin_subtask("user-a")
    rule = store.observe(
        "delivery",
        "retail",
        "preference_undercoverage",
        evidence_source=STATE_FAILURE,
    )
    assert rule is None
    assert store.rules() == []

    store.begin_subtask("user-a")
    policy = store.policy("delivery", "retail")
    ledger = CandidateLedger()
    RuntimePolicyAdapter.apply(
        policy,
        TaskRuntime.begin(TaskSpec.compile("推荐一个新商品")),
        ledger,
    )
    assert not ledger.require_max_preference_coverage


def test_policy_requires_same_tool_family_and_entity_structure():
    store = RuntimePolicyStore("user-a")
    store.begin_subtask("user-a")
    store.observe(
        "delivery",
        "retail",
        "missed_write",
        evidence_source=STATE_FAILURE,
        tool_family="product_search",
        entity_signature="product(store)+store",
    )
    store.begin_subtask("user-a")

    assert store.policy(
        "delivery",
        "retail",
        tool_family="product_search",
        entity_signature="product(store)+store",
    ).force_decision_after_candidates
    assert not store.policy(
        "delivery",
        "retail",
        tool_family="merchant_search",
        entity_signature="shop",
    ).force_decision_after_candidates


def test_policy_entity_signature_preserves_fictional_parent_topology():
    ledger = CandidateLedger()
    ledger.candidates = {
        "origin::north": Candidate(
            "origin::north", "origin", "North Origin", "", "survey"
        ),
        "glyph::aurora": Candidate(
            "glyph::aurora",
            "glyph",
            "Aurora Glyph",
            "",
            "survey",
            parent_ids=["origin::north"],
        ),
    }
    assert ledger.policy_entity_signature() == "glyph(origin)+origin"


def test_hard_rule_rejects_a_mismatched_or_missing_evidence_source():
    store = RuntimePolicyStore("user-a")
    store.begin_subtask("user-a")
    assert store.observe(
        "delivery",
        "retail",
        "missed_write",
        evidence_source=TrajectoryEvidenceSource.TOOL_ERROR,
    ) is None
    with pytest.raises(TypeError):
        store.observe("delivery", "retail", "missed_write")
    assert store.rules() == []


def test_user_correction_stays_session_evidence_not_a_cross_task_hard_rule():
    store = RuntimePolicyStore("user-a")
    store.begin_subtask("user-a")
    assert store.observe(
        "delivery",
        "retail",
        "user_correction",
        evidence_source=TrajectoryEvidenceSource.USER_CORRECTION,
    ) is None
    assert store.rules() == []


def test_actual_tool_error_can_tighten_only_the_later_matching_scope():
    store = RuntimePolicyStore("user-a")
    store.begin_subtask("user-a")
    rule = store.observe(
        "delivery",
        "retail",
        "tool_error",
        evidence_source=TrajectoryEvidenceSource.TOOL_ERROR,
        tool_family="scan",
        entity_signature="glyph(root)",
        observed_event_epoch=4,
    )
    assert rule is not None
    assert rule.evidence_source == "tool_error"
    assert rule.observed_event_epoch == 4
    assert rule.hard
    assert store.policy(
        "delivery", "retail", tool_family="scan", entity_signature="glyph(root)"
    ).max_identical_tool_failures == 2

    store.begin_subtask("user-a")
    tool_errors = ToolErrorLedger()
    RuntimePolicyAdapter.apply(
        store.policy(
            "delivery",
            "retail",
            tool_family="scan",
            entity_signature="glyph(root)",
        ),
        TaskRuntime.begin(TaskSpec.compile("activate a glyph")),
        CandidateLedger(),
        tool_errors,
    )
    assert tool_errors.max_identical_failures == 1
    assert store.policy(
        "delivery", "retail", tool_family="other", entity_signature="glyph(root)"
    ).max_identical_tool_failures == 2


def test_payment_confirmation_followed_by_user_stop_is_not_agent_failure():
    runtime = TaskRuntime.begin(TaskSpec.compile("帮我点杯喝的送到公司"))
    runtime.phase = RuntimePhase.READY_TO_PAY
    runtime.payment_question_sent = True
    assert not runtime.has_unresolved_payment_failure({"order-1"})


def test_authorized_payment_left_unexecuted_is_agent_failure():
    runtime = TaskRuntime.begin(TaskSpec.compile("帮我买票并支付"))
    runtime.phase = RuntimePhase.READY_TO_PAY
    runtime.authorization.pay_authorized = True
    assert runtime.has_unresolved_payment_failure({"order-1"})
