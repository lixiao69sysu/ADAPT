"""External runner for stock and complete ADAPT agents on pristine VitaBench.

This module imports VitaBench as a library and never patches its source tree.
It intentionally writes the same lightweight checkpoint shape used by interrupted
VitaBench runs: ``{timestamp, info, tasks, simulations}``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections.abc import Iterable
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import MethodType

import httpx
from openai import OpenAI

from agent.vitabench_bootstrap import (
    configure_adapt_model_config,
    enable_vitabench_utf8,
)

configure_adapt_model_config()
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
_LOOPBACK_NO_PROXY = ("localhost", "127.0.0.1", "::1")


def _configure_loopback_no_proxy() -> None:
    """Keep local model traffic off Windows' registry-configured HTTP proxy."""
    existing = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    entries = [item.strip() for item in existing.split(",") if item.strip()]
    lowered = {item.lower() for item in entries}
    for host in _LOOPBACK_NO_PROXY:
        if host.lower() not in lowered:
            entries.append(host)
            lowered.add(host.lower())
    value = ",".join(entries)
    # urllib/httpx environment discovery is case-insensitive on Windows, but
    # setting both forms also makes the behavior stable on Linux runners.
    os.environ["NO_PROXY"] = value
    os.environ["no_proxy"] = value


def _install_evaluator_transport_probe(model: str, output: Path) -> None:
    """Log transport metadata for evaluator calls without reading request text."""
    from vita.config import DEFAULT_MAX_RETRIES, models
    from vita.utils import llm_utils

    config = dict(models.get(model, {}))
    base_url = config.get("base_url")
    api_key = config.get("api_key")
    if not base_url or not api_key:
        raise ValueError(f"Missing transport configuration for evaluator {model}")
    output.parent.mkdir(parents=True, exist_ok=True)
    sequence = 0

    def append(entry: dict) -> None:
        with output.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def on_request(request: httpx.Request) -> None:
        nonlocal sequence
        sequence += 1
        request.extensions["adapt_probe_sequence"] = sequence
        request.extensions["adapt_probe_started"] = time.monotonic()
        declared = request.headers.get("content-length")
        append(
            {
                "event": "request",
                "sequence": sequence,
                "method": request.method,
                "path": request.url.path,
                "content_length": int(declared) if declared else None,
            }
        )

    def on_response(response: httpx.Response) -> None:
        response.read()
        started = response.request.extensions.get("adapt_probe_started")
        entry = {
            "event": "response",
            "sequence": response.request.extensions.get("adapt_probe_sequence"),
            "status": response.status_code,
            "response_length": len(response.content),
            "elapsed_ms": (
                round((time.monotonic() - started) * 1000)
                if isinstance(started, float)
                else None
            ),
        }
        if response.status_code >= 400:
            # Error responses contain transport/model diagnostics, not the
            # request.  Keep a short preview and never log headers or content.
            entry["error_preview"] = response.text[:500]
        append(entry)

    class ProbeTransport(httpx.BaseTransport):
        def __init__(self) -> None:
            self.inner = httpx.HTTPTransport(retries=0)

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            started = time.monotonic()
            try:
                return self.inner.handle_request(request)
            except Exception as exc:
                append(
                    {
                        "event": "transport_exception",
                        "sequence": request.extensions.get("adapt_probe_sequence"),
                        "exception_type": type(exc).__name__,
                        "exception": repr(exc)[:500],
                        "cause": repr(exc.__cause__)[:500],
                        "elapsed_ms": round((time.monotonic() - started) * 1000),
                    }
                )
                raise

        def close(self) -> None:
            self.inner.close()

    http_client = httpx.Client(
        timeout=httpx.Timeout(600.0, connect=5.0),
        transport=ProbeTransport(),
        event_hooks={"request": [on_request], "response": [on_response]},
    )
    client = OpenAI(
        base_url=base_url,
        api_key=api_key,
        max_retries=DEFAULT_MAX_RETRIES,
        http_client=http_client,
    )
    llm_utils._CLIENT_CACHE[(base_url, api_key, DEFAULT_MAX_RETRIES)] = client


class EvaluationIntegrityError(RuntimeError):
    """Raised when evaluator transport/model failure would become a fake zero."""


def _evaluation_error_note(reward_info) -> str:
    info = getattr(reward_info, "info", None) or {}
    note = str(info.get("note", "")) if isinstance(info, dict) else ""
    return note if note.startswith("Evaluation error:") else ""


def _install_evaluation_fail_fast(orchestrator) -> None:
    """Stop an external run before an evaluator error is cached as reward 0.

    VitaBench deliberately converts evaluator exceptions into ``RewardInfo`` so
    a broad benchmark run can continue.  For an expensive controlled comparison
    that behavior corrupts the checkpoint: infrastructure failure is not agent
    failure.  Wrap only this runner's orchestrator instance and leave VitaBench
    source and evaluator inputs untouched.
    """
    original = orchestrator._evaluate_subtask

    def checked(self, subtask, subtask_result):
        reward_info = original(subtask, subtask_result)
        note = _evaluation_error_note(reward_info)
        if note:
            subtask_id = getattr(subtask, "subtask_id", "unknown")
            raise EvaluationIntegrityError(f"{subtask_id}: {note}")
        return reward_info

    orchestrator._evaluate_subtask = MethodType(checked, orchestrator)


