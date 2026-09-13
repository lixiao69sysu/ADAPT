"""Proactive question policy: ask about a declared gap, never about a topic.

The previous policy decided *what to ask* by matching topic keywords
(咖啡/时间/人数/房型) against the instruction and a fixed per-domain question
table, then defaulting the domain to ``delivery`` when it could not tell. That
produced four reproduced defects (E-068): a book request got a food-taste
question, a haircut got a headcount question, a remembered dislike was echoed
back as a preference, and a tool-findable field was asked about.

This module keeps what was necessary -- the question budget, the pending
question, the answer association -- and replaces the decision with a gap-driven
one:

1. **A question exists only for a declared gap.** ``TaskSpec.unknown_slots`` is
   the compiler's own list of required-but-unstated slots. No gap, no question.
   That is what makes "帮我推荐一本书" correctly ask nothing.
2. **The question text comes from the slot, not from a domain topic list.** The
   slot vocabulary is the compiler's schema, so adding support for a new slot is
   a schema change, not another keyword.
3. **Personalisation reads structured facts only.** The old policy substring-
   matched the rendered memory text, so "不喜欢高铁" was echoed as "您之前偏好
   高铁". Polarity must come from the fact's typed polarity, never from the
   presence of a word (the E-059 invariant, one layer up).
4. **A gap a tool can close is not asked.** ``product``/``shop_or_service`` are
   found by searching; asking the user about them spends the budget on something
   a tool call answers.

The proposal is pure: it never spends budget. ``commit_question`` spends it only
after the question was actually sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Slots whose value only the user can supply. Everything else is either
# tool-findable (search returns products, shops, hotels) or derivable.
USER_ONLY_SLOTS: frozenset[str] = frozenset(
    {
        "address",
        "city",
        "date",
        "departure",
        "destination",
        "quantity",
        "room_type",
        "size",
        "time",
        "transport",
        "caffeine",
        "budget",
        "party_size",
        "taste",
    }
)

# Not askable: slots a search or detail call fills, plus slots whose
# "is it already stated?" test is too weak to trust.
#
# ``product``/``shop_or_service`` belong here deliberately. The compiler's
# product-category vocabulary does not cover food names, so marking them askable
# produced 47 questions of the form "您想要哪一类的呢？" for instructions that had
# already named the item ("帮我点个麻辣烫"). Re-asking something the user just
# said is the "已明确仍机械重问" failure, and it is worse than staying silent
# (E-068).
TOOL_FINDABLE_SLOTS: frozenset[str] = frozenset(
    {"product", "shop_or_service", "store", "hotel", "attraction"}
)

# Slots the agent's own context already answers. Every user profile in the
# benchmark carries 常住住址/工作地址, and ``decision.profile_address`` resolves
# the 家/公司 aliases, so asking the user for a delivery address is asking for
# something already in hand. The rule is the user's own: if it can be looked up,
# look it up rather than spending a question on it.
CONTEXT_RESOLVABLE_SLOTS: frozenset[str] = frozenset({"address"})

# One question per slot, phrased without naming a domain. The slot name is the
# schema's, so this table grows with the schema rather than with the corpus.
SLOT_QUESTIONS: dict[str, str] = {
    "transport": "这次出行您想用哪种方式？比如高铁、飞机或汽车。",
    "destination": "您要去哪里呢？",
    "departure": "您从哪里出发呢？",
    "city": "您想在哪个城市呢？",
    "date": "您计划哪一天呢？",
    "time": "您希望什么时间呢？",
    "quantity": "您需要几份／几张呢？",
    "address": "送到哪里呢？",
    "room_type": "您对房型有要求吗？比如大床房、双床房或套房。",
    "size": "您需要什么尺码呢？",
    "caffeine": "您想要高咖啡因还是低咖啡因的呢？",
    "budget": "您大致的预算范围是多少呢？",
    "party_size": "大概几个人一起呢？",
    "product": "您想要哪一类的呢？",
    "shop_or_service": "您想找哪一类的店或服务呢？",
    "taste": "您对口味有什么要求吗？",
}

# The order gaps are asked in when several are open at once: the one that most
# changes the next step comes first.
SLOT_PRIORITY: tuple[str, ...] = (
    "transport",
    "destination",
    "departure",
    "city",
    "date",
    "time",
    "room_type",
    "size",
    "budget",
    "quantity",
    "address",
    "party_size",
    "taste",
    "caffeine",
)


@dataclass(frozen=True)
class QuestionContext:
    """Everything the policy is allowed to look at. No raw memory text."""

    instruction: str = ""
    domain: str = ""
    facet: str = ""
    action: str = ""
    unknown_slots: tuple[str, ...] = ()
    resolved_slots: dict[str, str] = field(default_factory=dict)
    # slot -> value, taken from structured facts only.
    known_slots: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Proposal:
    """A question the policy would ask, together with what it is about."""

    question: str
    slot: str = ""
    value: str = ""
    is_confirmation: bool = False


@dataclass(frozen=True)
class PendingQuestion:
    """A question that was actually sent, with what it was about.

    Storing the *slot* alongside the text is what lets a reply be linked to a
    decision dimension. Without it, answering "是的" to "这次仍然不加糖吗？"
    produced the structured fact ``value="是的"`` -- the literal reply, which is
    useless downstream (E-069).
    """

    question: str
    slot: str = ""
    # The value the question is *about*. Set for confirmations, so an
    # affirmative reply can be resolved to the value rather than to the word
    # "yes".
    value: str = ""
    polarity: str = ""
    is_confirmation: bool = False


# A bare affirmative/negative reply carries no value of its own; it must be
# resolved against the pending question. Negatives are checked first because
# "不是" contains "是".
_NEGATIVE_REPLIES = (
    "不是",
    "不用",
    "不要",
    "不需要",
    "不用了",
    "不对",
    "不一样",
    "不",
    "否",
    "no",
    "nope",
)
_AFFIRMATIVE_REPLIES = (
    "是的",
    "对",
    "对的",
    "嗯",
    "好",
    "好的",
    "可以",
    "行",
    "要",
    "需要",
    "一样",
    "还是",
    "没错",
    "当然",
    "是",
    "yes",
    "yep",
    "ok",
)


def _is_affirmative(text: str) -> bool:
    lowered = text.strip().casefold()
    if not lowered or len(lowered) > 12:
        return False
    if any(marker in lowered for marker in _NEGATIVE_REPLIES):
        return False
    return any(marker in lowered for marker in _AFFIRMATIVE_REPLIES)


def _is_negative(text: str) -> bool:
    lowered = text.strip().casefold()
    if not lowered or len(lowered) > 12:
        return False
    return any(marker in lowered for marker in _NEGATIVE_REPLIES)


# Handing the decision back is not stating a preference. Observed in the first
# proactive smoke: the reply "随便，你看着办吧。" to "您想用哪种方式？" was stored
# as the value of the ``transport`` slot (E-069). Delegation is a state change --
# the agent must decide -- not evidence about the user.
_DELEGATION_PHRASES = (
    "随便",
    "你看着办",
    "看着办",
    "都行",
    "都可以",
    "听你的",
    "无所谓",
    "看你",
    "你觉得",
    "你决定",
    "你定",
    "我不太清楚",
    "不清楚",
    "不知道",
    "没想好",
    "都听",
)
_PARTICLES = "了吧啊呢嗯哦呀嘛，,。.！!？?、；;：: \t"

# Correction framing that introduces the new value rather than naming it:
# "不是，这次少糖" -> the value is 少糖, not "这次少糖". This is discourse framing,
# not domain vocabulary, so it is deliberately tiny and domain-free.
_CORRECTION_FRAMING = (
    "这次",
    "这回",
    "那",
    "这",
    "改成",
    "换成",
    "改为",
    "换",
    "要",
    "就",
)


def _is_delegation(text: str) -> bool:
    """Whether the reply only hands the decision back, stating no value.

    Requires that a delegation phrase actually occurs, and that nothing
    substantive remains once it, the particles and the punctuation are removed.
    Merely "becoming empty" is not enough: a bare "好" would qualify and be
    mistaken for delegation. A reply that mixes delegation with a real value
    ("随便，就二等座吧") keeps its content and is therefore not pure delegation.
    """
    remainder = text or ""
    found = False
    for phrase in _DELEGATION_PHRASES:
        if phrase in remainder:
            found = True
            remainder = remainder.replace(phrase, "")
    if not found:
        return False
    return len(remainder.strip(_PARTICLES)) < 2


def resolve_answer(
    pending: PendingQuestion | None, answer: str
) -> tuple[str, str]:
    """Map a reply to ``(slot, value)``, or ``("", "")`` when nothing is usable.

    Four cases:

    * a confirmation plus an affirmative -> the value the question was about
      ("是的" to "仍然不加糖吗？" resolves to 无糖, not to "是的");
    * a confirmation plus a negative -> no value; the remembered preference does
      not hold this round, which is a state change, not a new fact;
    * a pure delegation ("随便，你看着办吧") -> no value; the user handed the
      decision back rather than stating a preference;
    * anything else -> the reply text is the value for the question's slot.
    """
    if pending is None:
        return ("", "")
    text = (answer or "").strip()
    if not text:
        return ("", "")

    if _is_delegation(text):
        return (pending.slot, "")

    if pending.is_confirmation and pending.value:
        if _is_affirmative(text):
            return (pending.slot, pending.value)
        if _is_negative(text):
            # "不是，这次少糖" both denies the remembered value and states a new
            # one. Returning no value there discarded the correction, which is
            # the failure the user reported in the first place (E-080). The
            # denial is stripped, and whatever remains is the correction.
            remainder = text
            for marker in _NEGATIVE_REPLIES:
                if marker in remainder:
                    remainder = remainder.replace(marker, "", 1)
                    break
            remainder = remainder.strip(_PARTICLES)
            for framing in _CORRECTION_FRAMING:
                if remainder.startswith(framing):
                    remainder = remainder[len(framing) :].strip(_PARTICLES)
                    break
            if len(remainder) >= 2:
                return (pending.slot, remainder)
            return (pending.slot, "")
    # A long reply is a real statement, not a yes/no token.
    if not pending.is_confirmation and (_is_affirmative(text) or _is_negative(text)):
        # "是要的" / "不用了" to an open question carries no value we can use.
        return (pending.slot, "")
    return (pending.slot, text)


class ProactiveEngine:
    """Decide whether a declared gap is worth one question."""

    def __init__(self, max_questions: int = 2) -> None:
        self.max_questions = max_questions
        self.asked_this_subtask: int = 0
        self.asked_questions: set[str] = set()
        self.pending: Optional[PendingQuestion] = None

    @property
    def pending_question(self) -> Optional[str]:
        return self.pending.question if self.pending else None

    def reset_subtask(self) -> None:
        self.asked_this_subtask = 0
        self.asked_questions.clear()
        self.pending = None

    # -- gap selection ------------------------------------------------------

    def open_gaps(self, context: QuestionContext) -> list[str]:
        """Declared, user-only, still-unknown slots, most decisive first."""
        known = {slot for slot, value in (context.known_slots or {}).items() if value}
        resolved = {
            slot for slot, value in (context.resolved_slots or {}).items() if value
        }
        gaps = [
            slot
            for slot in context.unknown_slots or ()
            if slot in USER_ONLY_SLOTS
            and slot not in TOOL_FINDABLE_SLOTS
            and slot not in CONTEXT_RESOLVABLE_SLOTS
            and slot not in known
            and slot not in resolved
            and slot in SLOT_QUESTIONS
        ]
        order = {slot: index for index, slot in enumerate(SLOT_PRIORITY)}
        return sorted(dict.fromkeys(gaps), key=lambda slot: order.get(slot, len(order)))

    def question_for_slot(self, slot: str, context: QuestionContext) -> str:
        """The question for one slot, personalised from structured facts only."""
        base = SLOT_QUESTIONS.get(slot, "")
        if not base:
            return ""
        known = (context.known_slots or {}).get(slot, "")
        if known:
            # A remembered value that still holds is a confirmation, and the
            # gap would not have opened in the first place; this branch only
            # guards a caller that passes an inconsistent context.
            return f"您之前提到{known}，这次还是这样吗？"
        return base

    def propose(
        self,
        instruction: str = "",
        memory_text: str = "",
        domain: Optional[str] = None,
        known_slots: Optional[dict[str, str]] = None,
        *,
        context: Optional[QuestionContext] = None,
    ) -> Optional[Proposal]:
        """Purely propose a question and its slot, or None when no gap is open.

        ``memory_text`` is accepted for call-site compatibility and deliberately
        unused: deciding from rendered prose is what produced the echoed-dislike
        defect. Only ``context`` (compiler slots + structured facts) is read.
        """
        if self.asked_this_subtask >= self.max_questions:
            return None

        if context is None:
            context = QuestionContext(
                instruction=instruction or "",
                domain=domain or "",
                known_slots=dict(known_slots or {}),
            )

        for slot in self.open_gaps(context):
            question = self.question_for_slot(slot, context)
            if not question or question in self.asked_questions:
                continue
            known = (context.known_slots or {}).get(slot, "")
            return Proposal(
                question=question,
                slot=slot,
                value=known,
                is_confirmation=bool(known),
            )
        return None

    def propose_question(
        self,
        instruction: str = "",
        memory_text: str = "",
        domain: Optional[str] = None,
        known_slots: Optional[dict[str, str]] = None,
        *,
        context: Optional[QuestionContext] = None,
    ) -> Optional[str]:
        """String-returning wrapper around :meth:`propose`."""
        proposal = self.propose(
            instruction,
            memory_text,
            domain,
            known_slots,
            context=context,
        )
        return proposal.question if proposal else None

    def decide_to_ask(
        self, instruction: str, memory_text: str, domain: Optional[str]
    ) -> Optional[str]:
        """Backward-compatible alias for :meth:`propose_question`."""
        return self.propose_question(instruction, memory_text, domain)

    # -- budget and answer association --------------------------------------

    def commit_question(
        self,
        question: str,
        *,
        slot: str = "",
        value: str = "",
        polarity: str = "",
        is_confirmation: bool = False,
    ) -> bool:
        """Record a question only when it was actually sent to the user.

        ``slot``/``value`` describe what the question was about, so the reply can
        be resolved to a dimension instead of being stored as a bare "yes".
        """
        normalized = (question or "").strip()
        if not normalized or normalized in self.asked_questions:
            return False
        if self.asked_this_subtask >= self.max_questions:
            return False
        self.asked_questions.add(normalized)
        self.asked_this_subtask += 1
        self.pending = PendingQuestion(
            question=normalized,
            slot=slot,
            value=value,
            polarity=polarity,
            is_confirmation=is_confirmation,
        )
        return True

    def record_answer(self, question: str, answer: str, memory) -> tuple[str, str]:
        """Store the answer as a resolved fact. Returns ``(slot, value)``.

        The stored value is what the reply *means* for the slot, not the reply
        text: an affirmative answer to a confirmation stores the confirmed value.
        A reply that resolves to nothing usable leaves memory unchanged.
        """
        from agent.memory.signals import Signal

        pending = self.pending
        slot, value = resolve_answer(pending, answer)
        self.pending = None
        if not value:
            return (slot, "")

        sig = Signal(
            predicate="explicit_preference",
            object=value,
            confidence=0.95,
            timestamp="",
            type="conversation",
            raw=f"主动询问: {question}; 用户回答: {answer}",
            importance=8.0,
        )
        memory.stream.add(sig)
        return (slot, value)
