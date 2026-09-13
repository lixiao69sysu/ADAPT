"""Zero-model tests for the content-free per-condition evaluation detail.

Background: the vendored aggregator keeps only the binary subtask reward, so
saved artifacts carry ``nl_rubrics=[]``, ``reward_breakdown=null`` and no window
detail. Without the graded verdict, bottleneck inference reduces to correlations
over 0/1 outcomes, which is exactly what produced the falsified hypotheses in
E-053 and in the write-failure audit.

Condition texts in this file are synthetic paraphrases, not benchmark rubric
content: the derived record must never carry such text, and the tests enforce
that.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from vita.data_model.simulation import NLRubricCheck, RewardInfo
from vita.data_model.tasks import RewardType
from vita.orchestrator.personalization_orchestrator import PersonalizationOrchestrator

from agent.evaluation_integrity import (
    IntegrityPersonalizationOrchestrator,
    evaluator_error_note,
)
from agent.rubric_detail import condition_record, dimension_of, summarize


def _check(text: str, met: bool) -> NLRubricCheck:
    return NLRubricCheck(nl_rubric=text, met=met, justification="because")


def _reward_info(reward=0.0, checks=None, breakdown=None, info=None):
    return RewardInfo(
        reward=reward,
        nl_rubrics=checks,
        reward_breakdown=breakdown,
        info=info or {"evaluation_status": "ok"},
    )


# ── dimension labelling ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("下单的商品应该是少糖口味，不应选择正常糖等其他糖度选项", "sugar"),
        ("订单配送地址应该为公司地址，不应选择家庭地址", "address"),
        ("配送时长不能超过40分钟", "distance"),
        ("下单的商品的可用时间应该为次日", "date"),
        ("应选择靠窗座位，不应选择靠过道座位", "seat"),
        ("不应选择已售罄的座位", "inventory"),  # specificity beats the seat family
        ("下单的商品应该是高铁票（G字头车次）", "route"),
        ("优先选择该品牌的店铺", "store"),
        ("商家评分应高于4.5", "rating"),
        ("下单的商品不能为冰的规格，可选择温热", "temperature"),
        ("数量应为一件", "quantity"),
        ("用户偏好有仪式感或招牌特色", "preference"),
        ("订单应该已支付", "payment"),
        ("完全无法归类的条件", "other"),
        (None, "other"),
    ],
)
def test_dimension_labels_are_stable(text, expected):
    assert dimension_of(text) == expected


def test_a_missed_condition_wins_its_dimension_label():
    record = condition_record(
        _reward_info(
            checks=[
                _check("商品应为少糖口味", True),
                _check("甜度不应为正常糖", False),
            ]
        )
    )
    assert record["dimensions"]["sugar"] is False
    assert "sugar" in record["missed_dimensions"]


# ── record derivation ────────────────────────────────────────────────────


def test_record_derives_the_graded_verdict():
    record = condition_record(
        _reward_info(
            reward=0.0,
            checks=[
                _check("商品应匹配指定名称", True),
                _check("商品应为少糖口味", False),
                _check("配送地址应为公司地址", True),
                _check("配送时长不能超过40分钟", False),
            ],
        )
    )
    assert record["status"] == "available"
    assert record["source"] == "nl_rubrics"
    assert record["n_conditions"] == 4
    assert record["n_met"] == 2
    assert record["fraction_met"] == 0.5
    assert record["missed_dimensions"] == ["distance", "sugar"]


def test_record_is_content_free():
    """The derived record must never carry rubric text."""
    secret = "不应选择糯糯青稀以及伯牙绝弦等其他茶饮"
    record = condition_record(_reward_info(checks=[_check(secret, False)]))
    rendered = json.dumps(record, ensure_ascii=False)
    for fragment in ("糯糯青稀", "伯牙绝弦", "茶饮", secret):
        assert fragment not in rendered
    assert record["fraction_met"] == 0.0


def test_record_falls_back_to_the_reward_breakdown():
    record = condition_record(
        _reward_info(reward=0.0, breakdown={RewardType.NL_ASSERTION: 0.75})
    )
    assert record["status"] == "available"
    assert record["source"] == "reward_breakdown"
    assert record["fraction_met"] == 0.75
    assert record["missed_dimensions"] == []


def test_record_marks_unavailable_without_any_evaluation_detail():
    record = condition_record(_reward_info(reward=0.0))
    assert record["status"] == "unavailable"
    assert record["fraction_met"] is None
    assert record["n_conditions"] == 0


# ── aggregation ──────────────────────────────────────────────────────────


def test_summarize_excludes_unavailable_from_fractions():
    records = {
        "a": condition_record(_reward_info(checks=[_check("少糖", True), _check("常温", True)])),
        "b": condition_record(_reward_info(checks=[_check("少糖", False), _check("常温", True)])),
        "c": condition_record(_reward_info()),  # unavailable
    }
    summary = summarize(records)
    assert summary["n_subtasks"] == 3
    assert summary["n_available"] == 2
    assert summary["n_unavailable"] == 1
    assert summary["mean_fraction_met"] == pytest.approx((1.0 + 0.5) / 2)
    # per-condition pooling, not the mean of per-subtask fractions
    assert summary["per_condition_success_rate"] == pytest.approx(3 / 4)
    assert summary["fraction_met_histogram"] == {"0.5": 1, "1.0": 1}


def test_summarize_ranks_missed_dimensions_and_finds_the_convertible_pool():
    records = {
        # one condition away, failing
        "one_away": condition_record(
            _reward_info(
                reward=0.0,
                checks=[_check("少糖", True), _check("配送时长不超过40分钟", False)],
            )
        ),
        # two away, failing
        "two_away": condition_record(
            _reward_info(
                reward=0.0,
                checks=[
                    _check("少糖", False),
                    _check("配送时长不超过40分钟", False),
                    _check("公司地址", True),
                ],
            )
        ),
        # perfect
        "perfect": condition_record(
            _reward_info(reward=1.0, checks=[_check("少糖", True)])
        ),
    }
    summary = summarize(records)
    assert summary["missed_by_dimension"]["distance"] == 2
    assert summary["missed_by_dimension"]["sugar"] == 1
    assert summary["one_condition_away"] == ["one_away"]
    assert summary["n_one_condition_away"] == 1
    assert "per_subtask" in summary


def test_a_passing_subtask_is_never_counted_as_one_condition_away():
    records = {
        "passing": condition_record(
            _reward_info(reward=1.0, checks=[_check("少糖", True), _check("常温", True)])
        )
    }
    assert summarize(records)["n_one_condition_away"] == 0


# ── orchestrator wiring ──────────────────────────────────────────────────


def _orchestrator(monkeypatch, reward_info, *, retries=0):
    import agent.evaluation_integrity as ei

    monkeypatch.setattr(
        PersonalizationOrchestrator,
        "_evaluate_subtask",
        lambda self, subtask, result: reward_info,
    )
    orchestrator = object.__new__(ei.IntegrityPersonalizationOrchestrator)
    orchestrator.evaluator_retries = retries
    orchestrator.evaluator_retry_backoff_seconds = 0.0
    orchestrator.evaluation_records = []
    orchestrator.saved_subtask_trajectories = []
    orchestrator.rubric_details = {}
    return orchestrator


def test_evaluate_subtask_records_the_detail_without_changing_the_reward(monkeypatch):
    info = _reward_info(
        reward=0.0,
        checks=[_check("少糖", True), _check("常温", False)],
    )
    orchestrator = _orchestrator(monkeypatch, info)
    subtask = SimpleNamespace(subtask_id="sub_U_3")

    returned = orchestrator._evaluate_subtask(subtask, {})

    assert returned is info, "the reward path must be untouched"
    assert returned.reward == 0.0
    assert set(orchestrator.rubric_details) == {"sub_U_3"}
    assert orchestrator.rubric_details["sub_U_3"]["fraction_met"] == 0.5


def test_a_failed_evaluation_is_recorded_as_unavailable_not_as_a_zero(monkeypatch):
    failed = RewardInfo(
        reward=0.0,
        info={
            "evaluation_status": "evaluation_failed",
            "evaluation_attempts": 1,
            "evaluator_error": "transport",
            "note": "Evaluation unavailable; this is not an agent reward.",
        },
    )
    assert evaluator_error_note(failed) is not None
    orchestrator = _orchestrator(monkeypatch, failed, retries=0)
    subtask = SimpleNamespace(subtask_id="sub_U_4")

    orchestrator._evaluate_subtask(subtask, {})

    record = orchestrator.rubric_details["sub_U_4"]
    assert record["status"] == "unavailable"
    assert record["fraction_met"] is None
    assert summarize(orchestrator.rubric_details)["n_unavailable"] == 1


def test_run_writes_rubric_detail_into_simulation_states(monkeypatch):
    class _FakeSimulation:
        def __init__(self):
            self.states: dict = {}
            self.reward_info = None

    monkeypatch.setattr(
        PersonalizationOrchestrator, "run", lambda self: _FakeSimulation()
    )
    orchestrator = object.__new__(IntegrityPersonalizationOrchestrator)
    orchestrator.evaluator_retries = 0
    orchestrator.evaluator_retry_backoff_seconds = 0.0
    orchestrator.evaluation_records = []
    orchestrator.saved_subtask_trajectories = []
    orchestrator.rubric_details = {
        "sub_U_1": condition_record(
            _reward_info(reward=0.0, checks=[_check("少糖", True), _check("常温", False)])
        )
    }

    simulation = orchestrator.run()

    assert "rubric_detail" in simulation.states
    detail = simulation.states["rubric_detail"]
    assert detail["n_subtasks"] == 1
    assert detail["missed_by_dimension"] == {"temperature": 1}
    assert detail["n_one_condition_away"] == 1


def test_reevaluate_saved_also_publishes_the_detail(monkeypatch):
    """Regression: the re-evaluation path does not go through ``run()``.

    The first cohort-wide re-evaluation wrote every simulation without a
    ``rubric_detail`` block because only ``run()`` published it, so the whole
    batch had to be discarded.
    """

    def fake_evaluate(self, subtask, result):
        info = _reward_info(
            reward=0.0, checks=[_check("少糖", True), _check("常温", False)]
        )
        self.rubric_details[str(subtask.subtask_id)] = condition_record(info)
        return info

    monkeypatch.setattr(
        IntegrityPersonalizationOrchestrator, "_evaluate_subtask", fake_evaluate
    )
    monkeypatch.setattr(
        IntegrityPersonalizationOrchestrator,
        "_aggregate_rewards",
        lambda self, rewards: _reward_info(reward=0.0),
    )
    orchestrator = object.__new__(IntegrityPersonalizationOrchestrator)
    orchestrator.rubric_details = {}
    orchestrator.evaluation_records = []
    orchestrator.evaluator_retries = 0
    subtask = SimpleNamespace(subtask_id="sub_U_1", evaluation_criteria=object())
    orchestrator.task = SimpleNamespace(subtasks=[subtask])
    simulation = SimpleNamespace(states={}, reward_info=None)

    orchestrator.reevaluate_saved(
        simulation, [{"subtask_id": "sub_U_1", "messages": []}]
    )

    detail = simulation.states.get("rubric_detail")
    assert detail is not None, "the re-evaluation path must publish the detail too"
    assert detail["n_subtasks"] == 1
    assert detail["missed_by_dimension"] == {"temperature": 1}


def test_reevaluate_saved_clears_stale_detail_between_simulations(monkeypatch):
    """A reused orchestrator must not leak another simulation's records."""

    def fake_evaluate(self, subtask, result):
        info = _reward_info(checks=[_check("少糖", True)])
        self.rubric_details[str(subtask.subtask_id)] = condition_record(info)
        return info

    monkeypatch.setattr(
        IntegrityPersonalizationOrchestrator, "_evaluate_subtask", fake_evaluate
    )
    monkeypatch.setattr(
        IntegrityPersonalizationOrchestrator,
        "_aggregate_rewards",
        lambda self, rewards: _reward_info(reward=1.0),
    )
    orchestrator = object.__new__(IntegrityPersonalizationOrchestrator)
    orchestrator.rubric_details = {"stale_from_previous_run": {"status": "available"}}
    orchestrator.evaluation_records = []
    orchestrator.evaluator_retries = 0
    subtask = SimpleNamespace(subtask_id="sub_U_1", evaluation_criteria=object())
    orchestrator.task = SimpleNamespace(subtasks=[subtask])
    simulation = SimpleNamespace(states={}, reward_info=None)

    orchestrator.reevaluate_saved(
        simulation, [{"subtask_id": "sub_U_1", "messages": []}]
    )

    assert "stale_from_previous_run" not in simulation.states["rubric_detail"]["per_subtask"]
    assert simulation.states["rubric_detail"]["n_subtasks"] == 1