@contextmanager
def _stock_evaluation_fail_fast():
    """Apply the same integrity gate while VitaBench constructs stock objects."""
    original = PersonalizationOrchestrator._evaluate_subtask

    def checked(self, subtask, subtask_result):
        reward_info = original(self, subtask, subtask_result)
        note = _evaluation_error_note(reward_info)
        if note:
            subtask_id = getattr(subtask, "subtask_id", "unknown")
            raise EvaluationIntegrityError(f"{subtask_id}: {note}")
        return reward_info

    PersonalizationOrchestrator._evaluate_subtask = checked
    try:
        yield
    finally:
        PersonalizationOrchestrator._evaluate_subtask = original


def _assert_simulation_evaluation_integrity(simulation) -> None:
    """Reject completed stock runs containing evaluator-generated fake zeros."""
    reward_info = getattr(simulation, "reward_info", None)
    note = _evaluation_error_note(reward_info)
    if note:
        raise EvaluationIntegrityError(note)
    for index, item in enumerate(getattr(simulation, "subtask_results", None) or []):
        if isinstance(item, dict):
            reward = item.get("reward_info") or item.get("reward")
        else:
            reward = getattr(item, "reward_info", None) or getattr(item, "reward", None)
        note = _evaluation_error_note(reward)
        if note:
            raise EvaluationIntegrityError(f"subtask[{index}]: {note}")


def implementation_fingerprint() -> str:
    """Hash runtime code/config so incompatible checkpoints cannot resume."""
    root = Path(__file__).resolve().parents[1]
    files = [
        path
        for path in (root / "agent").rglob("*.py")
        if "tests" not in path.parts and "__pycache__" not in path.parts
    ]
    files.extend(
        path
        for pattern in ("models*.yaml", "memory*.yaml")
        for path in root.glob(pattern)
    )
    digest = hashlib.sha256()
    for path in sorted(set(files)):
        digest.update(str(path.relative_to(root)).replace("\\", "/").encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:20]


def external_model_config_fingerprint() -> str | None:
    """Fingerprint an explicitly selected model config without exposing it."""
    configured = os.environ.get("VITA_MODEL_CONFIG_PATH") or os.environ.get(
        "VITA_MODEL_CONFIG"
    )
    if configured:
        path = Path(configured)
    else:
        try:
            from vita.config import _models_yaml_path

            path = Path(_models_yaml_path)
        except (ImportError, TypeError, ValueError):
            return None
    return hashlib.sha256(path.read_bytes()).hexdigest()[:20] if path.is_file() else None


def _assert_loaded_external_model_config() -> None:
    """Fail before evaluation if Vita config was imported from another file."""
    from vita.config import _models_yaml_path

    expected = Path(os.environ["VITA_MODEL_CONFIG_PATH"]).resolve()
    loaded = Path(_models_yaml_path).resolve()
    if loaded != expected:
        raise RuntimeError(
            f"Vita model config already loaded from {loaded}; expected {expected}. "
            "Start evaluation through `python -m agent.vitabench_runner`."
        )


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
    run_context: dict | None = None,
) -> SimulationRun:
    """Compose ADAPTAgent with unchanged VitaBench components."""
    _configure_loopback_no_proxy()
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
    if run_context:
        agent.debug.context.update(run_context)
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
    _install_evaluation_fail_fast(orchestrator)
    try:
        simulation = orchestrator.run()
    finally:
        # Preserve public ADAPT evidence even when the evaluator gate aborts.
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
    evaluator_transport_to: Path | None = None,
) -> dict:
    _configure_loopback_no_proxy()
    _assert_loaded_external_model_config()
    if evaluator_transport_to is not None and llm_evaluator:
        _install_evaluator_transport_probe(llm_evaluator, evaluator_transport_to)
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

    code_fingerprint = implementation_fingerprint()
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
            "evaluator_transport_sidecar": (
                str(evaluator_transport_to) if evaluator_transport_to else None
            ),
            "subtask_ids": sorted(subtask_ids) if subtask_ids else None,
            "implementation_fingerprint": code_fingerprint,
            "model_config_fingerprint": external_model_config_fingerprint(),
        },
        "tasks": sorted(task.id for task in selected),
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
                    run_context={
                        "task_id": task.id,
                        "trial": trial,
                        "seed": trial_seed,
                        "implementation_fingerprint": code_fingerprint,
                    },
                )
            else:
                with _stock_evaluation_fail_fast():
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
                _assert_simulation_evaluation_integrity(simulation)
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
    parser.add_argument(
        "--evaluator-transport-to",
        type=Path,
        help="append evaluator HTTP metadata only; never logs request content",
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
        evaluator_transport_to=args.evaluator_transport_to,
    )


if __name__ == "__main__":
    main()
