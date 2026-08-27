"""Synthetic cross-layer invariants distilled from observable trace classes."""

from __future__ import annotations

from agent.decision import CandidateLedger, DecisionCard, TaskSpec
from agent.memory.adapt_memory import ADAPTMemory
from agent.runtime import (
    QuestionGate,
    RuntimePhase,
    RuntimePolicyStore,
    TaskRuntime,
    TrajectoryEvidenceSource,
)

def test_soup_delegation_transitions_to_create_without_reasking():
    runtime = TaskRuntime.begin(TaskSpec.compile("想喝汤了，你帮我点个送到家里"))
    runtime.observe_user("随便，你看着办吧。")
    runtime.observe_candidates(3)
    assert runtime.phase == RuntimePhase.READY_TO_CREATE
    assert not QuestionGate().evaluate("您喜欢什么口味的汤？", runtime).allowed


def test_retail_task_does_not_receive_restaurant_avoid_memory():
    memory = ADAPTMemory(language="chinese")
    memory.update([{
        "type": "conversation", "timestamp": "2026-01-01 10:00:00",
        "dialogue": [{"role": "user", "content": "我不吃香菜"}],
    }])
    card = memory.compile_task("鼠标坏了，帮我买个鼠标送到家里吧")
    assert "香菜" not in card.avoid


def test_unseen_retail_request_does_not_invent_size_requirement():
    runtime = TaskRuntime.begin(TaskSpec.compile("脚冷，该买一双新的拖鞋了，帮我下单一双送到家里"))
    assert runtime.next_question_dimension() == ""
    runtime.observe_user("第一双吧，直接下单。")
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_name=保暖拖鞋42-43码, "
        "product_id=S1_P00001, quantity=5)",
    )
    runtime.observe_candidates(1)
    assert runtime.phase == RuntimePhase.READY_TO_CREATE
    assert runtime.next_question_dimension() == ""


def test_ranker_excludes_zero_inventory_and_keeps_top_five():
    ledger = CandidateLedger()
    for index in range(7):
        ledger.observe(
            "delivery_product_search_recommand",
            f"StoreProduct(store_id=S1_S00001, product_name=鼠标{index}, "
            f"product_id=S1_P0000{index}, quantity={0 if index == 0 else 5}, price={index + 10})",
        )
    shortlist = ledger.shortlist(DecisionCard(must=["鼠标"]))
    assert len(shortlist) == 5
    assert all(candidate.inventory != 0 for candidate in shortlist)


def test_execution_lesson_changes_runtime_policy_not_only_prompt():
    policies = RuntimePolicyStore("synthetic-user")
    policies.begin_subtask("synthetic-user")
    source = TrajectoryEvidenceSource.EXPLICIT_STATE_FAILURE
    assert policies.observe(
        "delivery", "retail", "missed_write", evidence_source=source
    ) is None
    policies.observe(
        "delivery", "retail", "repeat_search", evidence_source=source
    )
    policies.observe(
        "delivery", "retail", "repeat_search", evidence_source=source
    )
    policies.begin_subtask("synthetic-user")
    policy = policies.policy("delivery", "retail")
    assert not policy.force_decision_after_candidates
    assert policy.max_searches_per_family == 1
