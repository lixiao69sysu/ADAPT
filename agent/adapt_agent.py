"""THE single ADAPT agent: stock skeleton plus observation of the proactive loop.

ADAPT ships exactly one agent class. It is the stock ``PersonalizationAgent``
skeleton plus per-turn *observation* of the proactive question loop -- nothing
else. It may only observe and carry state; it never decides.

The stock loop leaves three transitions open (E-069):

1. nothing records that the suggested question was actually sent, so the budget
   is never spent and the same question can be proposed forever;
2. a user reply never reaches the question state, so the answer is dropped;
3. a reply is stored as its literal text, so answering "是的" to "这次仍然不加糖
   吗？" produced the fact ``value="是的"`` instead of 无糖.

This agent closes them. It is deliberately **not** a controller: it never
rewrites, replaces, reorders or preempts the model's message, and it never
blocks a tool call or a write. Its whole job is two bookkeeping transitions
around ``super().generate_next_message``:

* an inbound user turn that answers a question we sent -> link the answer;
* an outbound assistant turn that actually asked the proposed question -> commit
  it, which is the only place the question budget is spent.

Both are observations of things that already happened, which keeps this inside
invariant (a) of the design spine: pass through observed values. The failures
that made a controller net-negative (E-042, E-048, E-049) all came from a
framework *speaking or deciding* for the model; nothing here does.

This is the only agent class the repository carries. The three other paths --
the bounded evidence reviewer, the frozen candidate marker and the thrash guard
-- were never measured or measured with no gain, and were removed (E-086) so
that "change one agent, then measure" is possible at all. This convergence does
not by itself change any score.

A **second** mechanism lives here behind its own switch,
``enable_candidate_evidence`` (default off, E-087). When a tool result arrives it
reports, per candidate, which of the current instruction's constraints are
satisfied / violated / unknown -- a three-valued relation, with polarity carried
by the constraint, so a ``forbid`` can never be read as a positive. The block is
**appended** to a ``deepcopy`` of the message: the environment's own object, and
therefore the trajectory the evaluator and the offline audits read, stays
byte-identical. It appends and never alters, reorders, removes or re-renders what
the environment produced, and it makes no score claim -- it is unmeasured and off
by default.

A **third** mechanism lives here behind its own switch, ``enable_task_state``
(default off, E-091). E-089 attributed 108 of the 283 failing baseline runs
(38.2%) to two adjacent mechanisms that share one root cause: ``planning_defect``
(66 runs, 23.3% -- searched, presented options, never attempted a write) and
``over_asking`` (42 runs, 14.8% -- asked while every slot the task required was
already settled). The common cause is that the agent carries no representation of
**what this subtask still needs and what it already has**, so it cannot tell "am
I done?" from "am I missing something?".

This mechanism reports that state, and nothing else:

```
【本任务状态】必需字段：product=已定 | address=已定 | time=待定　已观察候选：3 商家 / 12 商品　已发起写入：否　已提问：否
```

Every field is derived from something the repository already computes: the slots
come from ``TaskSpec.compile`` (plus the memory backend's own slot resolution),
the candidate counts come from the ledger's parse of the ids the tool results
actually printed, and the two flags are observations of events that already
happened in this subtask. It contains **no imperative, no recommendation and no
next action**: reporting that every required slot is settled is not the same as
telling the model to write, and this block never crosses that line. A reviewer
reading it cannot tell what the agent is being told to do, which is exactly the
difference between this repository's supported observer and its retired
controller (E-042, E-048, E-049, E-050). ``agent/tests/test_task_state.py`` gates
that property word by word.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from vita.agent.llm_agent import LLMAgentState
from vita.agent.personalization_agent import PersonalizationAgent

from agent.candidate_ledger import CandidateLedger
from agent.decision import TaskSpec, is_commit_tool
from agent.runtime.correspondence import (
    build_vocabulary,
    constraints_from_card,
    correspondence_for_candidate,
    render_correspondence_block,
)

_NORMALIZE_RE = re.compile(r"[\s，。！？、；：,.!?;:~～\"'“”‘’()（）]+")

#: Hard ceiling on what one tool message may gain (E-087). The observation is an
#: aid, not a payload: past this bound it stops being cheap to read and starts
#: competing with the environment's own result for attention.
_MAX_EVIDENCE_CHARS = 600

#: How many observed candidates one tool result may contribute.
_MAX_EVIDENCE_CANDIDATES = 8

_EVIDENCE_HEADER = "\n[候选-约束对应观察]\n"

#: Hard ceiling on what one turn's task-state observation may gain (E-091).
#: Same reasoning as ``_MAX_EVIDENCE_CHARS``: the observation is an aid, not a
#: payload, and it must not compete with the environment's own result.
_MAX_TASK_STATE_CHARS = 600

#: The block rides on its own line. Appending a new line is the only edit made
#: to the copy; the environment's text is never rewritten or re-flowed.
_TASK_STATE_PREFIX = "\n【本任务状态】"

#: The two words a slot's state can be reported as. Fixed vocabulary, so a
#: reviewer can never read a directive out of a status word.
_SETTLED_LABEL = "已定"
_OPEN_LABEL = "待定"

_FIELD_SEPARATOR = "\u3000"

# Rendering of the compiler's *own* entity-type vocabulary (the table in
# ``decision._entity_type``). This is a display map for a schema the repository
# already owns; it is never matched against user text, instructions or tool
# output, so it adds no lexicon and no benchmark-specific rule.
_ENTITY_LABELS: dict[str, str] = {
    "store": "商家",
    "shop": "商家",
    "product": "商品",
    "hotel": "酒店",
    "attraction": "景点",
    "flight": "航班",
    "train": "车次",
}
_ENTITY_LABEL_ORDER: tuple[str, ...] = (
    "商家",
    "商品",
    "酒店",
    "景点",
    "航班",
    "车次",
)


def task_state_slots(
    instruction: str, memory_resolved: dict[str, Any] | None = None
) -> list[tuple[str, bool]]:
    """``(slot, settled)`` for each slot the instruction requires.

    Purely derived from the compiler (E-091): ``TaskSpec.compile`` already
    declares ``required_slots`` and ``unknown_slots``, and the memory backend's
    own slot resolution supplies the rest. A slot is settled when the compiler
    does not list it as unknown, or when a value for it was resolved from the
    instruction or from structured memory. Nothing is inferred here and no slot
    name is invented -- an instruction the compiler finds no requirement in
    yields an empty list, and the block then reports only what it did observe.
    """
    spec = TaskSpec.compile(instruction or "")
    resolved = {
        str(slot): value
        for slot, value in (spec.resolved_slots or {}).items()
        if value
    }
    for slot, value in (memory_resolved or {}).items():
        if value:
            resolved[str(slot)] = value
    unknown = {str(slot) for slot in (spec.unknown_slots or [])}
    return [
        (str(slot), not (str(slot) in unknown and not resolved.get(str(slot))))
        for slot in (spec.required_slots or [])
    ]


def observed_candidate_counts(ledger: CandidateLedger) -> dict[str, int]:
    """How many candidate ids the tool results actually printed, by kind.

    The count comes from the ledger's own parse of the printed records; a kind
    with nothing printed is simply absent. No id is looked up, resolved or
    guessed, so the number can only ever be one the environment produced.
    """
    counts: dict[str, int] = {}
    for candidate in getattr(ledger, "candidates", {}).values():
        label = _ENTITY_LABELS.get(str(getattr(candidate, "entity_type", "")))
        if label:
            counts[label] = counts.get(label, 0) + 1
    return counts


def render_task_state_block(
    slots: list[tuple[str, bool]],
    *,
    candidate_counts: dict[str, int] | None = None,
    write_attempted: bool = False,
    question_asked: bool = False,
    max_chars: int = _MAX_TASK_STATE_CHARS,
) -> str:
    """The bounded task-state line, or ``""`` when there is nothing to report.

    The block is **state only**. Every sentence is a statement about something
    already observed in this subtask -- what the compiler requires, what the
    tools printed, whether a commit call was issued, whether a question was
    sent. There is deliberately no imperative, no recommendation and no next
    action: "all slots settled" is a fact about the subtask, not an instruction
    to write, and the model remains the only decider.

    Rendering is bounded by construction: whole field tokens are added while
    they fit inside ``max_chars``, so a token is never cut in half.
    """
    required = [(str(slot), bool(settled)) for slot, settled in slots if slot]
    counts = {
        str(label): int(value)
        for label, value in (candidate_counts or {}).items()
        if int(value) > 0
    }
    if not required and not counts:
        # Nothing was compiled and nothing was printed: appending a block of
        # flags alone would be noise, so nothing is appended.
        return ""

    tail_parts: list[str] = []
    if counts:
        rendered_counts = " / ".join(
            f"{counts[label]} {label}"
            for label in _ENTITY_LABEL_ORDER
            if counts.get(label)
        )
        if rendered_counts:
            tail_parts.append(f"已观察候选：{rendered_counts}")
    tail_parts.append(f"已发起写入：{'是' if write_attempted else '否'}")
    tail_parts.append(f"已提问：{'是' if question_asked else '否'}")
    tail = _FIELD_SEPARATOR.join(tail_parts)

    head = f"{_TASK_STATE_PREFIX}必需字段："
    accepted: list[str] = []
    for slot, settled in required:
        token = f"{slot}={_SETTLED_LABEL if settled else _OPEN_LABEL}"
        candidate = " | ".join([*accepted, token])
        if len(head) + len(candidate) + len(_FIELD_SEPARATOR) + len(tail) > max_chars:
            break
        accepted.append(token)
    slots_text = " | ".join(accepted) if accepted else "（无）"
    block = f"{head}{slots_text}{_FIELD_SEPARATOR}{tail}"
    # Defensive only: the loop above already guarantees the bound for any tail
    # that fits, and the tail is a fixed handful of status fields.
    return block[:max_chars]


def _inbound_tool_messages(message: Any) -> list[Any]:
    """The tool messages one inbound turn carries.

    The vendored loop delivers a single tool result as a bare ``ToolMessage``
    and only wraps *several* results in a ``MultiToolMessage``
    (``vita/orchestrator/orchestrator.py``). Both shapes must be handled, or a
    mechanism that only knows the wrapper silently never fires on the common
    single-call path -- the exact failure recorded in E-053.
    """
    nested = getattr(message, "tool_messages", None)
    if nested:
        return list(nested)
    if getattr(message, "role", None) == "tool":
        return [message]
    return []


def _normalize(text: str) -> str:
    return _NORMALIZE_RE.sub("", text or "")


def _bigrams(text: str) -> set[str]:
    return {text[index : index + 2] for index in range(len(text) - 1)}


def distinctive_bigrams(slot: str) -> set[str]:
    """Bigrams that belong to one slot's question and no other.

    Measuring how much of the *whole* question the model reproduced fails on
    paraphrases, because the generic framing ("这次出行您想用哪种方式") dominates
    the count and the model's phrasing is terser. What identifies the question is
    its distinctive content (高铁/飞机/出行), and that is derivable from the slot
    table itself rather than from a hand-written keyword list.
    """
    from agent.memory.proactive import SLOT_QUESTIONS

    mine = _bigrams(_normalize(SLOT_QUESTIONS.get(slot, "")))
    if not mine:
        return set()
    others: set[str] = set()
    for other_slot, text in SLOT_QUESTIONS.items():
        if other_slot != slot:
            others |= _bigrams(_normalize(text))
    return mine - others


_QUESTION_MARKERS = (
    "？",
    "?",
    "吗",
    "呢",
    "请问",
    "还是",
    "哪种",
    "哪一",
    "什么",
    "多少",
    "几",
    "which",
    "how many",
)


def _looks_like_a_question(message: str) -> bool:
    lowered = (message or "").casefold()
    return any(marker in lowered for marker in _QUESTION_MARKERS)


def asked_the_question(
    question: str,
    message: str,
    *,
    slot: str = "",
    threshold: float = 0.7,
    distinctive_hits: int = 2,
) -> bool:
    """Whether ``message`` actually asked ``question``.

    The model paraphrases, so exact containment is too strict, but a loose
    keyword test would spend the budget on a question that was never asked.
    Either suffices:

    * the whole question appears in the message; or
    * the message is phrased as a question **and** reproduces at least
      ``distinctive_hits`` of the content bigrams unique to this slot's question
      within the slot table.

    A ratio would be the wrong statistic: the generic framing dominates the
    question's bigram count while the model's phrasing is much terser, so a
    faithful short paraphrase scores low. An absolute count of distinctive hits
    plus the question-form guard separates "asked about transport" from "merely
    mentioned a train ticket".
    """
    target = _normalize(question)
    body = _normalize(message)
    if not target or not body:
        return False
    if target in body:
        return True

    grams = _bigrams(target)
    if len(grams) < 3:
        return False

    distinctive = distinctive_bigrams(slot) if slot else set()
    if distinctive and _looks_like_a_question(message):
        if len(distinctive & _bigrams(body)) >= distinctive_hits:
            return True

    return len(grams & _bigrams(body)) / len(grams) >= threshold


class AdaptAgent(PersonalizationAgent):
    """The single ADAPT agent.

    With both switches off (the defaults) every hook is skipped and
    ``generate_next_message`` returns ``super()`` unchanged, so the class is a
    **verified pass-through**: ``--agent adapt`` then behaves exactly like the
    stock skeleton, and the only difference between the two arms is the memory
    backend. That is the point of the defaults -- each mechanism is added behind
    its own switch so its effect is measured against a clean base rather than
    against a pile of unmeasured edits.

    With ``enable_proactive_loop`` on it observes the proactive question loop: it
    records that a proposed question was actually sent, and links the user's reply
    to the slot the question was about. It never decides.

    With ``enable_candidate_evidence`` on it appends a bounded three-valued
    candidate/constraint observation to a copy of an inbound tool result.

    With ``enable_task_state`` on it appends a bounded, non-directive statement
    of what this subtask requires, what it has already observed and whether a
    write or a question has happened yet. The three switches are independent and
    measured separately.
    """

    def __init__(
        self,
        *args: Any,
        enable_proactive_loop: bool = False,
        enable_candidate_evidence: bool = False,
        enable_task_state: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.enable_proactive_loop = enable_proactive_loop
        self.enable_candidate_evidence = enable_candidate_evidence
        self.enable_task_state = enable_task_state
        # Per-subtask counters, exposed for trace attribution.
        self.questions_committed = 0
        self.answers_linked = 0
        self.answers_resolved_to_a_value = 0
        self.tool_results_annotated = 0
        self.candidate_constraint_pairs_resolved = 0
        # Task-state bookkeeping (E-091). The ledger is built lazily so that a
        # run with every switch off constructs nothing extra.
        self.task_states_annotated = 0
        self.questions_observed = 0
        self.write_attempted = False
        self._task_state_ledger: CandidateLedger | None = None

    # -- state transitions --------------------------------------------------

    def set_current_instruction(self, instruction: str) -> None:
        super().set_current_instruction(instruction)
        if self.enable_task_state:
            self._reset_task_state()
        if not self.enable_proactive_loop:
            # begin_subtask resets the proactive engine. It is unobservable while
            # the loop is off, but skipping it keeps the pass-through exact.
            return
        begin = getattr(self.memory, "begin_subtask", None)
        if callable(begin):
            begin(instruction)

    @property
    def loop_events(self) -> dict[str, int | bool]:
        return {
            "enabled": self.enable_proactive_loop,
            "questions_committed": self.questions_committed,
            "answers_linked": self.answers_linked,
            "answers_resolved_to_a_value": self.answers_resolved_to_a_value,
            "candidate_evidence_enabled": self.enable_candidate_evidence,
            "tool_results_annotated": self.tool_results_annotated,
            "candidate_constraint_pairs_resolved": (
                self.candidate_constraint_pairs_resolved
            ),
            "task_state_enabled": self.enable_task_state,
            "task_states_annotated": self.task_states_annotated,
        }

    def generate_next_message(
        self, message: Any, state: LLMAgentState
    ) -> tuple[Any, LLMAgentState]:
        if (
            not self.enable_proactive_loop
            and not self.enable_candidate_evidence
            and not self.enable_task_state
        ):
            # Verified pass-through: no memory read, no memory write, no change to
            # the message. Asserted by
            # test_adapt_agent_defaults_to_a_pass_through.
            return super().generate_next_message(message, state)
        if self.enable_candidate_evidence:
            # Append-only and on a copy: the environment's own message object --
            # and therefore the trajectory the evaluator reads -- is untouched.
            message = self._annotate_tool_evidence(message)
        if self.enable_task_state:
            # Same shape, same guarantee: the subtask's own state is appended to
            # a copy, after the evidence block, and the original stays as the
            # environment produced it.
            message = self._annotate_task_state(message)
        if self.enable_proactive_loop:
            self._observe_inbound(message)
        assistant_message, state = super().generate_next_message(message, state)
        if self.enable_proactive_loop:
            self._observe_outbound(assistant_message)
        if self.enable_task_state:
            self._observe_task_state_outbound(assistant_message)
        return assistant_message, state

    # -- candidate evidence (E-087) -----------------------------------------

    def _annotate_tool_evidence(self, message: Any) -> Any:
        """Return ``message``, or a copy of it carrying one appended block.

        The relation is three-valued (satisfied / violated / unknown) and its
        polarity is carried by the constraint, so a prohibition is never read as
        a preference. Nothing here chooses, ranks, blocks or rewrites: the
        environment's records are reproduced verbatim and the observation is only
        ever *added*, bounded by ``_MAX_EVIDENCE_CHARS``.

        The original object is never mutated, and any failure returns it
        unchanged -- bookkeeping must never break a run.
        """
        try:
            if not _inbound_tool_messages(message):
                return message
            constraints = self._current_constraints()
            if not constraints:
                return message
            annotated = deepcopy(message)
            annotated_any = False
            for tool_message in _inbound_tool_messages(annotated):
                appended = self._evidence_addition(tool_message, constraints)
                if not appended:
                    continue
                tool_message.content = (
                    getattr(tool_message, "content", "") or ""
                ) + appended
                self.tool_results_annotated += 1
                annotated_any = True
            return annotated if annotated_any else message
        except Exception:  # noqa: BLE001 - bookkeeping must not break the run
            return message

    def _current_constraints(self) -> list[Any]:
        """The current instruction's compiled constraints, or an empty list.

        The constraints come from the memory backend's own compiler, so nothing
        is re-derived here and no fact is invented. A backend without
        ``compile_task`` (the stock rewrite memory) yields no observation at all:
        the mechanism goes inert rather than guessing. Read-only --
        ``compile_task`` is a pure function of the instruction and the fact store,
        so annotating a tool result cannot change what the next turn remembers.
        """
        instruction = self._current_instruction or ""
        if not instruction:
            return []
        compile_task = getattr(self.memory, "compile_task", None)
        if not callable(compile_task):
            return []
        return constraints_from_card(compile_task(instruction))

    def _evidence_addition(
        self, tool_message: Any, constraints: list[Any]
    ) -> str:
        """The bounded text this one tool result may append, or ``""``."""
        content = getattr(tool_message, "content", None)
        if not content:
            return ""
        ledger = CandidateLedger()
        ledger.observe(str(getattr(tool_message, "name", "") or ""), content)
        candidates = list(ledger.candidates.values())[:_MAX_EVIDENCE_CANDIDATES]
        if not candidates:
            return ""
        vocabulary = build_vocabulary(candidates)
        for candidate in candidates:
            self.candidate_constraint_pairs_resolved += len(
                correspondence_for_candidate(candidate, constraints, vocabulary)
            )
        block = render_correspondence_block(
            candidates,
            constraints,
            vocabulary=vocabulary,
            max_candidates=_MAX_EVIDENCE_CANDIDATES,
            max_chars=_MAX_EVIDENCE_CHARS - len(_EVIDENCE_HEADER),
        )
        if not block:
            return ""
        return _EVIDENCE_HEADER + block

    # -- task state (E-091) -------------------------------------------------

    def _reset_task_state(self) -> None:
        """Start a fresh subtask: no candidates, no write, no question yet."""
        self._task_state_ledger = CandidateLedger()
        self.write_attempted = False
        self.questions_observed = 0

    def _annotate_task_state(self, message: Any) -> Any:
        """Return ``message``, or a copy of it carrying one appended state line.

        The block states what this subtask requires, what its tool results have
        printed, and whether a write or a question has already happened. Nothing
        here chooses, ranks, blocks or rewrites: the environment's records are
        reproduced verbatim and the observation is only ever *added*, bounded by
        ``_MAX_TASK_STATE_CHARS``.

        The original object is never mutated, and any failure returns it
        unchanged -- bookkeeping must never break a run.
        """
        if not self.enable_task_state:
            return message
        try:
            tool_messages = _inbound_tool_messages(message)
            if not tool_messages:
                return message
            if self._task_state_ledger is None:
                self._task_state_ledger = CandidateLedger()
            for tool_message in tool_messages:
                self._task_state_ledger.observe(
                    str(getattr(tool_message, "name", "") or ""),
                    getattr(tool_message, "content", "") or "",
                )
            block = self._task_state_block()
            if not block:
                return message
            annotated = deepcopy(message)
            targets = _inbound_tool_messages(annotated)
            if not targets:
                return message
            # The block describes the *subtask*, not this one result. A
            # MultiToolMessage is extended into several separate messages in the
            # conversation, so it is attached to the first of them only.
            targets[0].content = (
                getattr(targets[0], "content", "") or ""
            ) + block
            self.task_states_annotated += 1
            return annotated
        except Exception:  # noqa: BLE001 - bookkeeping must not break the run
            return message

    def _task_state_block(self) -> str:
        """The bounded state line for the current subtask, or ``""``."""
        instruction = self._current_instruction or ""
        if not instruction:
            return ""
        memory_resolved: dict[str, Any] = {}
        resolve = getattr(self.memory, "resolve_task_slots", None)
        if callable(resolve):
            # Structured memory resolution, when the backend has it. A backend
            # without it (the stock rewrite memory) contributes nothing rather
            # than guessing, so the block never claims a slot is settled on
            # evidence that does not exist.
            try:
                memory_resolved = dict(resolve(instruction) or {})
            except Exception:  # noqa: BLE001 - bookkeeping must not break the run
                memory_resolved = {}
        counts = (
            observed_candidate_counts(self._task_state_ledger)
            if self._task_state_ledger is not None
            else {}
        )
        return render_task_state_block(
            task_state_slots(instruction, memory_resolved),
            candidate_counts=counts,
            write_attempted=self.write_attempted,
            question_asked=self._question_was_asked(),
        )

    def _question_was_asked(self) -> bool:
        """Whether a question has already been sent in this subtask.

        Two observations of things that already happened, OR-ed: the proactive
        engine's committed-question count (exact, but only maintained while the
        proactive loop is on) and this mechanism's own count of outbound turns
        phrased as a question. Under-reporting here would state "已提问：否"
        about a question the model did send, which would be false.
        """
        if self.questions_observed:
            return True
        proactive = getattr(self.memory, "proactive", None)
        return bool(getattr(proactive, "asked_this_subtask", 0))

    def _observe_task_state_outbound(self, assistant_message: Any) -> None:
        """Record a commit call and a question-shaped turn, if either happened."""
        if not self.enable_task_state or assistant_message is None:
            return
        try:
            for call in getattr(assistant_message, "tool_calls", None) or []:
                name = str(getattr(call, "name", "") or "")
                if is_commit_tool(name):
                    self.write_attempted = True
            content = getattr(assistant_message, "content", "") or ""
            if isinstance(content, str) and _looks_like_a_question(content):
                self.questions_observed += 1
        except Exception:  # noqa: BLE001 - bookkeeping must not break the run
            return

    # -- observations -------------------------------------------------------

    def _observe_inbound(self, message: Any) -> None:
        """Link a user reply to the question it answers, if one is pending."""
        if getattr(message, "role", None) != "user":
            return
        record = getattr(self.memory, "record_user_answer", None)
        if not callable(record):
            return
        if getattr(self.memory, "proactive", None) is None:
            return
        if getattr(self.memory.proactive, "pending", None) is None:
            # No question is outstanding. This is the subtask instruction or an
            # unprompted remark; neither is an answer.
            return
        content = getattr(message, "content", "") or ""
        if not content.strip():
            return
        before = len(getattr(self.memory, "facts", ()) or ())
        try:
            record(content)
        except Exception:  # noqa: BLE001 - bookkeeping must not break the run
            return
        self.answers_linked += 1
        if len(getattr(self.memory, "facts", ()) or ()) > before:
            self.answers_resolved_to_a_value += 1

    def _observe_outbound(self, assistant_message: Any) -> None:
        """Commit the proposed question when the model actually asked it."""
        if assistant_message is None:
            return
        content = getattr(assistant_message, "content", "") or ""
        if not content:
            return
        propose = getattr(self.memory, "propose", None)
        commit = getattr(self.memory, "commit_question", None)
        if not callable(propose) or not callable(commit):
            return
        instruction = self._current_instruction or ""
        try:
            proposal = propose(instruction)
        except Exception:  # noqa: BLE001 - bookkeeping must not break the run
            return
        if proposal is None:
            return
        if not asked_the_question(proposal.question, content, slot=proposal.slot):
            return
        if commit(
            proposal.question,
            slot=proposal.slot,
            value=proposal.value,
            is_confirmation=proposal.is_confirmation,
        ):
            self.questions_committed += 1
