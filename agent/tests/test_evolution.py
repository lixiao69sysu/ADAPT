import json

from agent.evolution.cases import (
    apply_diagnosis_hints,
    mine_bad_cases,
    mine_log_hints,
)
from agent.evolution.catalog import propose_candidates
from agent.evolution.gate import PromotionGate
from agent.evolution.metrics import build_scorecards
from agent.evolution.metrics import read_subtask_rewards_from_log
from agent.evolution.models import Scorecard
from agent.evolution.registry import StrategyRegistry
from agent.evolution.retrieval_probe import rank_target
from agent.evolution.runtime import load_strategy_changes
from agent.memory.adapt_memory import ADAPTMemory
from agent.memory.signals import Signal


def _assistant_call(name, arguments, call_id):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "name": name,
                "arguments": arguments,
                "requestor": "assistant",
            }
        ],
    }


def _result_fixture():
    repeated = _assistant_call(
        "delivery_product_search_recommand", {"keywords": ["拖鞋"]}, "s1"
    )
    return {
        "simulations": [
            {
                "id": "sim-1",
                "task_id": "U1",
                "termination_reason": "agent_stop",
                "reward_info": {
                    "reward": 0.5,
                    "info": {
                        "subtask_rewards": {
                            "subtask_0_reward": 0,
                            "subtask_1_reward": 1,
                        },
                        "subtask_skill_tested": {
                            "subtask_0": [],
                            "subtask_1": [],
                        },
                    },
                },
                "messages": [
                    {"role": "assistant", "content": "你好，请问需要什么服务？"},
                    {"role": "user", "content": "帮我买双拖鞋"},
                    repeated,
                    repeated,
                    repeated,
                    {
                        "role": "tool",
                        "name": "delivery_product_search_recommand",
                        "content": "product_id=P1",
                        "error": False,
                    },
                    _assistant_call(
                        "create_delivery_order",
                        {"product_ids": ["P1"]},
                        "create",
                    ),
                    {
                        "role": "tool",
                        "name": "pay_delivery_order",
                        "content": "Payment successful",
                        "error": False,
                    },
                    {"role": "assistant", "content": "你好，请问需要什么服务？"},
                    {"role": "user", "content": "帮我买水果"},
                    {"role": "assistant", "content": "已完成"},
                ],
                "states": {
                    "memory_snapshots": {
                        "subtask_0_memory": "- 用户偏好商品: 哈瓦娜 Slim 人字拖",
                        "subtask_1_memory": "- 用户喜欢: 苹果",
                    }
                },
            }
        ]
    }


def test_mines_failed_subtasks_and_diagnoses_observable_causes():
    cases = mine_bad_cases(_result_fixture())

    assert len(cases) == 1
    case = cases[0]
    assert case.case_id == "U1:subtask_0"
    assert case.instruction == "帮我买双拖鞋"
    assert case.paid is True
    assert case.max_identical_calls == 3
    assert {item.code for item in case.diagnoses} == {
        "memory_retrieval",
        "repeated_tool_call",
    }


def test_candidate_catalog_is_bounded_and_tracks_supporting_cases():
    candidates = propose_candidates(mine_bad_cases(_result_fixture()))
    by_id = {item.strategy_id: item for item in candidates}

    assert "retrieval.query_normalization.v1" in by_id
    assert "harness.search_stage_progression.v1" in by_id
    assert by_id["retrieval.query_normalization.v1"].status == "pending"
    assert by_id["retrieval.query_normalization.v1"].supporting_case_ids == [
        "U1:subtask_0"
    ]
    assert all(item.mutation_kind in {"config", "policy"} for item in candidates)


def test_explicit_hint_can_propose_cross_entity_soft_warning():
    cases = mine_bad_cases(_result_fixture())
    hinted = apply_diagnosis_hints(
        cases, {"U1:subtask_0": ["cross_entity_mismatch"]}
    )

    candidates = propose_candidates(hinted)

    assert "harness.cross_entity_soft_warning.v1" in {
        item.strategy_id for item in candidates
    }


