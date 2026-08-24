"""External runner for stock and complete ADAPT agents on pristine VitaBench.

This module imports VitaBench as a library and never patches its source tree.
It intentionally writes the same lightweight checkpoint shape used by interrupted
VitaBench runs: ``{timestamp, info, tasks, simulations}``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterable
from copy import deepcopy
from pathlib import Path

from agent.vitabench_bootstrap import enable_vitabench_utf8

enable_vitabench_utf8()

from vita.data_model.personalization_task import PersonalizationTask
from vita.data_model.simulation import SimulationRun
from vita.domains.personalization.environment import get_tasks
from vita.orchestrator.personalization_orchestrator import PersonalizationOrchestrator
from vita.prompts import get_prompts
from vita.run import _run_personalization_task
from vita.user.personalization_user import PersonalizationUser
from vita.utils.utils import get_now

from agent.adapt_agent import ADAPTAgent
from agent.memory.adapt_memory import ADAPTMemory

SPLIT_SEED = "ADAPT-2026"


def stable_user_split(tasks: Iterable[PersonalizationTask]) -> dict[str, list[str]]:
    """Return deterministic 8-user dev, 8-user blind, and remaining final IDs."""
    ids = sorted(
        {task.id for task in tasks},
        key=lambda user_id: hashlib.sha256(
            f"{SPLIT_SEED}:{user_id}".encode()
        ).hexdigest(),
    )
    return {"dev": ids[:8], "blind": ids[8:16], "final": ids[16:], "all": ids}


def run_adapt_personalization_task(
    task: PersonalizationTask,
    *,
    llm_agent: str,
    llm_user: str,
    llm_evaluator: str | None = None,
    llm_args_agent: dict | None = None,
    llm_args_user: dict | None = None,
    llm_args_evaluator: dict | None = None,
    max_steps: int = 100,
    max_errors: int = 10,
    evaluation_type: str = "trajectory",
    seed: int | None = None,
    enable_think: bool = False,
    language: str = "chinese",
    enable_candidate_validation: bool = True,
    enable_lessons: bool = True,
    enable_tiered_compaction: bool = True,
    debug_path: Path | None = None,
) -> SimulationRun:
    """Compose ADAPTAgent with unchanged VitaBench components."""
    user_id = task.user_profile.get("user_id") if task.user_profile else task.id
    memory = ADAPTMemory(
        language=language,
        user_id=user_id,
        enable_tiered_compaction=enable_tiered_compaction,
    )
    prompts = get_prompts(language)
    time = task.subtasks[0].environment.get("time") if task.subtasks else None
    agent = ADAPTAgent(
        tools=[],
        domain_policy=prompts.personalization_agent_system_prompt,
        memory=memory,
        user_profile=task.user_profile,
        llm=llm_agent,
        llm_args=deepcopy(llm_args_agent) if llm_args_agent else {},
        time=time,
        enable_think=enable_think,
        language=language,
        enable_candidate_validation=enable_candidate_validation,
        enable_lessons=enable_lessons,
    )
    user = PersonalizationUser(
        subtasks=task.subtasks,
        persona=str(task.user_profile),
        instructions=None,
        llm=llm_user,
        llm_args=deepcopy(llm_args_user) if llm_args_user else {},
        language=language,
    )
    if seed is not None:
        agent.set_seed(seed)
        user.set_seed(seed)
    orchestrator = PersonalizationOrchestrator(
        task=task,
        agent=agent,
        user=user,
        max_steps_per_subtask=max_steps,
        max_errors=max_errors,
        seed=seed,
        evaluation_type=evaluation_type,
        llm_evaluator=llm_evaluator,
        llm_args_evaluator=deepcopy(llm_args_evaluator) if llm_args_evaluator else {},
        language=language,
        enable_outcome_reward=False,
    )
    simulation = orchestrator.run()
    if debug_path is not None:
        agent.dump_debug_trace(debug_path, append=True)
    return simulation


def run_selected(
    *,
    agent_kind: str,
    cohort: str,
    task_ids: list[str] | None,
    subtask_ids: list[str] | None,
    save_to: Path,
    llm_agent: str,
    llm_user: str,
    llm_evaluator: str | None,
    max_steps: int,
    seed: int,
    num_trials: int,
    language: str,
    enable_candidate_validation: bool,
    enable_lessons: bool,
    enable_tiered_compaction: bool = True,
    debug_to: Path | None = None,
) -> dict:
    tasks = get_tasks(language)
    split = stable_user_split(tasks)
    selected_ids = set(task_ids or split[cohort])
    selected = [task for task in tasks if task.id in selected_ids]
    if len(selected) != len(selected_ids):
        missing = sorted(selected_ids - {task.id for task in selected})
        raise ValueError(f"Unknown task ids: {missing}")
    if subtask_ids:
        requested_subtasks = set(subtask_ids)
        found_subtasks: set[str] = set()
        filtered_tasks = []
        for task in selected:
            filtered = [
                subtask
                for subtask in task.subtasks
                if subtask.subtask_id in requested_subtasks
            ]
            found_subtasks.update(subtask.subtask_id for subtask in filtered)
            if filtered:
                filtered_tasks.append(
                    task.model_copy(update={"subtasks": filtered}, deep=True)
                )
        missing_subtasks = sorted(requested_subtasks - found_subtasks)
        if missing_subtasks:
            raise ValueError(f"Unknown subtask ids: {missing_subtasks}")
        selected = filtered_tasks

    checkpoint = {
        "timestamp": get_now(),
        "info": {
            "agent_kind": agent_kind,
            "cohort": cohort,
            "split_seed": SPLIT_SEED,
            "llm_agent": llm_agent,
            "llm_user": llm_user,
            "llm_evaluator": llm_evaluator,
            "max_steps": max_steps,
            "seed": seed,
            "num_trials": num_trials,
            "candidate_validation": enable_candidate_validation,
            "lessons": enable_lessons,
            "tiered_compaction": enable_tiered_compaction,
            "debug_sidecar": str(debug_to) if debug_to else None,
            "subtask_ids": sorted(subtask_ids) if subtask_ids else None,
        },
        "tasks": sorted(selected_ids),
        "simulations": [],
    }
    save_to.parent.mkdir(parents=True, exist_ok=True)
    if save_to.exists():
        existing = json.loads(save_to.read_text(encoding="utf-8"))
        if (
            existing.get("info") != checkpoint["info"]
            or existing.get("tasks") != checkpoint["tasks"]
        ):
            raise ValueError(f"Existing checkpoint config differs: {save_to}")
        checkpoint = existing
    done = {
        (item.get("task_id"), item.get("trial"), item.get("seed"))
        for item in checkpoint["simulations"]
    }
    for trial in range(num_trials):
        trial_seed = seed + trial
        for task in selected:
            if (task.id, trial, trial_seed) in done:
                continue
            if agent_kind == "adapt":
                simulation = run_adapt_personalization_task(
                    task,
                    llm_agent=llm_agent,
                    llm_user=llm_user,
                    llm_evaluator=llm_evaluator,
                    max_steps=max_steps,
                    seed=trial_seed,
                    language=language,
                    enable_candidate_validation=enable_candidate_validation,
                    enable_lessons=enable_lessons,
                    enable_tiered_compaction=enable_tiered_compaction,
                    debug_path=debug_to,
                )
            else:
                simulation = _run_personalization_task(
                    task,
                    llm_agent=llm_agent,
                    llm_user=llm_user,
                    max_steps=max_steps,
                    seed=trial_seed,
                    llm_evaluator=llm_evaluator,
                    language=language,
                    memory_type="rewrite",
                )
            simulation.trial = trial
            checkpoint["simulations"].append(simulation.model_dump(mode="json"))
            _write_checkpoint(save_to, checkpoint)
    return checkpoint


def _write_checkpoint(path: Path, checkpoint: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=("stock", "adapt"), default="adapt")
    parser.add_argument(
        "--cohort", choices=("dev", "blind", "final", "all"), default="dev"
    )
    parser.add_argument("--task-ids", nargs="*")
    parser.add_argument(
        "--subtask-ids",
        nargs="*",
        help="development smoke only: run these visible subtask IDs",
    )
    parser.add_argument("--save-to", type=Path, required=True)
    parser.add_argument("--agent-llm", required=True)
    parser.add_argument("--user-llm", required=True)
    parser.add_argument("--evaluator-llm")
    parser.add_argument("--max-steps", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--num-trials", type=int, default=1)
    parser.add_argument("--language", default="chinese")
    parser.add_argument("--no-candidate-validation", action="store_true")
    parser.add_argument("--no-lessons", action="store_true")
    parser.add_argument("--no-tiered-compaction", action="store_true")
    parser.add_argument(
        "--debug-to", type=Path, help="append ADAPT-visible events as JSONL"
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    run_selected(
        agent_kind=args.agent,
        cohort=args.cohort,
        task_ids=args.task_ids,
        subtask_ids=args.subtask_ids,
        save_to=args.save_to,
        llm_agent=args.agent_llm,
        llm_user=args.user_llm,
        llm_evaluator=args.evaluator_llm,
        max_steps=args.max_steps,
        seed=args.seed,
        num_trials=args.num_trials,
        language=args.language,
        enable_candidate_validation=not args.no_candidate_validation,
        enable_lessons=not args.no_lessons,
        enable_tiered_compaction=not args.no_tiered_compaction,
        debug_to=args.debug_to,
    )


if __name__ == "__main__":
    main()
