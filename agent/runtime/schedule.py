"""Deterministic resolution of relative dates in the current instruction.

The environment tells the agent its own clock ("当前时间：2024-12-24 20:00:00
星期二"), while users speak in relative terms ("明天晚上", "周末", "周六",
"下周三"). Two different things depended on the model converting that by hand:
the concrete date written into a booking, and whether the agent ever confirmed
which calendar day it was dealing with at all.

This module converts those expressions with pure date arithmetic, so the
runtime can publish one grounded absolute date and ask the environment about it
before choosing or writing anything. Nothing here reads evaluator data, and no
rule is specific to a user, task or candidate.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

_WEEKDAY_DIGITS = {
    "一": 0,
    "二": 1,
    "三": 2,
    "四": 3,
    "五": 4,
    "六": 5,
    "日": 6,
    "天": 6,
    "1": 0,
    "2": 1,
    "3": 2,
    "4": 3,
    "5": 4,
    "6": 5,
    "7": 6,
}

_TIME_HINTS = (
    ("早上", "早"),
    ("早晨", "早"),
    ("上午", "上午"),
    ("中午", "中午"),
    ("下午", "下午"),
    ("傍晚", "傍晚"),
    ("晚上", "晚上"),
    ("晚间", "晚上"),
    ("夜里", "晚上"),
)

_DAY_OFFSETS = (("大后天", 3), ("后天", 2), ("明天", 1), ("明日", 1), ("明晚", 1),
                ("明儿", 1), ("今天", 0), ("今日", 0), ("今晚", 0), ("当天", 0))

_WEEKDAY_RE = re.compile(r"(下{1,2})?(?:周|星期|礼拜)([一二三四五六日天1-7])")
_WEEKEND_RE = re.compile(r"(下)?(?:个)?(?:周末|双休)")
_DAYS_LATER_RE = re.compile(r"([一二三四五六七八九十\d]{1,3})\s*天(?:后|以后|之后)")
_MONTH_END_RE = re.compile(r"(?:这个|本)?月(?:底|末)")


@dataclass(frozen=True)
class DateResolution:
    """One grounded absolute date derived from a relative expression."""

    date: str
    evidence: str
    time_hint: str = ""

    @property
    def weekday(self) -> int:
        return datetime.strptime(self.date, "%Y-%m-%d").weekday()

    @property
    def is_weekend(self) -> bool:
        return self.weekday >= 5


def parse_agent_time(value: str) -> datetime | None:
    """Parse the environment's own clock string, ignoring the weekday suffix."""
    if not value:
        return None
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?", value)
    if not match:
        return None
    year, month, day = (int(match.group(index)) for index in (1, 2, 3))
    hour = int(match.group(4) or 0)
    minute = int(match.group(5) or 0)
    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def _chinese_number(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
              "八": 8, "九": 9, "十": 10}
    if text == "十":
        return 10
    if "十" in text:
        head, _, tail = text.partition("十")
        tens = digits.get(head or "一", 0) * 10
        return tens + digits.get(tail, 0) if tail else tens
    return digits.get(text)


def _time_hint(text: str) -> str:
    for marker, hint in _TIME_HINTS:
        if marker in text:
            return hint
    return ""


def resolve_relative_date(text: str, now: datetime) -> DateResolution | None:
    """Resolve the first relative date expression in ``text``.

    Order matters: a weekday ("周六") is more specific than a weekend word, and
    an explicit offset ("后天") is more specific than both.
    """
    content = text or ""
    if not content or now is None:
        return None
    for marker, offset in _DAY_OFFSETS:
        if marker in content:
            return DateResolution(
                (now + timedelta(days=offset)).strftime("%Y-%m-%d"),
                marker,
                _time_hint(content),
            )
    match = _WEEKDAY_RE.search(content)
    if match:
        prefix, digit = match.group(1) or "", match.group(2)
        target = _WEEKDAY_DIGITS[digit]
        # ``now.weekday()`` is Monday=0. A bare weekday means the next one,
        # counting today; "下周X" always lands in the following week.
        base = now + timedelta(days=7 if prefix else 0)
        delta = (target - base.weekday()) % 7
        if not prefix and delta == 0 and now.hour > 12:
            delta = 7
        return DateResolution(
            (base + timedelta(days=delta)).strftime("%Y-%m-%d"),
            match.group(0),
            _time_hint(content),
        )
    match = _WEEKEND_RE.search(content)
    if match:
        base = now + timedelta(days=7 if match.group(1) else 0)
        saturday = base + timedelta(days=(5 - base.weekday()) % 7)
        return DateResolution(
            saturday.strftime("%Y-%m-%d"), match.group(0), _time_hint(content)
        )
    match = _DAYS_LATER_RE.search(content)
    if match:
        offset = _chinese_number(match.group(1))
        if offset is not None:
            return DateResolution(
                (now + timedelta(days=offset)).strftime("%Y-%m-%d"),
                match.group(0),
                _time_hint(content),
            )
    match = _MONTH_END_RE.search(content)
    if match:
        last_day = calendar.monthrange(now.year, now.month)[1]
        return DateResolution(
            now.replace(day=last_day).strftime("%Y-%m-%d"),
            match.group(0),
            _time_hint(content),
        )
    return None
