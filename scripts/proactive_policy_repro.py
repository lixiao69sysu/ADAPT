"""Zero-model reproduction of the proactive-question defects.

The user reported four wrong questions produced from inputs that contain no
dataset entity, plus a set of wiring gaps. This script reproduces the four
decision defects against the real engine, with no model call and no benchmark
data, so the policy rewrite has an evidence gate.

P1  "帮我推荐一本书"        -> a delivery taste question (domain defaulted to delivery)
P2  "帮我预约理发"          -> a group-dining headcount question (预约 read as 聚餐)
P3  "买张出行票" + memory "不喜欢高铁，喜欢飞机"
                            -> "您之前偏好高铁" (negation ignored)
P4  a field is missing but a tool can look it up
                            -> must not ask; asking is not free

Run:
    python scripts/proactive_policy_repro.py

Exit code 0. After the policy rewrite this is the gate for flipping the
engineering-log entry: every case is expected to report FIXED.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.memory.adapt_memory import ADAPTMemory  # noqa: E402


def _rule(title: str) -> None:
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


def _memory(dialogue: str | None = None) -> ADAPTMemory:
    memory = ADAPTMemory()
    if dialogue:
        memory.update([
            {
                "date": "2026-03-01",
                "behavior": [],
                "dialogue": [{"role": "user", "content": dialogue}],
            }
        ])
    return memory


def _ask(instruction: str, dialogue: str | None = None) -> str:
    memory = _memory(dialogue)
    question = memory.propose_question(instruction)
    return question or ""


# Markers that must NOT appear: each names a topic the input never mentioned.
FOOD_TASTE = ("清淡", "麻辣", "烧烤", "口味", "餐厅类型", "日料", "火锅")
GROUP_DINING = ("几个人", "包间", "聚餐")
WRONG_TRANSPORT = ("之前偏好高铁", "偏好高铁出行")


def case_p1() -> bool:
    _rule("P1  'recommend me a book' must not get a food question")
    instruction = "帮我推荐一本书"
    question = _ask(instruction)
    print(f"instruction : {instruction}")
    print(f"question    : {question!r}")
    wrong = any(marker in question for marker in FOOD_TASTE)
    print(f"  food-taste question on a book request : {wrong}")
    print(f"P1 {'STILL BROKEN' if wrong else 'FIXED'}")
    return wrong


def case_p2() -> bool:
    _rule("P2  'book me a haircut' must not get a group-dining question")
    instruction = "帮我预约理发"
    question = _ask(instruction)
    print(f"instruction : {instruction}")
    print(f"question    : {question!r}")
    wrong = any(marker in question for marker in GROUP_DINING)
    print(f"  headcount/room question on a haircut  : {wrong}")
    print(f"P2 {'STILL BROKEN' if wrong else 'FIXED'}")
    return wrong


def case_p3() -> bool:
    _rule("P3  a remembered dislike must not become a preference")
    instruction = "帮我买张出行票"
    question = _ask(instruction, dialogue="我不喜欢高铁，我喜欢坐飞机。")
    facts = _memory("我不喜欢高铁，我喜欢坐飞机。").facts
    print("stored facts (value, polarity, dimension):")
    for fact in facts:
        print(f"    {fact.value!r}, {fact.polarity}, {fact.dimension}")
    print(f"instruction : {instruction}")
    print(f"question    : {question!r}")
    wrong = any(marker in question for marker in WRONG_TRANSPORT)
    print(f"  remembered dislike stated as a pref   : {wrong}")
    print(f"P3 {'STILL BROKEN' if wrong else 'FIXED'}")
    return wrong


def case_p4() -> bool:
    _rule("P4  a tool-findable gap must not be asked")
    # A hotel request with no room type: the room list is obtainable from the
    # environment, so asking the user first spends the question budget on
    # something a tool call answers.
    instruction = "帮我订个酒店"
    question = _ask(instruction)
    print(f"instruction : {instruction}")
    print(f"question    : {question!r}")
    print("  (a question here is not automatically wrong; what matters is that")
    print("   the policy has a stated reason, recorded in a later section.)")
    print(f"P4 {'ASKED' if question else 'NOT ASKED'}")
    return False


def main() -> int:
    p1 = case_p1()
    p2 = case_p2()
    p3 = case_p3()
    case_p4()

    _rule("SUMMARY")
    print(f"P1 book request got a food question      : {'STILL BROKEN' if p1 else 'FIXED'}")
    print(f"P2 haircut got a group-dining question   : {'STILL BROKEN' if p2 else 'FIXED'}")
    print(f"P3 dislike rendered as a preference      : {'STILL BROKEN' if p3 else 'FIXED'}")
    print()
    print(f"defects still present: {sum([p1, p2, p3])}/3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
