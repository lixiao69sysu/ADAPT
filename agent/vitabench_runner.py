"""External runner for stock and complete ADAPT agents on pristine VitaBench.

This module imports VitaBench as a library and never patches its source tree.
It intentionally writes the same lightweight checkpoint shape used by interrupted
VitaBench runs: ``{timestamp, info, tasks, simulations}``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Iterable
from copy import deepcopy
from pathlib import Path

from loguru import logger

from agent.vitabench_bootstrap import enable_vitabench_utf8

enable_vitabench_utf8()

from vita.agent.personalization_agent import PersonalizationAgent
from vita.config import models
from vita.data_model.personalization_task import PersonalizationTask
from vita.data_model.simulation import SimulationRun
from vita.domains.personalization.environment import get_tasks
from vita.memory.groundtruth_memory import GroundtruthMemory
from vita.memory.rewrite_memory import RewriteMemory
from vita.prompts import get_prompts
from vita.user.personalization_user import PersonalizationUser
from vita.utils.utils import get_now

import vita.agent.llm_agent as llm_agent_module
import vita.memory.rewrite_memory as rewrite_memory_module
import vita.utils.llm_utils as llm_utils

from agent.adapt_agent import AdaptAgent
from agent.evaluation_integrity import (
    IntegrityPersonalizationOrchestrator,
    patch_evaluator_extracter,
)
from agent.memory.adapt_memory import ADAPTMemory

SPLIT_SEED = "ADAPT-2026"

# Identity of the one ADAPT agent implementation, recorded in the checkpoint so
# a resumed run cannot silently mix agent versions.
ADAPT_AGENT_VERSION = "adapt_agent_v1"


def runtime_fingerprint() -> str:
    """Freeze executable sources, excluding tests and generated artifacts."""
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for source in sorted(root.rglob("*.py")):
        if "tests" in source.relative_to(root).parts:
            continue
        digest.update(source.relative_to(root).as_posix().encode("utf-8"))
        digest.update(source.read_bytes())
    return digest.hexdigest()


def model_settings(names: list[str | None]) -> dict:
    """Persist effective inference settings without credentials."""
    keys = {"temperature", "max_tokens", "max_input_tokens", "extra_body", "base_url", "model"}
    return {name: {k: v for k, v in (models.get(name, {}) or {}).items() if k in keys}
            for name in names if name}

_AGENT_OVERFLOW_RE = re.compile(
    r"maximum context length is (\d+) tokens.*?"
    r"you requested (\d+) output tokens.*?"
    r"prompt contains at least (\d+) input tokens",
    re.DOTALL,
)


def _agent_overflow_numbers(exc: Exception) -> tuple[int, int, int] | None:
    response = getattr(exc, "response", None)
    try:
        body = response.json()
    except Exception:
        body = None
    text = ""
    if isinstance(body, dict):
        err = body.get("error") or {}
        text = str(err.get("message", "")) or str(body)
    if not text:
        text = str(exc)
    match = _AGENT_OVERFLOW_RE.search(text)
    if not match:
        return None
    return tuple(int(group) for group in match.groups())


def _count_tokens_vllm(model: str, text: str, base_url: str) -> int:
    """Count tokens via the vLLM native /tokenize endpoint; 0 on any failure."""
    import urllib.error
    import urllib.request

    try:
        tokenize_url = base_url.rstrip("/").rsplit("/v1", 1)[0] + "/tokenize"
        request = urllib.request.Request(
            tokenize_url,
            data=json.dumps({"model": model, "prompt": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return int(payload.get("count") or 0)
    except Exception:
        return 0


def _concat_message_content(messages) -> str:
    parts = []
    for message in messages:
        content = (
            message.get("content")
            if isinstance(message, dict)
            else getattr(message, "content", None) or ""
        )
        parts.append(content)
    return "\n".join(parts)


def _trim_tool_messages(messages, cap_chars: int):
    """Truncate the content of every tool message to cap_chars (keep the head).

    Tool messages carry environment payloads (search results, order dumps)
    that dominate long conversations; their head is the most relevant part.
    User, assistant and system messages are never touched.
    """
    adjusted = []
    for message in messages:
        if isinstance(message, dict):
            role = message.get("role")
            content = message.get("content")
            if role == "tool" and isinstance(content, str) and len(content) > cap_chars:
                message = {
                    **message,
                    "content": content[:cap_chars] + "\n...[truncated by agent-context-guard]",
                }
        else:
            role = getattr(message, "role", None)
            content = getattr(message, "content", None)
            if role == "tool" and isinstance(content, str) and len(content) > cap_chars:
                message = message.model_copy(
                    update={
                        "content": content[:cap_chars]
                        + "\n...[truncated by agent-context-guard]"
                    }
                )
        adjusted.append(message)
    return adjusted


def make_agent_context_guard(original_generate, agent_llm: str):
    """Wrap generate() for the agent model with a context-overflow safety trim.

    Delegates untouched unless the server rejects the prompt with a
    "maximum context length" 400. In that case it measures the true token
    count with /tokenize, then progressively truncates old tool-message
    payloads until the prompt fits, and retries. Only the agent model is
    guarded; the user simulator and evaluator paths are left untouched.
    """

    def guarded_generate(*args, **kwargs):
        model = kwargs.get("model") or (args[0] if args else None)
        if model != agent_llm:
            return original_generate(*args, **kwargs)
        messages = kwargs.get("messages") or (args[1] if len(args) > 1 else None)
        if not messages:
            return original_generate(*args, **kwargs)
        try:
            return original_generate(*args, **kwargs)
        except Exception as exc:
            parsed = _agent_overflow_numbers(exc)
            if parsed is None:
                raise
            max_len, output_tokens, _ = parsed
            cfg = models.get(model, {}) or {}
            requested = (
                kwargs.get("max_tokens")
                or cfg.get("max_tokens")
                or output_tokens
                or 0
            )
            budget = max_len - int(requested) - 1024
            base_url = cfg.get("base_url", "")
            count = (
                _count_tokens_vllm(model, _concat_message_content(messages), base_url)
                if base_url
                else 0
            )
            cap = 8000
            for _ in range(7):
                trimmed = _trim_tool_messages(messages, cap)
                new_count = (
                    _count_tokens_vllm(
                        model, _concat_message_content(trimmed), base_url
                    )
                    if base_url
                    else 0
                )
                print(
                    f"[agent-context-guard] model={model} count={count} "
                    f"new={new_count} budget={budget} cap={cap}",
                    flush=True,
                )
                retry_kwargs = dict(kwargs)
                retry_kwargs["model"] = model
                retry_kwargs["messages"] = trimmed
                try:
                    return original_generate(**retry_kwargs)
                except Exception as retry_exc:
                    if _agent_overflow_numbers(retry_exc) is None:
                        raise
                cap = cap // 2
            raise RuntimeError(f"agent context overflow not resolved: {exc}")

    return guarded_generate


def stable_user_split(tasks: Iterable[PersonalizationTask]) -> dict[str, list[str]]:
    """Return deterministic 8-user dev, 8-user blind, and remaining final IDs."""
    ids = sorted(
        {task.id for task in tasks},
        key=lambda user_id: hashlib.sha256(
            f"{SPLIT_SEED}:{user_id}".encode()
        ).hexdigest(),
    )
    return {"dev": ids[:8], "blind": ids[8:16], "final": ids[16:], "all": ids}


def run_stock_personalization_task(
    task: PersonalizationTask,
    *,
    agent_kind: str,
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
    memory_type: str = "rewrite",
    evaluator_retries: int = 2,
    evaluator_retry_backoff_seconds: float = 1.0,
    debug_path: Path | None = None,
    agent_context_guard: bool = True,
    enable_profile_summary: bool = False,
    summary_max_chars: int = 800,
    enable_proactive_loop: bool = False,
    enable_candidate_evidence: bool = False,
    enable_task_state: bool = False,
) -> SimulationRun:
    """Run the stock skeleton through the integrity-aware harness."""
    if agent_kind not in {"stock", "adapt"}:
        raise ValueError(f"Unsupported agent kind: {agent_kind}")
    user_id = task.user_profile.get("user_id") if task.user_profile else task.id
    if memory_type == "groundtruth":
        memory = GroundtruthMemory(language=language, user_id=user_id)
    elif memory_type == "rewrite":
        memory = RewriteMemory(language=language, user_id=user_id)
    elif memory_type == "adapt":
        # Isolation rig (E-046): the stock agent, unchanged, but with ADAPT's
        # memory backend. This isolates the cost of the memory *representation*
        # from every control-layer difference.
        memory = ADAPTMemory(
            language=language,
            user_id=user_id,
            enable_summary_rewrite=enable_profile_summary,
            summary_max_chars=summary_max_chars,
        )
    else:
        raise ValueError(f"Unsupported memory type: {memory_type}")
    prompts = get_prompts(language)
    time = task.subtasks[0].environment.get("time") if task.subtasks else None
    common_agent_args = {
        "tools": [],
        "domain_policy": prompts.personalization_agent_system_prompt,
        "memory": memory,
        "user_profile": task.user_profile,
        "llm": llm_agent,
        "llm_args": deepcopy(llm_args_agent) if llm_args_agent else {},
        "time": time,
        "enable_think": enable_think,
        "language": language,
    }
    if agent_kind == "adapt":
        # Each ADAPT mechanism sits behind its own off-by-default switch so its
        # effect is measured against a clean pass-through base (E-086, E-087).
        agent = AdaptAgent(
            **common_agent_args,
            enable_proactive_loop=enable_proactive_loop,
            enable_candidate_evidence=enable_candidate_evidence,
            enable_task_state=enable_task_state,
        )
    else:
        agent = PersonalizationAgent(**common_agent_args)
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
    orchestrator = IntegrityPersonalizationOrchestrator(
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
        evaluator_retries=evaluator_retries,
        evaluator_retry_backoff_seconds=evaluator_retry_backoff_seconds,
    )
    simulation = None
    originals = None
    if agent_context_guard:
        originals = (
            llm_utils.generate,
            llm_agent_module.generate,
            rewrite_memory_module.generate,
        )
        guarded = make_agent_context_guard(llm_utils.generate, llm_agent)
        llm_utils.generate = guarded
        llm_agent_module.generate = guarded
        rewrite_memory_module.generate = guarded
    try:
        simulation = orchestrator.run()
    finally:
        if originals is not None:
            llm_utils.generate, llm_agent_module.generate, rewrite_memory_module.generate = originals
    if isinstance(agent, AdaptAgent):
        # Per-subtask attribution: a single simulation-level block cannot say
        # which unit asked or answered.
        simulation.states["adapt_agent"] = agent.loop_events
    return simulation


def _run_one_simulation(
    task: PersonalizationTask,
    *,
    agent_kind: str,
    llm_agent: str,
    llm_user: str,
    llm_evaluator: str | None,
    evaluator_args: dict | None,
    max_steps: int,
    seed: int,
    language: str,
    evaluator_retries: int,
    evaluator_retry_backoff_seconds: float,
    memory_type: str,
    debug_path: Path | None,
    agent_context_guard: bool,
    enable_profile_summary: bool = False,
    summary_max_chars: int = 800,
    enable_proactive_loop: bool = False,
    enable_candidate_evidence: bool = False,
    enable_task_state: bool = False,
) -> SimulationRun:
    return run_stock_personalization_task(        task,
        agent_kind=agent_kind,
        llm_agent=llm_agent,
        llm_user=llm_user,
        max_steps=max_steps,
        seed=seed,
        llm_evaluator=llm_evaluator,
        llm_args_evaluator=evaluator_args,
        language=language,
        memory_type=memory_type,
        evaluator_retries=evaluator_retries,
        evaluator_retry_backoff_seconds=evaluator_retry_backoff_seconds,
        debug_path=debug_path,
        agent_context_guard=agent_context_guard,
        enable_profile_summary=enable_profile_summary,
        summary_max_chars=summary_max_chars,
        enable_proactive_loop=enable_proactive_loop,
        enable_candidate_evidence=enable_candidate_evidence,
        enable_task_state=enable_task_state,
    )


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
    memory_type: str = "rewrite",
    evaluator_retries: int = 2,
    evaluator_retry_backoff_seconds: float = 1.0,
    debug_to: Path | None = None,
    agent_context_guard: bool = True,
    enable_profile_summary: bool = False,
    summary_max_chars: int = 800,
    enable_proactive_loop: bool = False,
    enable_candidate_evidence: bool = False,
    enable_task_state: bool = False,
) -> dict:
    tasks = get_tasks(language)
    split = stable_user_split(tasks)
    selected_ids = set(task_ids or split[cohort])
    selected = [task for task in tasks if task.id in selected_ids]
    if len(selected) != len(selected_ids):
        missing = sorted(selected_ids - {task.id for task in tasks})
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
            "implementation": (
                ADAPT_AGENT_VERSION if agent_kind == "adapt" else "stock"
            ),
            "runtime_fingerprint": runtime_fingerprint(),
            "model_settings": model_settings([llm_agent, llm_user, llm_evaluator]),
            "summary_max_chars": summary_max_chars,
            "cohort": cohort,
            "split_seed": SPLIT_SEED,
            "llm_agent": llm_agent,
            "llm_user": llm_user,
            "llm_evaluator": llm_evaluator,
            "max_steps": max_steps,
            "seed": seed,
            "num_trials": num_trials,
            "memory_type": memory_type,
            "profile_summary": enable_profile_summary,
            "adapt_agent": {
                "proactive_loop": enable_proactive_loop,
                "candidate_evidence": enable_candidate_evidence,
                "task_state": enable_task_state,
            },
            "feature_flags": {},
            "evaluator_retries": evaluator_retries,
            "evaluator_retry_backoff_seconds": evaluator_retry_backoff_seconds,
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
    evaluator_args = (
        deepcopy(models[llm_evaluator]) if llm_evaluator is not None else None
    )
    for trial in range(num_trials):
        trial_seed = seed + trial
        for task in selected:
            if (task.id, trial, trial_seed) in done:
                continue
            try:
                simulation = _run_one_simulation(
                    task,
                    agent_kind=agent_kind,
                    llm_agent=llm_agent,
                    llm_user=llm_user,
                    llm_evaluator=llm_evaluator,
                    evaluator_args=evaluator_args,
                    max_steps=max_steps,
                    seed=trial_seed,
                    language=language,
                    evaluator_retries=evaluator_retries,
                    evaluator_retry_backoff_seconds=evaluator_retry_backoff_seconds,
                    memory_type=memory_type,
                    debug_path=debug_to,
                    agent_context_guard=agent_context_guard,
                    enable_profile_summary=enable_profile_summary,
                    summary_max_chars=summary_max_chars,
                    enable_proactive_loop=enable_proactive_loop,
                    enable_candidate_evidence=enable_candidate_evidence,
                    enable_task_state=enable_task_state,
                )
            except Exception:
                # One user crashing (e.g. agent context overflow) must not
                # kill the whole batch. Skip the user so the rest completes;
                # it stays unrecorded and can be retried by resuming the run.
                logger.exception(f"Run failed for {task.id}; skipping user")
                continue
            simulation.trial = trial
            serialized = simulation.model_dump(mode="json")
            integrity = (serialized.get("states") or {}).get(
                "evaluation_integrity", {}
            )
            serialized.update(
                {
                    "evaluation_status": integrity.get("evaluation_status", "ok"),
                    "evaluation_attempts": integrity.get("evaluation_attempts", 0),
                    "evaluator_error": integrity.get("evaluator_error"),
                    "trajectory_hash": _trajectory_hash(serialized),
                    "agent_version": (
                        ADAPT_AGENT_VERSION if agent_kind == "adapt" else agent_kind
                    ),
                    "feature_flags": {},
                }
            )
            checkpoint["simulations"].append(serialized)
            _write_checkpoint(save_to, checkpoint)
    return checkpoint


def _write_checkpoint(path: Path, checkpoint: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def _trajectory_hash(simulation: dict) -> str:
    observable = {
        "task_id": simulation.get("task_id"),
        "seed": simulation.get("seed"),
        "messages": simulation.get("messages", []),
        "old_states": (simulation.get("states") or {}).get("old_states", []),
        "new_states": (simulation.get("states") or {}).get("new_states", []),
    }
    return hashlib.sha256(
        json.dumps(observable, ensure_ascii=False, sort_keys=True, default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def reevaluate_checkpoint(
    path: Path,
    *,
    llm_evaluator: str,
    language: str = "chinese",
    evaluator_retries: int = 2,
    evaluator_retry_backoff_seconds: float = 1.0,
) -> dict:
    """Re-score failed saved trajectories in place without replaying the agent."""
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    tasks = {task.id: task for task in get_tasks(language)}
    evaluator_args = deepcopy(models[llm_evaluator])
    changed = False
    for item in checkpoint.get("simulations", []):
        if item.get("evaluation_status") != "evaluation_failed":
            continue
        task = tasks.get(str(item.get("task_id")))
        if task is None:
            raise ValueError(f"Unknown task in checkpoint: {item.get('task_id')}")
        trajectories = (item.get("states") or {}).get(
            "integrity_subtask_trajectories"
        )
        if not trajectories:
            raise ValueError(
                "Checkpoint predates integrity trajectory preservation; "
                f"cannot re-evaluate {task.id} without an agent replay"
            )
        saved_ids = {str(entry.get("subtask_id")) for entry in trajectories}
        filtered = [
            subtask for subtask in task.subtasks if str(subtask.subtask_id) in saved_ids
        ]
        task = task.model_copy(update={"subtasks": filtered}, deep=True)
        time = task.subtasks[0].environment.get("time") if task.subtasks else None
        memory = RewriteMemory(language=language, user_id=task.id)
        agent = PersonalizationAgent(
            tools=[],
            domain_policy=get_prompts(language).personalization_agent_system_prompt,
            memory=memory,
            user_profile=task.user_profile,
            llm=None,
            llm_args={},
            time=time,
            language=language,
        )
        user = PersonalizationUser(
            subtasks=task.subtasks,
            persona=str(task.user_profile),
            instructions=None,
            llm=None,
            llm_args={},
            language=language,
        )
        orchestrator = IntegrityPersonalizationOrchestrator(
            task=task,
            agent=agent,
            user=user,
            llm_evaluator=llm_evaluator,
            llm_args_evaluator=evaluator_args,
            language=language,
            evaluator_retries=evaluator_retries,
            evaluator_retry_backoff_seconds=evaluator_retry_backoff_seconds,
        )
        simulation = SimulationRun.model_validate(item)
        simulation = orchestrator.reevaluate_saved(simulation, trajectories)
        preserved = {
            key: item.get(key)
            for key in ("trial", "agent_version", "feature_flags")
            if key in item
        }
        updated = simulation.model_dump(mode="json")
        integrity = (updated.get("states") or {}).get("evaluation_integrity", {})
        updated.update(preserved)
        updated.update(
            {
                "evaluation_status": integrity.get("evaluation_status", "ok"),
                "evaluation_attempts": integrity.get("evaluation_attempts", 0),
                "evaluator_error": integrity.get("evaluator_error"),
                "trajectory_hash": item.get("trajectory_hash") or _trajectory_hash(updated),
            }
        )
        item.clear()
        item.update(updated)
        changed = True
        _write_checkpoint(path, checkpoint)
    if changed:
        checkpoint["timestamp"] = get_now()
        _write_checkpoint(path, checkpoint)
    return checkpoint


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--agent",
        choices=("stock", "adapt"),
        default="stock",
    )
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
    parser.add_argument(
        "--memory-type",
        choices=("rewrite", "groundtruth", "adapt"),
        default="rewrite",
        help=(
            "memory backend; 'adapt' runs the stock agent with ADAPT's memory "
            "backend to isolate the memory representation (E-046)"
        ),
    )
    parser.add_argument(
        "--profile-summary",
        action="store_true",
        help=(
            "let the LLM maintain a bounded, generalized preference summary "
            "and inject it into the prompt (baseline-style memory); off by "
            "default pending the paired measurement (E-046)"
        ),
    )
    parser.add_argument("--summary-max-chars", type=int, default=800)
    parser.add_argument(
        "--proactive-loop",
        action="store_true",
        help=(
            "AdaptAgent: observe the proactive question loop (record a sent "
            "question and link the user's reply); off by default so the agent "
            "stays a verified pass-through (E-086)"
        ),
    )
    parser.add_argument(
        "--candidate-evidence",
        action="store_true",
        help=(
            "AdaptAgent: append a bounded three-valued candidate/constraint "
            "observation to a copy of each tool result; off by default and "
            "unmeasured (E-087)"
        ),
    )
    parser.add_argument(
        "--task-state",
        action="store_true",
        help=(
            "AdaptAgent: append a bounded, non-directive statement of this "
            "subtask's required slots, observed candidates and whether a write "
            "or a question has happened yet; off by default and unmeasured "
            "(E-091)"
        ),
    )
    parser.add_argument("--evaluator-retries", type=int, default=2)
    parser.add_argument(
        "--evaluator-retry-backoff-seconds", type=float, default=1.0
    )
    parser.add_argument(
        "--no-agent-context-guard",
        action="store_true",
        help=(
            "disable the agent-side context-overflow safety trim (truncates "
            "old tool-message payloads only when the agent prompt would "
            "otherwise exceed the model limit)"
        ),
    )
    parser.add_argument(
        "--debug-to", type=Path, help="append ADAPT-visible events as JSONL"
    )
    parser.add_argument(
        "--no-normalize-extracter",
        action="store_true",
        help=(
            "disable the evaluator-output normalizer (nested list payloads "
            "otherwise raise 'list' object has no attribute 'get' and the "
            "subtask is recorded as evaluation_failed)"
        ),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not args.no_normalize_extracter:
        # Config-parity neutral: only the shape of the evaluator payload is
        # normalized; rubric text and reward semantics are untouched.
        patch_evaluator_extracter()
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
        memory_type=args.memory_type,
        evaluator_retries=args.evaluator_retries,
        evaluator_retry_backoff_seconds=args.evaluator_retry_backoff_seconds,
        debug_to=args.debug_to,
        agent_context_guard=not args.no_agent_context_guard,
        enable_profile_summary=args.profile_summary,
        summary_max_chars=args.summary_max_chars,
        enable_proactive_loop=args.proactive_loop,
        enable_candidate_evidence=args.candidate_evidence,
        enable_task_state=args.task_state,
    )


if __name__ == "__main__":
    main()
