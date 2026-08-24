"""Observable request-goal and authorization semantics.

The policy model may choose *how* to satisfy a task, but whether the user
asked for information or an external transaction is a runtime safety
boundary.  Keep that decision in one place so initial TaskSpec compilation
and later user confirmations cannot drift apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class DesiredOutcome(str, Enum):
    INFORM = "inform"
    TRANSACT = "transact"
    MODIFY = "modify"


@dataclass(frozen=True)
class CompletionContract:
    desired_outcome: DesiredOutcome
    evidence_span: str = ""

    @property
    def requires_write(self) -> bool:
        return self.desired_outcome in {
            DesiredOutcome.TRANSACT,
            DesiredOutcome.MODIFY,
        }


_MODIFY_RE = re.compile(r"取消|改签|退票|修改|改一下(?:订单|预约)")

# Match an agent-directed transaction verb, not a product/category noun.  In
# particular, bare `团`/`购` are deliberately excluded so `帮我看看团购券`
# remains an information request, while `帮我团张券` is a transaction.
_AGENT_TRANSACTION_RE = re.compile(
    r"(?:帮(?:我|我们)?|给我|替我|你(?:直接)?帮我|你给我|"
    r"麻烦(?:帮我|给我)?|快帮我|再帮我)"
    r"[^，。！？!?\n]{0,20}?"
    r"(?:下单|预约|预定|买|点|订|定|团(?:个|张|份|一下)|购(?:买|票))"
)
_SHORT_TRANSACTION_RE = re.compile(
    r"(?:请|麻烦|直接|快|再)?"
    r"(?:下单|点个|再来一杯|再来杯|来一杯|来杯|"
    r"买一下|买个票|买一份|订个|定个|订一间|预约|预定|团个券)"
)
_SELECTION_TRANSACTION_RE = re.compile(
    r"(?:就|那就|可以[，,]?就)?(?:订|定|买|点|下单)"
    r"(?:第[一二三四五1-5](?:个|家|款|项|杯|班|趟)?|这个|那个|它)"
)
_INFORMATION_RE = re.compile(
    r"推荐|帮我看看|帮我看|看看有没有|有哪些|有什么|比较一下|怎么选"
)
_NEGATION_BEFORE_RE = re.compile(r"(?:别|不要|不用|无需) *$")


def _transaction_match(text: str) -> re.Match[str] | None:
    for pattern in (
        _AGENT_TRANSACTION_RE,
        _SHORT_TRANSACTION_RE,
        _SELECTION_TRANSACTION_RE,
    ):
        for match in pattern.finditer(text or ""):
            prefix = (text or "")[max(0, match.start() - 4) : match.start()]
            if _NEGATION_BEFORE_RE.search(prefix):
                continue
            return match
    return None


def completion_contract(text: str) -> CompletionContract:
    content = (text or "").strip()
    modify = _MODIFY_RE.search(content)
    if modify:
        return CompletionContract(DesiredOutcome.MODIFY, modify.group(0))
    transaction = _transaction_match(content)
    if transaction:
        return CompletionContract(DesiredOutcome.TRANSACT, transaction.group(0))
    information = _INFORMATION_RE.search(content)
    return CompletionContract(
        DesiredOutcome.INFORM,
        information.group(0) if information else "",
    )


def has_create_authorization(text: str) -> bool:
    return completion_contract(text).desired_outcome == DesiredOutcome.TRANSACT


def selected_ordinal(text: str) -> int:
    """Return a 1-based choice from the latest visible shortlist."""
    match = re.search(
        r"第([\u4e00\u4e8c\u4e09\u56db\u4e941-5])(?:个|双|款|家|项|杯|班|趟)?|"
        r"首个|第一个|就选一|选一",
        text or "",
    )
    if not match:
        return 0
    if not match.group(1):
        return 1
    mapping = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}
    token = match.group(1)
    return mapping[token] if token in mapping else int(token)
