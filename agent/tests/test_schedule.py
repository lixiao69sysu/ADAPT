"""Deterministic relative-date resolution and its framework grounding (E-039).

The environment tells the agent its own clock, while users speak relatively.
Converting "明天晚上" or "周末" must not depend on the policy model, and a
time-relative request must not lead straight into a time-sensitive write
without the calendar day being confirmed.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from agent.decision import CandidateLedger, DecisionCard
from agent.runtime.schedule import (
    parse_agent_time,
    resolve_relative_date,
)
from agent.runtime.state import RuntimePhase, TaskRuntime
from agent.runtime.tools import ToolMeta, ToolRegistry, ToolRole


def at(value: str) -> datetime:
    parsed = parse_agent_time(value)
    assert parsed is not None
    return parsed


def test_parse_agent_time_ignores_the_weekday_suffix():
    assert at("2024-12-24 20:00:00 星期二") == datetime(2024, 12, 24, 20, 0)
    assert parse_agent_time("") is None
    assert parse_agent_time("unknown") is None


@pytest.mark.parametrize(
    "instruction,expected,evidence",
    [
        ("明天晚上想去放松一下", "2024-12-25", "明天"),
        ("后天帮我订张票", "2024-12-26", "后天"),
        ("我今天就想吃火锅", "2024-12-24", "今天"),
        ("周六要去绵阳找朋友，帮我定张车票", "2024-12-28", "周六"),
        ("周末又想去摘草莓了", "2024-12-28", "周末"),
        ("下周三出发", "2025-01-01", "下周三"),
        ("3天后送到", "2024-12-27", "3天后"),
        ("月底之前搞定", "2024-12-31", "月底"),
    ],
)
def test_relative_dates_resolve_against_the_agent_clock(instruction, expected, evidence):
    resolution = resolve_relative_date(instruction, at("2024-12-24 20:00:00 星期二"))
    assert resolution is not None
    assert resolution.date == expected
    assert resolution.evidence == evidence


def test_weekday_resolution_is_relative_to_today():
    # 2024-05-23 is a Thursday; the coming Saturday is 05-25.
    resolution = resolve_relative_date("周六去绵阳", at("2024-05-23 09:30:00 星期四"))
    assert resolution is not None
    assert resolution.date == "2024-05-25"
    assert not resolution.is_weekend or resolution.weekday == 5


def test_a_passed_weekday_in_the_evening_moves_to_next_week():
    resolution = resolve_relative_date("周三晚上聚一下", at("2024-12-25 20:00:00 星期三"))
    assert resolution is not None
    assert resolution.date == "2025-01-01"
    same_day = resolve_relative_date("周三中午聚一下", at("2024-12-25 09:00:00 星期三"))
    assert same_day is not None
    assert same_day.date == "2024-12-25"


def test_time_of_day_hint_is_recorded():
    resolution = resolve_relative_date("明天晚上想放松", at("2024-12-24 20:00:00 星期二"))
    assert resolution is not None
    assert resolution.time_hint == "晚上"
    assert resolve_relative_date("没有时间词的请求", at("2024-12-24 20:00:00")) is None


def test_runtime_publishes_the_resolved_date():
    from agent.decision import TaskSpec

    runtime = TaskRuntime.begin(TaskSpec.compile("明天晚上帮我找个地方"))
    runtime.resolved_date = "2024-12-25"
    runtime.date_evidence = "明天"
    runtime.date_time_hint = "晚上"
    rendered = runtime.render()
    assert "RESOLVED_DATE=2024-12-25 (from 明天) time-of-day: 晚上" in rendered


class _Debug:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, event: str, **payload) -> None:
        self.events.append({"event": event, **payload})


def build_agent() -> object:
    from agent.adapt_agent import ADAPTAgent
    from agent.decision import TaskSpec

    agent = ADAPTAgent.__new__(ADAPTAgent)
    agent.debug = _Debug()
    agent.ledger = CandidateLedger()
    agent.task_spec = TaskSpec.compile("明天晚上帮我找个地方")
    agent.decision_card = DecisionCard()
    agent.runtime = TaskRuntime.begin(agent.task_spec)
    agent.tool_registry = ToolRegistry()
    agent.tool_registry.meta = {
        "get_date_holiday_info": ToolMeta(
            "get_date_holiday_info", ToolRole.READ, {"date"}, {}
        ),
        "instore_shop_search_recommend": ToolMeta(
            "instore_shop_search_recommend", ToolRole.SEARCH, {"keywords"}, {}
        ),
    }
    agent._date_grounded = False
    return agent


def test_date_grounding_call_is_issued_once_before_deciding():
    agent = build_agent()
    agent.runtime.phase = RuntimePhase.SEARCH
    agent.runtime.resolved_date = "2024-12-25"
    agent.runtime.date_evidence = "明天"
    message = agent._framework_date_grounding()
    assert message is not None
    call = message.tool_calls[0]
    assert call.name == "get_date_holiday_info"
    assert call.arguments == {"date": "2024-12-25"}
    # Never repeated, and never issued after the write phase has been reached.
    assert agent._framework_date_grounding() is None


def test_date_grounding_skipped_without_a_relative_expression():
    agent = build_agent()
    agent.runtime.phase = RuntimePhase.SEARCH
    assert agent._framework_date_grounding() is None


def test_date_grounding_is_not_issued_after_execution_started():
    agent = build_agent()
    agent.runtime.phase = RuntimePhase.READY_TO_CREATE
    agent.runtime.resolved_date = "2024-12-25"
    assert agent._framework_date_grounding() is None
