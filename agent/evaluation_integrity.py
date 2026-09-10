"""External evaluator-integrity safeguards for pristine VitaBench.

The upstream orchestrator converts evaluator transport exceptions into a real
zero reward. This adapter retries only evaluation of the already-completed
trajectory, records the distinction, and removes contaminated rewards from the
scoreable simulation while preserving the trajectory for later re-evaluation.
"""

from __future__ import annotations

import time
from copy import deepcopy
from typing import Any

from pydantic import TypeAdapter
from vita.data_model.message import Message
from vita.data_model.simulation import RewardInfo, SimulationRun
from vita.orchestrator.personalization_orchestrator import PersonalizationOrchestrator


class EvaluationIntegrityError(RuntimeError):
    """Raised when a saved item cannot be re-evaluated without agent replay."""


def evaluator_error_note(reward_info: RewardInfo | None) -> str | None:
    """Recognize infrastructure failure without misclassifying a genuine zero."""
    if reward_info is None:
        return "Evaluator returned no RewardInfo"
    info: Any = getattr(reward_info, "info", None)
    if not isinstance(info, dict):
        return None
    if info.get("evaluation_status") == "evaluation_failed":
        return str(info.get("evaluator_error") or info.get("note") or "evaluation failed")
    note = info.get("note")
    if isinstance(note, str) and note.startswith("Evaluation error:"):
        return note
    return None


def is_evaluation_failed(reward_info: RewardInfo | None) -> bool:
    return evaluator_error_note(reward_info) is not None


def normalize_evaluator_result(parsed: Any) -> list:
    """Normalize a parsed evaluator JSON payload into a flat list of dicts.

    The vendored window-evaluation loop iterates the parsed payload and calls
    ``.get("rubric_idx")`` on each item. Evaluator outputs can be
    double-wrapped arrays, lone objects, or lists containing stray scalars;
    those would crash the vendored parser ("'list'/'int' object has no
    attribute 'get'") and make the whole subtask unscoreable. This helper
    recursively collects every dict item (bounded depth) and drops non-dict
    entries. Valid lists of rubric-update dicts pass through unchanged.
    """
    flat: list = []

    def visit(item: Any, depth: int) -> None:
        if isinstance(item, dict):
            sanitized = dict(item)
            value = sanitized.get("meetExpectation")
            if isinstance(value, str) and value.strip().lower() in ("true", "false"):
                sanitized["meetExpectation"] = value.strip().lower() == "true"
            elif not isinstance(value, bool):
                # The vendored code stores whatever value the model returned
                # and later validates NLRubricCheck(met=...); a non-bool would
                # crash the whole subtask. Dropping the key makes the vendored
                # code keep the previous rubric state instead.
                sanitized.pop("meetExpectation", None)
            flat.append(sanitized)
        elif isinstance(item, list) and depth < 6:
            for sub in item:
                visit(sub, depth + 1)

    visit(parsed, 0)
    return flat


def patch_evaluator_extracter() -> Any:
    """Temporarily replace the vendored extracter with a hardened version.

    Returns the previous function so callers can restore it. Both module-level
    bindings are patched (utils and evaluator_traj import the name directly).
    """
    import vita.evaluator.evaluator_traj as evaluator_traj
    import vita.utils.utils as vita_utils

    original = vita_utils.evaluator_extracter

    def hardened(content: str) -> list:
        return normalize_evaluator_result(original(content))

    vita_utils.evaluator_extracter = hardened
    evaluator_traj.evaluator_extracter = hardened
    return original


def restore_evaluator_extracter(original: Any) -> None:
    import vita.evaluator.evaluator_traj as evaluator_traj
    import vita.utils.utils as vita_utils

    vita_utils.evaluator_extracter = original
    evaluator_traj.evaluator_extracter = original


