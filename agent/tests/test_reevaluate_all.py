"""Zero-model tests for the cohort-wide re-evaluation entry point.

``reevaluate_all`` re-scores saved trajectories without replaying the agent, so
it costs evaluator calls only. These tests stub the per-item scorer entirely:
no model call is made, and no benchmark task data is loaded.
"""

from __future__ import annotations

import json

import pytest

import agent.reevaluate_guarded as rg


@pytest.fixture
def no_dataset(monkeypatch):
    """Keep the tests offline: no task dataset, no model config."""
    monkeypatch.setattr(rg, "get_tasks", lambda language: [])
    monkeypatch.setattr(rg, "models", {"evaluator": {"temperature": 0.0}})


def _source_item(task_id: str, trial: int) -> dict:
    return {
        "task_id": task_id,
        "trial": trial,
        "reward_info": {"reward": 0.0, "info": {"subtask_rewards": {}}},
        "states": {"integrity_subtask_trajectories": [{"subtask_id": f"sub_{task_id}_1"}]},
    }


def _detail(fraction_met: float, missed: list[str], n_met: int = 1, n_cond: int = 2) -> dict:
    return {
        "per_subtask": {
            "sub_x_1": {
                "status": "available",
                "source": "nl_rubrics",
                "reward": 0.0,
                "n_conditions": n_cond,
                "n_met": n_met,
                "fraction_met": fraction_met,
                "dimensions": {},
                "missed_dimensions": missed,
            }
        }
    }


def _scored(task_id: str, trial: int, detail: dict) -> dict:
    item = _source_item(task_id, trial)
    item["states"]["rubric_detail"] = detail
    return item


def _write(path, items):
    path.write_text(
        json.dumps({"timestamp": "x", "info": {}, "tasks": [], "simulations": items}),
        encoding="utf-8",
    )


def test_item_key_pairs_task_with_trial():
    assert rg._item_key({"task_id": "U1", "trial": 3}) == ("U1", 3)


def test_aggregate_merges_per_subtask_records_across_simulations():
    summary = rg._aggregate_rubric_detail(
        [
            _scored("U1", 0, _detail(0.5, ["sugar"], n_met=1, n_cond=2)),
            _scored("U2", 0, _detail(1.0, [], n_met=2, n_cond=2)),
        ]
    )
    assert summary["n_subtasks"] == 2
    assert summary["n_available"] == 2
    assert summary["missed_by_dimension"] == {"sugar": 1}
    assert summary["n_one_condition_away"] == 1  # 1 of 2 met, reward 0


def test_reevaluate_all_never_modifies_the_source(tmp_path, monkeypatch, no_dataset):
    source = tmp_path / "source.json"
    _write(source, [_source_item("U1", 0)])
    before = source.read_text(encoding="utf-8")
    monkeypatch.setattr(rg, "_rescore_item", lambda item, **kw: _scored("U1", 0, _detail(0.5, ["sugar"])))

    rg.reevaluate_all(source, tmp_path / "out.json", llm_evaluator="evaluator")

    assert source.read_text(encoding="utf-8") == before


def test_reevaluate_all_resumes_and_reuses_scored_items(tmp_path, monkeypatch, no_dataset):
    source = tmp_path / "source.json"
    _write(source, [_source_item("U1", 0), _source_item("U2", 0)])
    out = tmp_path / "out.json"
    _write(out, [_scored("U1", 0, _detail(0.5, ["sugar"]))])

    calls: list[str] = []

    def fake(item, **kw):
        calls.append(str(item["task_id"]))
        return _scored(str(item["task_id"]), item["trial"], _detail(1.0, []))

    monkeypatch.setattr(rg, "_rescore_item", fake)

    checkpoint = rg.reevaluate_all(source, out, llm_evaluator="evaluator")

    assert calls == ["U2"], "the already-scored item must be reused, not re-scored"
    assert checkpoint["reevaluate_all"]["reused"] == 1
    assert checkpoint["reevaluate_all"]["scored"] == 1
    assert len(checkpoint["simulations"]) == 2


def test_force_rescores_everything(tmp_path, monkeypatch, no_dataset):
    source = tmp_path / "source.json"
    _write(source, [_source_item("U1", 0)])
    out = tmp_path / "out.json"
    _write(out, [_scored("U1", 0, _detail(0.5, ["sugar"]))])
    monkeypatch.setattr(rg, "_rescore_item", lambda item, **kw: _scored("U1", 0, _detail(1.0, [])))

    checkpoint = rg.reevaluate_all(source, out, llm_evaluator="evaluator", force=True)

    assert checkpoint["reevaluate_all"]["scored"] == 1
    assert checkpoint["reevaluate_all"]["reused"] == 0


def test_a_failing_item_is_isolated_and_keeps_its_original_record(
    tmp_path, monkeypatch, no_dataset
):
    source = tmp_path / "source.json"
    _write(source, [_source_item("U1", 0), _source_item("U2", 0)])

    def fake(item, **kw):
        if item["task_id"] == "U1":
            raise RuntimeError("evaluator unavailable")
        return _scored("U2", 0, _detail(1.0, []))

    monkeypatch.setattr(rg, "_rescore_item", fake)

    checkpoint = rg.reevaluate_all(source, tmp_path / "out.json", llm_evaluator="evaluator")

    stats = checkpoint["reevaluate_all"]
    assert stats["failed"] == 1
    assert stats["scored"] == 1
    assert stats["failures"][0]["task_id"] == "U1"
    # the original item is carried over so the batch never loses a trajectory
    kept = [i for i in checkpoint["simulations"] if i["task_id"] == "U1"]
    assert kept and "rubric_detail" not in kept[0]["states"]
    # and the aggregate only counts what was actually scored
    assert checkpoint["rubric_summary"]["n_subtasks"] == 1


def test_user_filter_selects_a_subset(tmp_path, monkeypatch, no_dataset):
    source = tmp_path / "source.json"
    _write(source, [_source_item("U1", 0), _source_item("U2", 0)])
    monkeypatch.setattr(rg, "_rescore_item", lambda item, **kw: _scored(str(item["task_id"]), 0, _detail(1.0, [])))

    checkpoint = rg.reevaluate_all(
        source, tmp_path / "out.json", llm_evaluator="evaluator", user_ids=["U2"]
    )

    assert checkpoint["reevaluate_all"]["not_selected"] == 1
    assert [i["task_id"] for i in checkpoint["simulations"]] == ["U2"]


def test_output_records_the_guard_and_the_source(tmp_path, monkeypatch, no_dataset):
    source = tmp_path / "source.json"
    _write(source, [_source_item("U1", 0)])
    monkeypatch.setattr(rg, "_rescore_item", lambda item, **kw: _scored("U1", 0, _detail(1.0, [])))

    checkpoint = rg.reevaluate_all(source, tmp_path / "out.json", llm_evaluator="evaluator")

    assert checkpoint["reevaluation_guard"]["agent_replay"] is False
    assert checkpoint["reevaluation_guard"]["source"] == str(source)
    assert checkpoint["info"]["rubric_detail_from"] == str(source)