def test_mines_rejected_draft_diagnoses_from_agent_log(tmp_path):
    log = tmp_path / "run.log"
    log.write_text(
        "SUBTASK 2/12\n"
        "ADAPT_TOOL_DRAFT_REJECTED issues=['repeated_tool_call']\n"
        "SUBTASK 10/12\n"
        "ADAPT_TOOL_DRAFT_REJECTED issues=['cross_entity_mismatch']\n",
        encoding="utf-8",
    )

    hints = mine_log_hints(log, "U1")

    assert hints == {
        "U1:subtask_1": ["repeated_tool_call"],
        "U1:subtask_9": ["cross_entity_mismatch"],
    }


def test_reads_completed_subtask_rewards_from_partial_log(tmp_path):
    log = tmp_path / "partial.log"
    log.write_text(
        "Subtask sub_U642088_1 evaluation: reward=1.0\n"
        "Subtask sub_U642088_2 evaluation: reward=0.0\n",
        encoding="utf-8",
    )

    assert read_subtask_rewards_from_log(log) == {
        "U642088:subtask_0": 1.0,
        "U642088:subtask_1": 0.0,
    }


def test_promotion_gate_prioritizes_reward_and_blocks_regressions():
    baseline = Scorecard(
        sample_count=12,
        avg_reward=0.25,
        bad_case_success_rate=0.0,
        regression_success_rate=0.75,
        invalid_tool_rate=0.10,
        loop_rate=0.20,
        avg_tokens=10_000,
    )
    improved = Scorecard(
        sample_count=12,
        avg_reward=0.33,
        bad_case_success_rate=1.0,
        regression_success_rate=0.75,
        invalid_tool_rate=0.08,
        loop_rate=0.10,
        avg_tokens=18_000,
    )
    regressed = Scorecard(
        sample_count=12,
        avg_reward=0.34,
        bad_case_success_rate=1.0,
        regression_success_rate=0.60,
        invalid_tool_rate=0.08,
        loop_rate=0.10,
    )

    accepted = PromotionGate().evaluate(baseline, improved)
    rejected = PromotionGate().evaluate(baseline, regressed)

    assert accepted.promoted is True
    assert "token" not in " ".join(accepted.reasons).lower()
    assert rejected.promoted is False
    assert any("regression" in reason for reason in rejected.reasons)


def test_registry_persists_candidates_and_gate_decisions(tmp_path):
    path = tmp_path / "strategies.json"
    registry = StrategyRegistry(path)
    candidate = propose_candidates(mine_bad_cases(_result_fixture()))[0]
    registry.upsert([candidate])
    registry.record_decision(candidate.strategy_id, promoted=True, reasons=["passed"])

    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["candidates"][candidate.strategy_id]["status"] == "promoted"
    assert payload["active_strategy_ids"] == [candidate.strategy_id]


def test_registry_reanalysis_preserves_probe_and_terminal_decision(tmp_path):
    path = tmp_path / "strategies.json"
    registry = StrategyRegistry(path)
    candidate = propose_candidates(mine_bad_cases(_result_fixture()))[0]
    registry.upsert([candidate])
    probe = {"task_id": "U1", "baseline_rank": 12, "candidate_rank": 1}
    registry.record_probe(candidate.strategy_id, probe)
    registry.record_decision(candidate.strategy_id, promoted=True, reasons=["passed"])

    registry.upsert([candidate])
    stored = registry.load()["candidates"][candidate.strategy_id]

    assert stored["status"] == "promoted"
    assert stored["decision_reasons"] == ["passed"]
    assert stored["probes"] == [probe]


