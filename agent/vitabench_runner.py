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

from agent.adapt_agent import ADAPTAgent
from agent.evaluation_integrity import (
    IntegrityPersonalizationOrchestrator,
    patch_evaluator_extracter,
)
from agent.memory.adapt_memory import ADAPTMemory
from agent.v2 import ADAPTV2, HybridMemory, V2FeatureFlags

SPLIT_SEED = "ADAPT-2026"

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
    enable_adapt_prompt: bool = True,
    gate_phases: bool = True,
    focus_write_phase: bool = True,
    framework_speech: bool = False,
    enable_profile_summary: bool = False,
    summary_max_chars: int = 800,
    evaluator_retries: int = 2,
    evaluator_retry_backoff_seconds: float = 1.0,
    debug_path: Path | None = None,
) -> SimulationRun:
    """Compose frozen ADAPT V1 with unchanged VitaBench components.

    ``enable_adapt_prompt`` and ``gate_phases`` exist for the isolation rig: the
    first drops ADAPT's own prompt blocks (policy, decision card, runtime state,
    lessons, ledger) so only the stock prompt remains, the second stops cropping
    the tool set per phase. They let one factor be varied at a time when
    comparing against the stock baseline (E-046).
    """
    user_id = task.user_profile.get("user_id") if task.user_profile else task.id
    memory = ADAPTMemory(
        language=language,
        user_id=user_id,
        enable_summary_rewrite=enable_profile_summary,
        summary_max_chars=summary_max_chars,
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
        enable_adapt_prompt=enable_adapt_prompt,
        gate_phases=gate_phases,
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
    simulation = orchestrator.run()
    if debug_path is not None:
        agent.dump_debug_trace(debug_path, append=True)
    return simulation


def run_stock_or_v2_personalization_task(
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
    feature_flags: V2FeatureFlags | None = None,
    memory_type: str = "rewrite",
    evaluator_retries: int = 2,
    evaluator_retry_backoff_seconds: float = 1.0,
    debug_path: Path | None = None,
    agent_context_guard: bool = True,
    enable_profile_summary: bool = False,
    summary_max_chars: int = 800,
) -> SimulationRun:
    """Run stock or ADAPT V2 through the same integrity-aware harness."""
    if agent_kind not in {"stock", "adapt_v2"}:
        raise ValueError(f"Unsupported stock/V2 agent kind: {agent_kind}")
    flags = feature_flags or V2FeatureFlags()
    user_id = task.user_profile.get("user_id") if task.user_profile else task.id
    if memory_type == "groundtruth":
        if flags.hybrid_memory:
            raise ValueError("hybrid_memory and groundtruth memory are separate ablations")
        memory = GroundtruthMemory(language=language, user_id=user_id)
    elif memory_type == "rewrite":
        rewrite = RewriteMemory(language=language, user_id=user_id)
        memory = (
            HybridMemory(language=language, rewrite=rewrite, user_id=user_id)
            if agent_kind == "adapt_v2" and flags.hybrid_memory
            else rewrite
        )
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
    agent = (
        ADAPTV2(**common_agent_args, feature_flags=flags)
        if agent_kind == "adapt_v2"
        else PersonalizationAgent(**common_agent_args)
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
    if isinstance(agent, ADAPTV2):
        simulation.states["adapt_v2"] = agent.debug_snapshot()
    if debug_path is not None and isinstance(agent, ADAPTV2):
        agent.dump_debug_trace(debug_path, append=True)
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
    enable_candidate_validation: bool,
    enable_lessons: bool,
    enable_tiered_compaction: bool,
    evaluator_retries: int,
    evaluator_retry_backoff_seconds: float,
    feature_flags: V2FeatureFlags,
    memory_type: str,
    debug_path: Path | None,
    agent_context_guard: bool,
    enable_adapt_prompt: bool = True,
    gate_phases: bool = True,
    focus_write_phase: bool = True,
    framework_speech: bool = False,
    enable_profile_summary: bool = False,
    summary_max_chars: int = 800,
) -> SimulationRun:
    if agent_kind == "adapt_v1":
        return run_adapt_personalization_task(
            task,
            llm_agent=llm_agent,
            llm_user=llm_user,
            llm_evaluator=llm_evaluator,
            llm_args_evaluator=evaluator_args,
            max_steps=max_steps,
            seed=seed,
            language=language,
            enable_candidate_validation=enable_candidate_validation,
            enable_lessons=enable_lessons,
            enable_tiered_compaction=enable_tiered_compaction,
            enable_adapt_prompt=enable_adapt_prompt,
            gate_phases=gate_phases,
            enable_profile_summary=enable_profile_summary,
            summary_max_chars=summary_max_chars,
            evaluator_retries=evaluator_retries,
            evaluator_retry_backoff_seconds=evaluator_retry_backoff_seconds,
            debug_path=debug_path,
        )
    return run_stock_or_v2_personalization_task(
        task,
        agent_kind=agent_kind,
        llm_agent=llm_agent,
        llm_user=llm_user,
        max_steps=max_steps,
        seed=seed,
        llm_evaluator=llm_evaluator,
        llm_args_evaluator=evaluator_args,
        language=language,
        feature_flags=feature_flags,
        memory_type=memory_type,
        evaluator_retries=evaluator_retries,
        evaluator_retry_backoff_seconds=evaluator_retry_backoff_seconds,
        debug_path=debug_path,
        agent_context_guard=agent_context_guard,
        enable_profile_summary=enable_profile_summary,
        summary_max_chars=summary_max_chars,
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
    enable_candidate_validation: bool,
    enable_lessons: bool,
    enable_tiered_compaction: bool = True,
    memory_type: str = "rewrite",
    v2_feature_flags: V2FeatureFlags | None = None,
    evaluator_retries: int = 2,
    evaluator_retry_backoff_seconds: float = 1.0,
    debug_to: Path | None = None,
    agent_context_guard: bool = True,
    enable_adapt_prompt: bool = True,
    gate_phases: bool = True,
    focus_write_phase: bool = True,
    framework_speech: bool = False,
    enable_profile_summary: bool = False,
    summary_max_chars: int = 800,
) -> dict:
    if agent_kind == "adapt":
        # Backward-compatible CLI spelling. V1 remains frozen and explicit in
        # newly written checkpoint metadata.
        agent_kind = "adapt_v1"
    flags = v2_feature_flags or V2FeatureFlags()
    if agent_kind == "adapt_v1" and memory_type != "rewrite":
        raise ValueError("Frozen ADAPT V1 supports only its native ADAPTMemory")
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
            "memory_type": memory_type,
            "v2_feature_flags": flags.as_dict(),
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
                    enable_candidate_validation=enable_candidate_validation,
                    enable_lessons=enable_lessons,
                    enable_tiered_compaction=enable_tiered_compaction,
                    evaluator_retries=evaluator_retries,
                    evaluator_retry_backoff_seconds=evaluator_retry_backoff_seconds,
                    feature_flags=flags,
                    memory_type=memory_type,
                    debug_path=debug_to,
                    agent_context_guard=agent_context_guard,
                    enable_adapt_prompt=enable_adapt_prompt,
                    gate_phases=gate_phases,
                    focus_write_phase=focus_write_phase,
                    framework_speech=framework_speech,
                    enable_profile_summary=enable_profile_summary,
                    summary_max_chars=summary_max_chars,
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
                    "agent_version": agent_kind,
                    "feature_flags": flags.as_dict() if agent_kind == "adapt_v2" else {},
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
        choices=("stock", "adapt", "adapt_v1", "adapt_v2"),
        default="adapt_v2",
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
        "--framework-speech",
        action="store_true",
        help=(
            "legacy governor path: the framework asks the clarifying question "
            "and finalises the recommendation itself (off by default, E-048)"
        ),
    )
    parser.add_argument(
        "--keep-write-phase-history",
        action="store_true",
        help=(
            "isolation rig: keep the full transcript in the write phases instead "
            "of replacing it with the system prompt, the latest user turn and a "
            "controller directive (E-047)"
        ),
    )
    parser.add_argument("--no-candidate-validation", action="store_true")
    parser.add_argument("--no-lessons", action="store_true")
    parser.add_argument("--no-tiered-compaction", action="store_true")
    parser.add_argument(
        "--no-adapt-prompt",
        action="store_true",
        help=(
            "isolation rig: drop ADAPT's own prompt blocks (policy, decision "
            "card, runtime state, lessons, ledger) and keep only the stock "
            "prompt, so the prompt tax can be measured separately"
        ),
    )
    parser.add_argument(
        "--no-phase-gating",
        action="store_true",
        help=(
            "isolation rig: stop cropping the tool set per phase (every tool "
            "stays visible); write-time validation is unchanged"
        ),
    )
    parser.add_argument(
        "--v2-features",
        nargs="*",
        default=[],
        metavar="FEATURE",
        help=(
            "opt-in V2 flags: hybrid_memory decision_workspace planner "
            "voi_questions transaction_enforcement and shadow_* variants"
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
    flags = V2FeatureFlags.from_names(args.v2_features)
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
        enable_candidate_validation=not args.no_candidate_validation,
        enable_lessons=not args.no_lessons,
        enable_tiered_compaction=not args.no_tiered_compaction,
        memory_type=args.memory_type,
        v2_feature_flags=flags,
        evaluator_retries=args.evaluator_retries,
        evaluator_retry_backoff_seconds=args.evaluator_retry_backoff_seconds,
        debug_to=args.debug_to,
        agent_context_guard=not args.no_agent_context_guard,
        enable_adapt_prompt=not args.no_adapt_prompt,
        gate_phases=not args.no_phase_gating,
        enable_profile_summary=args.profile_summary,
        summary_max_chars=args.summary_max_chars,
        focus_write_phase=not args.keep_write_phase_history,
        framework_speech=args.framework_speech,
    )


if __name__ == "__main__":
    main()
