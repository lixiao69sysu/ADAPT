"""Tests for frozen-code, per-user runtime procedural learning."""

from __future__ import annotations

from dataclasses import asdict

import pytest

from agent.decision import CandidateLedger, TaskSpec
from agent.runtime import (
    QuestionGate,
    RuntimePhase,
    RuntimePolicyAdapter,
    RuntimePolicyStore,
    TaskRuntime,
)


def test_rule_is_learned_now_but_activates_on_later_subtask_only():
    store = RuntimePolicyStore("user-a")
    store.begin_subtask("user-a")
    rule = store.observe("delivery", "retail", "missed_write")
    assert rule is not None
    assert rule.active_from_subtask == 2
    assert not store.policy("delivery", "retail").force_decision_after_candidates

    store.begin_subtask("user-a")
    assert store.policy("delivery", "retail").force_decision_after_candidates


def test_repeat_search_requires_repeated_evidence_and_changes_budget():
    store = RuntimePolicyStore("user-a")
    store.begin_subtask("user-a")
    store.observe("delivery", "beverage", "repeat_search")
    store.begin_subtask("user-a")
    assert store.policy("delivery", "beverage").max_searches_per_family == 2
    store.observe("delivery", "beverage", "repeat_search")
    store.begin_subtask("user-a")
    assert store.policy("delivery", "beverage").max_searches_per_family == 1


def test_policy_is_facet_scoped():
    store = RuntimePolicyStore("user-a")
    store.begin_subtask("user-a")
    store.observe("delivery", "beverage", "missed_write")
    store.begin_subtask("user-a")
    assert store.policy("delivery", "beverage").force_decision_after_candidates
    assert not store.policy("delivery", "retail").force_decision_after_candidates
    assert not store.policy("ota", "hotel").force_decision_after_candidates


def test_switching_user_clears_all_learned_rules():
    store = RuntimePolicyStore("user-a")
    store.begin_subtask("user-a")
    store.observe("delivery", "retail", "missed_write")
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
    rule = store.observe("delivery", "retail", "missed_write")
    payload = asdict(rule)
    rendered = repr(payload)
    assert payload["capability_target"] == "candidate_to_action_execution"
    assert payload["failure_cluster"]
    assert payload["proposed_change"]
    assert "user-a" not in rendered
    assert "P00094" not in rendered
    assert "target_product_ids" not in rendered


def test_unknown_case_specific_scope_is_collapsed_to_general():
    store = RuntimePolicyStore("user-a")
    store.begin_subtask("user-a")
    rule = store.observe("B865629", "P00094", "missed_write")
    assert rule.domain == "general"
    assert rule.facet == "general"
    assert "B865629" not in repr(asdict(rule))
    assert "P00094" not in repr(asdict(rule))


def test_hidden_evaluator_payload_cannot_be_passed_to_learner():
    store = RuntimePolicyStore("user-a")
    store.begin_subtask("user-a")
    with pytest.raises(TypeError):
        store.observe(
            "delivery",
            "retail",
            "missed_write",
            reward=1.0,
        )
    assert store.observe("delivery", "retail", "rubric") is None
    assert store.rules() == []


def test_policy_adapter_changes_deterministic_candidate_to_action_transition():
    store = RuntimePolicyStore("user-a")
    store.begin_subtask("user-a")
    store.observe("delivery", "retail", "missed_write")
    store.begin_subtask("user-a")

    runtime = TaskRuntime.begin(TaskSpec.compile("推荐一款鼠标"))
    runtime.authorization.create_authorized = True
    runtime.authorization.candidate_choice_authorized = False
    ledger = CandidateLedger()
    RuntimePolicyAdapter.apply(store.policy("delivery", "retail"), runtime, ledger)
    runtime.observe_candidates(2, execution_ready=True)
    assert runtime.phase == RuntimePhase.READY_TO_CREATE


def test_learned_question_policy_is_executable_not_only_rendered_text():
    store = RuntimePolicyStore("user-a")
    store.begin_subtask("user-a")
    store.observe("delivery", "retail", "candidate_choice_reask")
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
    assert not decision.allowed
    assert "learned same-user runtime policy" in decision.reason


def test_preference_undercoverage_activates_strict_grounding_next_subtask():
    store = RuntimePolicyStore("user-a")
    store.begin_subtask("user-a")
    rule = store.observe("delivery", "retail", "preference_undercoverage")
    assert rule is not None
    assert rule.capability_target == "preference_to_candidate_grounding"
    assert not store.policy(
        "delivery", "retail"
    ).require_max_preference_coverage

    store.begin_subtask("user-a")
    policy = store.policy("delivery", "retail")
    ledger = CandidateLedger()
    RuntimePolicyAdapter.apply(
        policy,
        TaskRuntime.begin(TaskSpec.compile("推荐一个新商品")),
        ledger,
    )
    assert policy.require_max_preference_coverage
    assert ledger.require_max_preference_coverage


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