def test_build_scorecards_uses_failed_baseline_as_bad_cases(tmp_path):
    baseline = _result_fixture()
    candidate = _result_fixture()
    candidate["simulations"][0]["reward_info"]["info"]["subtask_rewards"] = {
        "subtask_0_reward": 1,
        "subtask_1_reward": 1,
    }

    baseline_log = tmp_path / "baseline.log"
    candidate_log = tmp_path / "candidate.log"
    baseline_log.write_text(
        "SUBTASK 1/2\nADAPT_TOOL_DRAFT_REJECTED issues=['repeated_tool_call']\n",
        encoding="utf-8",
    )
    candidate_log.write_text("SUBTASK 1/2\n", encoding="utf-8")

    baseline_card, candidate_card = build_scorecards(
        baseline,
        candidate,
        baseline_log=baseline_log,
        candidate_log=candidate_log,
    )

    assert baseline_card.avg_reward == 0.5
    assert baseline_card.bad_case_success_rate == 0.0
    assert candidate_card.avg_reward == 1.0
    assert candidate_card.bad_case_success_rate == 1.0
    assert candidate_card.regression_success_rate == 1.0
    assert baseline_card.loop_rate == 0.5
    assert candidate_card.loop_rate == 0.0


def test_retrieval_probe_executes_query_expansion_candidate():
    memory = ADAPTMemory(language="chinese", top_k=1)
    memory.stream.add(
        Signal(
            predicate="prefers_product",
            object="哈瓦娜 Slim 系列人字拖",
            confidence=0.8,
            timestamp="2024-06-01 00:00:00",
            type="order",
        )
    )
    memory.stream.add(
        Signal(
            predicate="raw_observation",
            object="帮我赶紧送到家里",
            confidence=0.5,
            timestamp="2024-06-14 00:00:00",
            type="conversation",
            raw="帮我赶紧送到家里",
        )
    )
    changes = {
        "product_type_expansions": {"拖鞋": ["人字拖"]},
        "ignore_generic_bigrams": True,
    }

    baseline = rank_target(
        memory,
        "帮我买双拖鞋送到家里",
        ["哈瓦娜", "人字拖"],
        now="2024-06-15",
        changes={},
    )
    candidate = rank_target(
        memory,
        "帮我买双拖鞋送到家里",
        ["哈瓦娜", "人字拖"],
        now="2024-06-15",
        changes=changes,
    )

    assert candidate["best_target_rank"] < baseline["best_target_rank"]


def test_runtime_loads_pending_candidate_only_when_explicitly_selected(tmp_path):
    path = tmp_path / "strategies.json"
    registry = StrategyRegistry(path)
    candidate = propose_candidates(mine_bad_cases(_result_fixture()))
    candidate = next(
        item
        for item in candidate
        if item.strategy_id == "retrieval.query_normalization.v1"
    )
    registry.upsert([candidate])

    inactive = load_strategy_changes(path)
    selected = load_strategy_changes(path, candidate.strategy_id)

    assert inactive == {}
    assert selected["memory.retrieval"]["product_type_expansions"] == {
        "拖鞋": ["人字拖"]
    }


def test_adapt_memory_can_apply_registry_candidate_at_runtime(tmp_path):
    path = tmp_path / "strategies.json"
    registry = StrategyRegistry(path)
    candidate = next(
        item
        for item in propose_candidates(mine_bad_cases(_result_fixture()))
        if item.strategy_id == "retrieval.query_normalization.v1"
    )
    registry.upsert([candidate])
    memory = ADAPTMemory(
        language="chinese",
        top_k=1,
        strategy_registry=path,
        candidate_strategy_id=candidate.strategy_id,
    )
    memory.stream.add(
        Signal(
            predicate="prefers_product",
            object="哈瓦娜 Slim 系列人字拖",
            confidence=0.8,
            timestamp="2024-06-01 00:00:00",
            type="order",
        )
    )
    memory.stream.add(
        Signal(
            predicate="raw_observation",
            object="帮我赶紧送到家里",
            confidence=0.5,
            timestamp="2024-06-14 00:00:00",
            type="conversation",
            raw="帮我赶紧送到家里",
        )
    )

    output = memory.read("帮我买双拖鞋送到家里", with_suggestion=False)

    assert "哈瓦娜 Slim 系列人字拖" in output
