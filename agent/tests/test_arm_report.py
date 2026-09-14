"""Edge cases of the arm-report device, on synthetic checkpoints.

The arm being measured cannot be re-run on demand, so the failure modes most
likely to occur while reading it -- and which would silently produce a wrong
number rather than an error -- are pinned here:

  * a user skipped by the runner (an exception in one user only skips that user,
    ``vitabench_runner.py:531-536``), which must abort rather than quietly report
    a 7-user mean as if it were the cohort;
  * an evaluator failure, whose subtask rewards are missing and which therefore
    count as 0 in the official metric;
  * the positive branch of the ``states['adapt_agent']`` reader, which no
    existing checkpoint exercises (every artifact on disk predates the current
    runner's attribution fields) and which is the only source of the mechanism
    counters used for attribution;
  * the pre-registered gate table, so ``required_net`` cannot drift.

No model calls, no benchmark access.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO / "scripts" / "adapt_arm_report.py"

USERS = [
    "E057330",
    "E941775",
    "J365414",
    "M793481",
    "O309411",
    "P722245",
    "Q089190",
    "U000828",
]


def _sim(task_id, trial, rewards, status="ok", events=None):
    info = {
        "subtask_rewards": {
            f"subtask_{i}_reward": r for i, r in enumerate(rewards)
        }
    }
    states = {"adapt_agent": events} if events is not None else {}
    return {
        "task_id": task_id,
        "trial": trial,
        "evaluation_status": status,
        "reward_info": {"info": info},
        "states": states,
    }


def _checkpoint(tasks, sims, **info_overrides):
    info = {
        "agent_kind": "adapt",
        "memory_type": "adapt",
        "profile_summary": True,
        "num_trials": 1,
        "adapt_agent": {
            "proactive_loop": True,
            "candidate_evidence": False,
            "task_state": False,
        },
        "llm_agent": "a",
        "llm_user": "u",
        "llm_evaluator": "e",
    }
    info.update(info_overrides)
    return {"info": info, "tasks": tasks, "simulations": sims}


def _write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _baseline(tmp_path, tasks):
    """Baseline: same units, all failing, one trial."""
    sims = [_sim(t, 0, [0, 0, 0]) for t in tasks]
    return _write(tmp_path / "baseline.json", _checkpoint(tasks, sims))


def _run(tmp_path, arm, baseline, *extra):
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--arm",
            str(arm),
            "--baseline",
            str(baseline),
            *extra,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(REPO),
    )


def test_a_complete_arm_issues_a_verdict(tmp_path):
    tasks = list(USERS)
    base = _baseline(tmp_path, tasks)
    arm = _write(
        tmp_path / "arm.json",
        _checkpoint(tasks, [_sim(t, 0, [1, 1]) for t in tasks]),
    )
    done = _run(tmp_path, arm, base)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "ok the 8 pre-registered users" in done.stdout
    assert "ABORT" not in done.stdout
    assert "VERDICT: MEETS TARGET, UNCONFIRMED" in done.stdout


def test_a_sub_floor_arm_is_reported_as_not_resolvable(tmp_path):
    """Below half the required movement: unresolvable, never 'no effect'."""
    tasks = list(USERS)
    base = _baseline(tmp_path, tasks)
    arm = _write(
        tmp_path / "arm.json",
        _checkpoint(tasks, [_sim(t, 0, [0, 0]) for t in tasks]),
    )
    done = _run(tmp_path, arm, base)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "VERDICT: NOT RESOLVABLE" in done.stdout
    assert "does NOT show the mechanisms are inert" in done.stdout


def test_wrong_cohort_aborts(tmp_path):
    """A cohort that is not the pre-registered eight users must not be scored."""
    tasks = USERS[:2]
    base = _baseline(tmp_path, tasks)
    arm = _write(
        tmp_path / "arm.json",
        _checkpoint(tasks, [_sim(t, 0, [1, 1]) for t in tasks]),
    )
    done = _run(tmp_path, arm, base)
    assert done.returncode == 2
    assert "arm cohort is not the pre-registered 8 users" in done.stdout
    assert "VERDICT" not in done.stdout


def test_missing_user_aborts_instead_of_scoring_a_smaller_cohort(tmp_path):
    tasks = list(USERS)
    base = _baseline(tmp_path, tasks)
    arm = _write(
        tmp_path / "arm.json",
        _checkpoint(tasks, [_sim(t, 0, [1, 1]) for t in tasks[:-1]]),
    )
    done = _run(tmp_path, arm, base)
    assert done.returncode == 2
    assert "missing users" in done.stdout
    assert "U000828" in done.stdout


def test_evaluator_failure_is_reported_not_hidden(tmp_path):
    tasks = list(USERS)
    base = _baseline(tmp_path, tasks)
    sims = [_sim(t, 0, [1, 1]) for t in tasks]
    # One sim failed evaluation; its rewards are absent entirely.
    broken = {
        "task_id": "U000828",
        "trial": 0,
        "evaluation_status": "evaluation_failed",
        "reward_info": {"info": {}},
        "states": {},
    }
    sims[-1] = broken
    arm = _write(tmp_path / "arm.json", _checkpoint(tasks, sims))
    done = _run(tmp_path, arm, base)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "non-ok evaluation_status" in done.stdout
    assert "evaluation_failed" in done.stdout
    # 2 subtasks x 7 users survived; the broken user contributes no units.
    assert "arm=14" in done.stdout


def test_mechanism_counters_are_summed_and_ratio_reported(tmp_path):
    tasks = list(USERS)
    base = _baseline(tmp_path, tasks)
    sims = [_sim(t, 0, [1, 0]) for t in tasks]
    sims[0]["states"]["adapt_agent"] = {
        "enabled": True,
        "questions_committed": 2,
        "answers_linked": 2,
        "answers_resolved_to_a_value": 1,
        "candidate_evidence_enabled": False,
        "tool_results_annotated": 0,
        "candidate_constraint_pairs_resolved": 0,
        "task_state_enabled": False,
        "task_states_annotated": 0,
    }
    sims[1]["states"]["adapt_agent"] = {
        "enabled": True,
        "questions_committed": 1,
        "answers_linked": 1,
        "answers_resolved_to_a_value": 1,
        "candidate_evidence_enabled": False,
        "tool_results_annotated": 0,
        "candidate_constraint_pairs_resolved": 0,
        "task_state_enabled": False,
        "task_states_annotated": 0,
    }
    arm = _write(tmp_path / "arm.json", _checkpoint(tasks, sims))
    done = _run(tmp_path, arm, base)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "questions -> values: 2/3 (67%)" in done.stdout
    assert "no states['adapt_agent'] found" not in done.stdout
    # Booleans must not be summed into the totals.
    assert "'enabled': True" not in done.stdout.split("TOTALS")[1].splitlines()[0]


def test_no_questions_committed_is_flagged_as_a_path_that_did_not_execute(tmp_path):
    tasks = list(USERS)
    base = _baseline(tmp_path, tasks)
    sims = [_sim(t, 0, [1, 0]) for t in tasks]
    for sim in sims:
        sim["states"]["adapt_agent"] = {
            "enabled": True,
            "questions_committed": 0,
            "answers_linked": 0,
            "answers_resolved_to_a_value": 0,
        }
    arm = _write(tmp_path / "arm.json", _checkpoint(tasks, sims))
    done = _run(tmp_path, arm, base)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "no question was ever committed" in done.stdout


def test_legacy_checkpoint_without_attribution_fields_is_reported(tmp_path):
    tasks = list(USERS)
    base = _baseline(tmp_path, tasks)
    arm = _write(
        tmp_path / "arm.json",
        _checkpoint(tasks, [_sim(t, 0, [1, 0]) for t in tasks]),
    )
    done = _run(tmp_path, arm, base)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "no states['adapt_agent'] found" in done.stdout


@pytest.mark.parametrize(
    ("discordant", "expected_net"),
    [
        (6, 6),
        (8, 6),
        (9, 7),
        (10, 8),
        (12, 8),
        (15, 9),
        (18, 10),
        (24, 10),
        (30, 12),
        (40, 14),
        (60, 16),
        (100, 20),
    ],
)
def test_required_net_matches_the_pre_registered_table(discordant, expected_net):
    """The gate table in docs/ADAPT_ARM_PREREG_2026-09-13.md section 6b."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("adapt_arm_report", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.required_net(discordant) == expected_net
