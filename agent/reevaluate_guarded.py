"""Re-evaluate checkpoint simulations while refusing invalid default rewards.

The re-scoring path delegates to the same config-driven
``vita.utils.llm_utils.generate`` used by the original run, so request
parameters (max_tokens, temperature, extra_body enable_thinking) stay
identical to the original evaluation. The only intervention is a
context-overflow guard: when the evaluator rejects a prompt with a
"maximum context length" 400, the window content is trimmed by the exact
token deficit and the request is retried once. Prompts that fit are sent
untouched.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import agent.vitabench_bootstrap as bootstrap

bootstrap.enable_vitabench_utf8()

from agent.evaluation_integrity import (
    IntegrityPersonalizationOrchestrator,
    patch_evaluator_extracter,
    restore_evaluator_extracter,
)
import agent.trace_metrics
from agent.vitabench_runner import _trajectory_hash, _write_checkpoint
from vita.config import models
from vita.data_model.personalization_task import PersonalizationTask
from vita.data_model.simulation import SimulationRun
from vita.domains.personalization.environment import get_tasks

from vita.agent.personalization_agent import PersonalizationAgent
from vita.memory.rewrite_memory import RewriteMemory
from vita.prompts import get_prompts
from vita.user.personalization_user import PersonalizationUser
from vita.utils.utils import get_now

_OVERFLOW_RE = re.compile(
    r"maximum context length is (\d+) tokens.*?"
    r"you requested (\d+) output tokens.*?"
    r"prompt contains at least (\d+) input tokens",
    re.DOTALL,
)


def _overflow_numbers(exc: Exception) -> tuple[int, int, int] | None:
    """Parse (max_len, output_tokens, input_tokens) from a vLLM 400 body."""
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
    match = _OVERFLOW_RE.search(text)
    if not match:
        return None
    return tuple(int(group) for group in match.groups())


def _total_content_chars(messages: list[Any]) -> int:
    total = 0
    for message in messages:
        content = (
            message.get("content")
            if isinstance(message, dict)
            else getattr(message, "content", None) or ""
        )
        total += len(content)
    return total


def _trim_window_content(messages: list[Any], scale: float) -> list[Any]:
    """Cut every <window_content> block by the same relative scale."""
    adjusted = []
    for message in messages:
        if isinstance(message, dict):
            role = message.get("role")
            content = message.get("content") or ""
            is_obj = True
        else:
            role = getattr(message, "role", None)
            content = getattr(message, "content", None) or ""
            is_obj = False
        if role == "user" and "<window_content>" in content:
            start = content.index("<window_content>") + len("<window_content>")
            end = content.index("</window_content>")
            window = content[start:end]
            cut = window[: int(len(window) * scale)]
            content = content[:start] + cut + content[end:]
            message = (
                {**message, "content": content}
                if is_obj
                else message.model_copy(update={"content": content})
            )
        adjusted.append(message)
    return adjusted


def _concat_content(messages: list[Any]) -> str:
    parts = []
    for message in messages:
        content = (
            message.get("content")
            if isinstance(message, dict)
            else getattr(message, "content", None) or ""
        )
        parts.append(content)
    return "\n".join(parts)


def _count_tokens(model: str, text: str, base_url: str) -> int:
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


def make_guarded_generate(original_generate):
    """Wrap generate(): delegate untouched unless a context-overflow 400 hits.

    The overflow path measures the true prompt length with the server's
    /tokenize endpoint (the numbers inside vLLM's 400 body are derived from
    the requested max_tokens, not measured), trims the window content by the
    exact deficit, and retries with progressively larger cuts if needed.
    """

    def guarded_generate(*args: Any, **kwargs: Any):
        model = kwargs.get("model") or (args[0] if args else None)
        messages = kwargs.get("messages") or (args[1] if len(args) > 1 else None)
        try:
            return original_generate(*args, **kwargs)
        except Exception as exc:
            parsed = _overflow_numbers(exc)
            if parsed is None or not messages:
                raise
            max_len, output_tokens, _input_tokens = parsed
            cfg = models.get(model, {}) if model else {}
            requested = (
                kwargs.get("max_tokens")
                or cfg.get("max_tokens")
                or output_tokens
                or 0
            )
            budget = max_len - int(requested) - 256
            base_url = cfg.get("base_url", "")
            total_chars = _total_content_chars(messages)
            count = _count_tokens(model, _concat_content(messages), base_url) if base_url else 0
            scale = None
            if count and total_chars:
                excess = count - budget
                if excess <= 0:
                    raise
                chars_to_remove = math.ceil(excess * (total_chars / count)) + 128
                scale = max(0.02, 1.0 - chars_to_remove / total_chars)
            for attempt in range(3):
                if scale is None:
                    scale = 0.7 ** (attempt + 1)
                print(
                    f"[overflow-guard] model={model} tokens={count or '?'} "
                    f"budget={budget} trimming window_content to scale={scale:.3f}",
                    flush=True,
                )
                trimmed = _trim_window_content(messages, scale)
                retry_kwargs = dict(kwargs)
                retry_kwargs["model"] = model
                retry_kwargs["messages"] = trimmed
                try:
                    return original_generate(**retry_kwargs)
                except Exception as retry_exc:
                    if _overflow_numbers(retry_exc) is None:
                        raise
                    scale = max(0.02, scale * 0.7)
            raise RuntimeError(f"context overflow not resolved after trims: {exc}")

    return guarded_generate


def _assert_real_evaluation(simulation: SimulationRun, user_id: str) -> None:
    reward_info = simulation.reward_info
    if reward_info is None:
        raise RuntimeError(f"{user_id}: evaluator returned no reward_info")
    info = reward_info.info
    if not isinstance(info, dict):
        raise RuntimeError(f"{user_id}: reward_info.info is not a mapping")
    rewards = info.get("subtask_rewards")
    if not isinstance(rewards, dict) or not rewards:
        raise RuntimeError(f"{user_id}: missing subtask-level rewards")
    for key in ("num_subtasks", "num_evaluated", "subtask_rewards"):
        if key not in info:
            raise RuntimeError(f"{user_id}: reward_info.info missing {key}")
    if not isinstance(reward_info.reward, (int, float)):
        raise RuntimeError(f"{user_id}: aggregate reward is not a number")
    if int(info["num_subtasks"]) != int(info["num_evaluated"]):
        raise RuntimeError(
            f"{user_id}: only {info['num_evaluated']}/{info['num_subtasks']} subtasks evaluated"
        )
    if int(info["num_evaluated"]) != len(rewards):
        raise RuntimeError(f"{user_id}: reward count mismatch")
    if int(info["num_evaluated"]) == 0:
        raise RuntimeError(f"{user_id}: no subtasks were evaluated")


def reevaluate_user(
    path: Path,
    output_path: Path,
    *,
    user_id: str,
    llm_evaluator: str,
    language: str = "chinese",
    evaluator_retries: int = 3,
    evaluator_retry_backoff_seconds: float = 3.0,
    evaluation_type: str = "trajectory",
    normalize_extracter: bool = False,
) -> dict:
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    matches = [
        item
        for item in checkpoint.get("simulations", [])
        if str(item.get("task_id")) == user_id
    ]
    if not matches:
        raise ValueError(f"User not found in checkpoint: {user_id}")

    tasks = {task.id: task for task in get_tasks(language)}
    task: PersonalizationTask = tasks[user_id]
    item = matches[0]
    trajectories = (item.get("states") or {}).get(
        "integrity_subtask_trajectories"
    )
    if not trajectories:
        raise ValueError(
            f"{user_id}: saved subtask trajectories are missing; refusing replay"
        )

    saved_ids = {str(entry.get("subtask_id")) for entry in trajectories}
    task_subtask_ids = {str(subtask.subtask_id) for subtask in task.subtasks}
    if saved_ids != task_subtask_ids:
        raise ValueError(f"{user_id}: saved subtask IDs do not match benchmark task")
    missing_criteria = [
        str(subtask.subtask_id)
        for subtask in task.subtasks
        if subtask.evaluation_criteria is None
    ]
    if missing_criteria:
        raise ValueError(
            f"{user_id}: benchmark criteria missing for {missing_criteria}"
        )

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
        llm_args_evaluator=deepcopy(models[llm_evaluator]),
        evaluation_type=evaluation_type,
        language=language,
        evaluator_retries=evaluator_retries,
        evaluator_retry_backoff_seconds=evaluator_retry_backoff_seconds,
    )
    simulation = SimulationRun.model_validate(item)

    import vita.evaluator.evaluator_traj as evaluator_traj
    import vita.utils.llm_utils as llm_utils

    original_generate = llm_utils.generate
    guarded = make_guarded_generate(original_generate)
    llm_utils.generate = guarded
    evaluator_traj.generate = guarded
    original_extracter = (
        patch_evaluator_extracter() if normalize_extracter else None
    )
    try:
        simulation = orchestrator.reevaluate_saved(simulation, trajectories)
    finally:
        llm_utils.generate = original_generate
        evaluator_traj.generate = original_generate
        if original_extracter is not None:
            restore_evaluator_extracter(original_extracter)
    _assert_real_evaluation(simulation, user_id)

    updated = simulation.model_dump(mode="json")
    integrity = (updated.get("states") or {}).get("evaluation_integrity", {})
    updated.update(
        {
            "evaluation_status": integrity.get("evaluation_status", "ok"),
            "evaluation_attempts": integrity.get("evaluation_attempts", 0),
            "evaluator_error": integrity.get("evaluator_error"),
            "trajectory_hash": item.get("trajectory_hash")
            or _trajectory_hash(updated),
        }
    )
    output_checkpoint = deepcopy(checkpoint)
    output_matches = [
        item
        for item in output_checkpoint.get("simulations", [])
        if str(item.get("task_id")) == user_id
    ]
    output_matches[0].clear()
    output_matches[0].update(updated)
    output_checkpoint["timestamp"] = get_now()
    output_checkpoint["reevaluation_guard"] = {
        "complete_criteria_required": True,
        "complete_subtask_rewards_required": True,
        "normalize_extracter": bool(normalize_extracter),
        "source": str(path),
    }
    _write_checkpoint(output_path, output_checkpoint)
    return output_checkpoint


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--evaluator-llm", required=True)
    parser.add_argument("--language", default="chinese")
    parser.add_argument("--evaluator-retries", type=int, default=3)
    parser.add_argument(
        "--evaluator-retry-backoff-seconds", type=float, default=3.0
    )
    parser.add_argument("--evaluation-type", default="trajectory")
    parser.add_argument(
        "--normalize-extracter",
        action="store_true",
        help=(
            "flatten double-wrapped evaluator JSON outputs instead of "
            "crashing; only used for re-scoring vendored-parser failures"
        ),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    checkpoint = reevaluate_user(
        args.checkpoint,
        args.output,
        user_id=args.user_id,
        llm_evaluator=args.evaluator_llm,
        language=args.language,
        evaluator_retries=args.evaluator_retries,
        evaluator_retry_backoff_seconds=args.evaluator_retry_backoff_seconds,
        evaluation_type=args.evaluation_type,
        normalize_extracter=args.normalize_extracter,
    )
    simulation = next(
        item
        for item in checkpoint["simulations"]
        if item["task_id"] == args.user_id
    )
    print(json.dumps(simulation["reward_info"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