class IntegrityPersonalizationOrchestrator(PersonalizationOrchestrator):
    """Retry scoring without replaying tools and preserve failed trajectories."""

    def __init__(
        self,
        *args: Any,
        evaluator_retries: int = 2,
        evaluator_retry_backoff_seconds: float = 1.0,
        **kwargs: Any,
    ) -> None:
        if evaluator_retries < 0:
            raise ValueError("evaluator_retries must be non-negative")
        if evaluator_retry_backoff_seconds < 0:
            raise ValueError("evaluator_retry_backoff_seconds must be non-negative")
        super().__init__(*args, **kwargs)
        self.evaluator_retries = evaluator_retries
        self.evaluator_retry_backoff_seconds = evaluator_retry_backoff_seconds
        self.evaluation_records: list[dict[str, Any]] = []
        self.saved_subtask_trajectories: list[dict[str, Any]] = []

    def _run_subtask(self, subtask: Any, environment: Any, subtask_idx: int) -> dict[str, Any]:
        result = super()._run_subtask(subtask, environment, subtask_idx)
        # Deep-copy before evaluation. Retrying or serializing this object never
        # calls the agent or the environment again.
        self.saved_subtask_trajectories.append(deepcopy(result))
        return result

    def _evaluate_subtask(self, subtask: Any, subtask_result: dict[str, Any]) -> RewardInfo:
        last_note = "unknown evaluator failure"
        attempts = 0
        for attempt in range(self.evaluator_retries + 1):
            attempts = attempt + 1
            reward_info = super()._evaluate_subtask(subtask, subtask_result)
            note = evaluator_error_note(reward_info)
            if note is None:
                self.evaluation_records.append(
                    {
                        "subtask_id": subtask.subtask_id,
                        "evaluation_status": "ok",
                        "evaluation_attempts": attempts,
                        "evaluator_error": None,
                    }
                )
                return reward_info
            last_note = note
            if attempt < self.evaluator_retries:
                delay = self.evaluator_retry_backoff_seconds * (attempt + 1)
                if delay:
                    time.sleep(delay)

        self.evaluation_records.append(
            {
                "subtask_id": subtask.subtask_id,
                "evaluation_status": "evaluation_failed",
                "evaluation_attempts": attempts,
                "evaluator_error": last_note,
            }
        )
        return RewardInfo(
            reward=0.0,
            info={
                "evaluation_status": "evaluation_failed",
                "evaluation_attempts": attempts,
                "evaluator_error": last_note,
                "note": "Evaluation unavailable; this is not an agent reward.",
            },
        )

    def _aggregate_rewards(self, reward_infos: list[RewardInfo | None]) -> RewardInfo:
        # The whole simulation is later marked unscoreable if any evaluated
        # subtask failed. Filtering here also prevents a transport error from
        # lowering the diagnostic aggregate printed by the stock orchestrator.
        trustworthy = [
            reward if reward is None or not is_evaluation_failed(reward) else None
            for reward in reward_infos
        ]
        return super()._aggregate_rewards(trustworthy)

    def run(self) -> SimulationRun:
        simulation = super().run()
        summary = self.integrity_summary()
        simulation.states["evaluation_integrity"] = summary
        simulation.states["integrity_subtask_trajectories"] = self.saved_subtask_trajectories
        if summary["evaluation_status"] == "evaluation_failed":
            # A missing reward cannot be mistaken for a legitimate zero by
            # normal metric readers.
            simulation.reward_info = None
        return simulation

    def integrity_summary(self) -> dict[str, Any]:
        failed = [
            item
            for item in self.evaluation_records
            if item["evaluation_status"] == "evaluation_failed"
        ]
        return {
            "evaluation_status": "evaluation_failed" if failed else "ok",
            "evaluation_attempts": sum(
                int(item["evaluation_attempts"]) for item in self.evaluation_records
            ),
            "evaluator_error": (
                "; ".join(str(item["evaluator_error"]) for item in failed) or None
            ),
            "subtasks": deepcopy(self.evaluation_records),
        }

    def reevaluate_saved(
        self,
        simulation: SimulationRun,
        trajectories: list[dict[str, Any]],
    ) -> SimulationRun:
        """Score serialized subtask trajectories without executing the agent."""
        by_id = {str(item.get("subtask_id")): item for item in trajectories}
        rewards: list[RewardInfo | None] = []
        self.evaluation_records.clear()
        adapter = TypeAdapter(Message)
        for subtask in self.task.subtasks:
            saved = by_id.get(str(subtask.subtask_id))
            if saved is None:
                raise EvaluationIntegrityError(
                    f"Saved trajectory missing subtask {subtask.subtask_id}; agent replay refused"
                )
            restored = deepcopy(saved)
            restored["messages"] = [
                adapter.validate_python(message) for message in restored.get("messages", [])
            ]
            if subtask.evaluation_criteria is None:
                rewards.append(None)
            else:
                rewards.append(self._evaluate_subtask(subtask, restored))
        summary = self.integrity_summary()
        simulation.states["evaluation_integrity"] = summary
        simulation.reward_info = (
            None
            if summary["evaluation_status"] == "evaluation_failed"
            else self._aggregate_rewards(rewards)
        )
        return simulation
