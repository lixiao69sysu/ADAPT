"""Agent-side progress and idempotency checks for tool execution.

Phase 3 improvement: Stall detection — when the agent has searched multiple
times without creating an order, emit a correction forcing it to select from
existing candidates instead of continuing to search.
"""

from __future__ import annotations

import json
import re
from typing import Protocol

from vita.data_model.message import ToolCall, ToolMessage

from agent.harness.tool_guard import ValidationIssue


_ORDER_ID = re.compile(r"order_id\s*[:=]\s*([A-Za-z0-9_-]+)", re.IGNORECASE)

SEARCH_TOOLS = {"search", "search_products", "search_stores", "recommand", "search_recommand"}
QUESTION_TOOLS = {"suggest_question", "suggest_question_tool"}
MAX_SEARCH_BEFORE_SELECT = 2
MAX_QUESTIONS_BEFORE_ACT = 2


class ProgressState(Protocol):
    tool_call_counts: dict[str, int]
    pending_tool_names: dict[str, str]
    created_order_ids: set[str]
    last_order_result: str
    goal_completed: bool
    disabled_tool_names: set[str]


def call_signature(call: ToolCall) -> str:
    arguments = json.dumps(
        call.arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return f"{call.name}:{arguments}"


def is_write_tool(name: str) -> bool:
    return name.startswith(("create_", "pay_", "cancel_", "modify_"))


def validate_progress(call: ToolCall, state: ProgressState) -> list[ValidationIssue]:
    if state.goal_completed:
        return [
            ValidationIssue(
                "goal_already_completed",
                "当前消费目标已经支付完成；不得继续搜索、询问或创建新订单",
            )
        ]

    if call.name in state.disabled_tool_names:
        return [
            ValidationIssue(
                "tool_disabled_after_stall",
                f"工具 {call.name} 因此前没有推进任务而已被禁用；必须换用已有候选继续执行",
            )
        ]

    signature = call_signature(call)
    if state.tool_call_counts.get(signature, 0) >= 2:
        return [
            ValidationIssue(
                "repeated_tool_call",
                f"工具调用 {signature} 已产生过两次相同请求；必须使用已有结果推进任务",
            )
        ]

    if call.name.startswith("create_") and state.created_order_ids:
        return [
            ValidationIssue(
                "duplicate_order",
                "当前任务已经成功创建订单；应支付已有订单，不得创建第二个订单",
            )
        ]
    return []


def record_calls(calls: list[ToolCall], state: ProgressState) -> None:
    for call in calls:
        signature = call_signature(call)
        state.tool_call_counts[signature] = state.tool_call_counts.get(signature, 0) + 1
        if call.id:
            state.pending_tool_names[call.id] = call.name


def search_call_count(state: ProgressState) -> int:
    """Count total search/recommend tool calls made so far."""
    total = 0
    for sig, count in state.tool_call_counts.items():
        tool_name = sig.split(":")[0] if ":" in sig else sig
        if any(s in tool_name.lower() for s in SEARCH_TOOLS):
            total += count
    return total


def should_force_select(state: ProgressState) -> bool:
    """Return True when agent has searched enough and should pick a candidate."""
    if state.goal_completed:
        return False
    if state.created_order_ids:
        return False
    return search_call_count(state) >= MAX_SEARCH_BEFORE_SELECT


def question_call_count(state: ProgressState) -> int:
    """Count total question tool calls made so far."""
    total = 0
    for sig, count in state.tool_call_counts.items():
        tool_name = sig.split(":")[0] if ":" in sig else sig
        if any(s in tool_name.lower() for s in QUESTION_TOOLS):
            total += count
    return total


def should_force_act(state: ProgressState) -> bool:
    """Return True when agent has asked too many questions and should act."""
    if state.goal_completed:
        return False
    if state.created_order_ids:
        return False
    return question_call_count(state) >= MAX_QUESTIONS_BEFORE_ACT


def stall_correction_message(state: ProgressState) -> str:
    """Generate a correction message when the agent is stuck searching."""
    n_searches = search_call_count(state)
    return (
        f"已搜索{n_searches}次但尚未创建订单。立即停止搜索，从已有工具返回的"
        "候选商品/店铺中选择一个满足用户需求的，直接创建订单。不要再次调用"
        "任何搜索或查询工具。如果已有候选都不完美，选择最接近的一个继续。"
    )


def over_questioning_correction_message(state: ProgressState) -> str:
    """Generate a correction message when the agent asks too many questions."""
    n_questions = question_call_count(state)
    return (
        f"已询问用户{n_questions}次但用户已表达'随便'或'你看着办'。停止询问，"
        "根据已有信息和工具返回的候选，直接为用户选择一个最合适的方案并创建订单。"
        "不要再次调用询问工具。"
    )


def observe_result(message: ToolMessage, state: ProgressState) -> None:
    if message.error:
        return
    name = state.pending_tool_names.get(message.id, message.name)
    content = message.content or ""
    if name.startswith("create_"):
        match = _ORDER_ID.search(content)
        if match:
            state.created_order_ids.add(match.group(1))
            state.last_order_result = content
    if name.startswith("pay_") and (
        "payment successful" in content.lower() or "支付成功" in content
    ):
        state.goal_completed = True
