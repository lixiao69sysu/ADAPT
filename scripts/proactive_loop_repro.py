"""Zero-model gate for the proactive loop wiring (E-069).

Three transitions the stock loop left open, and how they are reproduced and
checked here without any model call:

L1  A reply was stored as its literal text.
    Answering "是的" to "这次仍然不加糖吗？" produced the fact
    ``value="是的"``, which is useless downstream. The stored value must be the
    confirmed slot value.

L2  A reply that resolves to nothing must not invent a value.
    "不用了" to an open question is a state change, not a new preference, so
    no fact may be created.

L3  The question text must be matched against what the model actually said, and
    the budget spent only then. Paraphrases count; unrelated text does not.

L4  A user turn that is not an answer (the subtask instruction) must not be
    linked to a question.

Run:
    python scripts/proactive_loop_repro.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The vendored prompts load YAML without an encoding, so the process-local
# adapter has to be installed before anything imports vita.agent.
import agent.vitabench_bootstrap as bootstrap  # noqa: E402

bootstrap.enable_vitabench_utf8()

from agent.memory.adapt_memory import ADAPTMemory  # noqa: E402
from agent.memory.proactive import (  # noqa: E402
    PendingQuestion,
    Proposal,
    resolve_answer,
)
from agent.adapt_agent import asked_the_question  # noqa: E402


def _rule(title: str) -> None:
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


def case_l1() -> bool:
    """An affirmative reply to a confirmation resolves to the confirmed value."""
    _rule("L1  '是的' must resolve to the slot value, not to the word 'yes'")

    pending = PendingQuestion(
        question="您之前提到无糖，这次还是这样吗？",
        slot="sweetness",
        value="无糖",
        is_confirmation=True,
    )
    for reply in ("是的", "对", "嗯，还是一样", "是的，不加糖"):
        slot, value = resolve_answer(pending, reply)
        print(f"  {reply!r:16} -> slot={slot!r} value={value!r}")

    _, value = resolve_answer(pending, "是的")
    ok = value == "无糖"
    print(f"  stored value is the slot value, not the reply : {ok}")

    # End to end through the memory layer.
    memory = ADAPTMemory()
    memory.begin_subtask("帮我点杯奶茶")
    memory.commit_question(
        pending.question,
        slot="sweetness",
        value="无糖",
        is_confirmation=True,
    )
    memory.record_user_answer("是的")
    facts = [(f.value, f.dimension) for f in memory.facts]
    print(f"  facts ingested by the memory layer            : {facts}")
    ok = ok and any(v == "无糖" for v, _ in facts) and not any(
        "是的" in v for v, _ in facts
    )
    print(f"L1 {'FIXED' if ok else 'STILL BROKEN'}")
    return ok


def case_l2() -> bool:
    """A reply that resolves to nothing creates no fact."""
    _rule("L2  a bare decline must not become a preference")

    pending = PendingQuestion(
        question="您希望什么时间呢？",
        slot="time",
        value="",
        is_confirmation=False,
    )
    print("  open question + reply '不用了' ->", resolve_answer(pending, "不用了"))
    print("  open question + reply '下午三点' ->", resolve_answer(pending, "下午三点"))

    memory = ADAPTMemory()
    memory.begin_subtask("帮我点杯奶茶")
    memory.commit_question("您希望什么时间呢？", slot="time")
    memory.record_user_answer("不用了")
    declined = len(memory.facts)

    memory2 = ADAPTMemory()
    memory2.begin_subtask("帮我点杯奶茶")
    memory2.commit_question("您希望什么时间呢？", slot="time")
    memory2.record_user_answer("下午三点")
    answered = [(f.value, f.dimension) for f in memory2.facts]

    print(f"  facts after a decline        : {declined} (must be 0)")
    print(f"  facts after a real answer    : {answered}")
    ok = declined == 0 and any(v == "下午三点" for v, _ in answered)
    print(f"L2 {'FIXED' if ok else 'STILL BROKEN'}")
    return ok


def case_l3() -> bool:
    """The budget is spent only when the question was really asked."""
    _rule("L3  commit only when the model actually asked the question")

    proposed = "这次出行您想用哪种方式？比如高铁、飞机或汽车。"
    cases = [
        (proposed, True, "verbatim"),
        ("这次出行您想用哪种方式呢？高铁、飞机还是汽车？", True, "paraphrase"),
        ("请问您想坐高铁还是飞机出行？", True, "terse paraphrase"),
        ("好的，我帮您查一下高铁票。", False, "unrelated tool talk"),
        ("高铁和飞机都可以，我看看。", False, "mentions modes, not a question"),
        ("", False, "empty"),
        ("嗯，明白。", False, "acknowledgement"),
        # A different slot's question must not count as this one.
        ("您想在哪个城市呢？", False, "another slot's question"),
    ]
    results = []
    for message, expected, label in cases:
        actual = asked_the_question(proposed, message, slot="transport")
        results.append(actual == expected)
        print(
            f"  {label:24} expected={expected!s:5} actual={actual!s:5} "
            f"{'ok' if actual == expected else 'MISMATCH'}"
        )
    ok = all(results)
    print(f"L3 {'FIXED' if ok else 'STILL BROKEN'}")
    return ok


def case_l4() -> bool:
    """The subtask instruction is not an answer to anything."""
    _rule("L4  a user turn with no outstanding question is not an answer")

    memory = ADAPTMemory()
    memory.begin_subtask("帮我订个酒店")
    # No commit_question: nothing was asked yet.
    accepted = memory.record_user_answer("帮我订个酒店")
    print(f"  record_user_answer with no pending question -> {accepted}")
    print(f"  facts created                              -> {len(memory.facts)}")
    ok = accepted is False and len(memory.facts) == 0
    print(f"L4 {'FIXED' if ok else 'STILL BROKEN'}")
    return ok


def case_l5() -> bool:
    """The proposal carries its slot so the loop can link the reply."""
    _rule("L5  the proposal carries the slot it is about")

    memory = ADAPTMemory()
    proposal = memory.propose("帮我预约理发")
    print(f"  proposal : {proposal}")
    ok = isinstance(proposal, Proposal) and proposal.slot == "time"
    print(f"  slot is the declared gap ('time') : {ok}")
    print(f"L5 {'FIXED' if ok else 'STILL BROKEN'}")
    return ok


def main() -> int:
    results = [case_l1(), case_l2(), case_l3(), case_l4(), case_l5()]
    _rule("SUMMARY")
    for index, ok in enumerate(results, start=1):
        print(f"L{index}: {'FIXED' if ok else 'STILL BROKEN'}")
    print()
    print(f"transitions still open: {sum(1 for ok in results if not ok)}/{len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
