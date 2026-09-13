"""Zero-model tests for the external harness's reliability guarantees.

Carved out of `test_adapt_v2.py` when ADAPT V2 was deleted: these two tests never
touched V2, they test the pristine-VitaBench wrappers that remain --
`IntegrityPersonalizationOrchestrator` (a vendored evaluator transport error must
not be recorded as a legitimate zero, E-037) and `agent.trace_metrics` (which must
exclude such a simulation from the scoreable set).

No API call is made.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from vita.data_model.simulation import RewardInfo
from vita.orchestrator.personalization_orchestrator import PersonalizationOrchestrator

from agent.evaluation_integrity import IntegrityPersonalizationOrchestrator
from agent.trace_metrics import summarize


def test_evaluator_retry_accepts_genuine_zero_and_tracks_attempts(monkeypatch):
    responses = [
        RewardInfo(reward=0.0, info={"note": "Evaluation error: HTTP 502"}),
        RewardInfo(reward=0.0, info={"note": "Evaluation error: timeout"}),
        RewardInfo(reward=0.0, info={"note": "genuine rubric failure"}),
    ]

    def fake_evaluate(self, subtask, result):
        return responses.pop(0)

    monkeypatch.setattr(PersonalizationOrchestrator, "_evaluate_subtask", fake_evaluate)
    orchestrator = IntegrityPersonalizationOrchestrator(
        task=SimpleNamespace(subtasks=[]),
        agent=object(),
        user=object(),
        evaluator_retries=2,
        evaluator_retry_backoff_seconds=0,
    )
    reward = orchestrator._evaluate_subtask(SimpleNamespace(subtask_id="s1"), {})

    assert reward.reward == 0.0
    assert orchestrator.evaluation_records == [
        {
            "subtask_id": "s1",
            "evaluation_status": "ok",
            "evaluation_attempts": 3,
            "evaluator_error": None,
        }
    ]


def test_trace_metrics_excludes_evaluator_failure(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text(
        json.dumps(
            {
                "info": {"agent_kind": "stock", "cohort": "dev"},
                "simulations": [
                    {
                        "task_id": "good",
                        "trial": 0,
                        "seed": 42,
                        "evaluation_status": "ok",
                        "reward_info": {"reward": 1.0},
                        "messages": [],
                    },
                    {
                        "task_id": "bad-evaluator",
                        "trial": 0,
                        "seed": 42,
                        "evaluation_status": "evaluation_failed",
                        "reward_info": {"reward": 0.0},
                        "messages": [],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    metrics = summarize(path)

    assert metrics["avg_reward"] == 1.0
    assert metrics["num_scoreable"] == 1
    assert metrics["evaluation_failed"] == 1
