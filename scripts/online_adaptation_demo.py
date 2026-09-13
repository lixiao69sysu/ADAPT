"""Online adaptation demo: the memory changes its own state within a session.

This is the honest version of the "self-evolving" keyword. It does **not** claim
an agent that improves itself; it demonstrates something narrower and verifiable:
the memory layer adapts online, from a user's answer, and the adaptation changes
what the agent does next -- without any model call in this script.

Two kinds of evidence are printed, and they are kept visibly separate:

REAL     excerpts read out of a saved smoke checkpoint. This shows the model
         actually asked the question the policy proposed, and that the loop
         recorded it. Source: data/simulations/smoke_proactive_M793481_sub1.json

DERIVED  a zero-model replay of the same instruction through the memory layer,
         showing the state transitions. Where the real user delegated, the
         closure case is shown as an explicitly-labelled counterfactual, because
         the real reply did not close the gap.

Run:
    python scripts/online_adaptation_demo.py
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent.vitabench_bootstrap as bootstrap  # noqa: E402

bootstrap.enable_vitabench_utf8()

from agent.decision import TaskSpec  # noqa: E402
from agent.memory.adapt_memory import ADAPTMemory  # noqa: E402
from agent.memory.proactive import QuestionContext  # noqa: E402
from agent.memory.slots import resolve_preference_slots  # noqa: E402

SMOKE = pathlib.Path("data/simulations/smoke_proactive_M793481_sub1.json")
INSTRUCTION = "想去昆明玩，你帮我定个这周六的票吧。"


def _rule(title: str) -> None:
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def real_evidence() -> None:
    """What the saved run actually did, read from the checkpoint."""
    _rule("REAL  what the saved smoke run did")
    if not SMOKE.exists():
        print(f"  {SMOKE} not found; skipping the real-evidence section")
        return
    with SMOKE.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    for simulation in payload.get("simulations", []):
        states = simulation.get("states") or {}
        # "proactive_loop" is the key the pre-E-086 checkpoints used; the single
        # AdaptAgent writes "adapt_agent".
        loop = states.get("adapt_agent") or states.get("proactive_loop")
        print(f"  loop counters: {loop}")
        for traj in states.get("integrity_subtask_trajectories") or []:
            asked = ""
            reply = ""
            seen_question = False
            for message in traj.get("messages") or []:
                content = message.get("content")
                if not isinstance(content, str):
                    continue
                if message.get("role") == "assistant" and content.strip():
                    if not asked and any(
                        marker in content for marker in ("方式", "高铁", "飞机")
                    ):
                        asked = content.strip()
                        seen_question = True
                elif message.get("role") == "user" and seen_question and not reply:
                    # The reply is the user turn that follows the question, not
                    # the subtask instruction that opened the conversation.
                    reply = content.strip()
            if asked:
                print(f"  the agent asked : {asked[:96]}")
            print(f"  the user replied: {reply[:96] or '(none captured)'}")


def _context(instruction: str, memory: ADAPTMemory) -> QuestionContext:
    spec = TaskSpec.compile(instruction)
    return QuestionContext(
        instruction=instruction,
        domain=spec.domain,
        facet=spec.facet,
        action=spec.action,
        unknown_slots=tuple(spec.unknown_slots or ()),
        resolved_slots={
            slot: str(value)
            for slot, value in (spec.resolved_slots or {}).items()
            if value
        },
        known_slots=dict(resolve_preference_slots(spec, memory.facts) or {}),
    )


def _state(memory: ADAPTMemory, label: str) -> None:
    facts = [(f.value, f.dimension, f.polarity) for f in memory.facts]
    gaps = memory.proactive.open_gaps(_context(INSTRUCTION, memory))
    if not gaps:
        action = "proceed, nothing to ask"
    else:
        proposal = memory.proactive.propose(context=_context(INSTRUCTION, memory))
        # An open gap whose identical question was already sent will not be
        # repeated (one identical question is counted once), so say that rather
        # than implying the same sentence comes back.
        action = (
            f"ask about {gaps[0]}"
            if proposal is not None
            else f"{gaps[0]} is still unresolved, but the same question is not repeated"
        )
    print(f"  {label}")
    print(f"      facts        : {facts}")
    print(f"      open gaps    : {gaps}")
    print(f"      next action  : {action}")


def derived_timeline() -> None:
    """Zero-model replay of the memory's state transitions."""
    _rule("DERIVED  the memory adapting, step by step (no model call)")
    memory = ADAPTMemory()
    memory.begin_subtask(INSTRUCTION)
    _state(memory, "step 1  the subtask starts")

    proposal = memory.propose(INSTRUCTION)
    print()
    print(f"  step 2  the policy declares a gap and proposes: {proposal.question!r}")
    print(f"          (slot={proposal.slot!r}; proposing spends no budget)")
    memory.commit_question(
        proposal.question,
        slot=proposal.slot,
        value=proposal.value,
        is_confirmation=proposal.is_confirmation,
    )
    print(f"          committed -> asked_this_subtask={memory.proactive.asked_this_subtask}")

    print()
    print("  step 3a  the REAL reply was a delegation:")
    memory.record_user_answer("随便，你看着办吧。")
    _state(memory, "after '随便，你看着办吧。'")

    print()
    print("  step 3b  COUNTERFACTUAL (not observed in the run) -- an answer that")
    print("           names a value would instead resolve to the slot and close it:")
    memory2 = ADAPTMemory()
    memory2.begin_subtask(INSTRUCTION)
    proposal2 = memory2.propose(INSTRUCTION)
    memory2.commit_question(proposal2.question, slot=proposal2.slot)
    memory2.record_user_answer("坐高铁吧")
    _state(memory2, "after '坐高铁吧'")


def main() -> int:
    print("Online adaptation of the ADAPT memory layer")
    print(f"session instruction: {INSTRUCTION!r}")
    real_evidence()
    derived_timeline()
    _rule("WHAT THIS DOES AND DOES NOT SHOW")
    print("  SHOWS   a declared gap -> a question -> an answer resolved to a slot")
    print("          value -> the gap closes -> the policy does not re-ask.")
    print("  SHOWS   the model really asked the proposed question in a saved run.")
    print("  DOES NOT SHOW  that this improves any score. No evaluation was run,")
    print("          and the real reply in the saved run delegated rather than")
    print("          answered, so the closure step is counterfactual here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
