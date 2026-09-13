"""Offline audit: dump the "commit-type but never wrote" units from a stock run.

Zero-model. Reads only observable trajectory fields (instructions, tool call
names/arguments, tool result text, termination). Reward is used only to bucket
units for inspection, never as a runtime signal.

Usage:
    python scripts/_landing_gap_dump.py <sim.json> <out.md>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.decision import TaskSpec  # noqa: E402

WRITE_PREFIXES = ("create_", "pay_")
WRITE_EXACT = {"instore_book", "instore_reservation"}


def dom_of(names: list[str]) -> str:
    for n in names:
        if n.startswith("delivery_"):
            return "delivery"
        if n.startswith("instore_"):
            return "instore"
        if n.startswith(
            (
                "flight_",
                "train_",
                "hotel_",
                "attractions_",
                "get_ota",
                "create_train",
                "create_flight",
                "create_hotel",
                "pay_train",
                "pay_flight",
                "pay_hotel",
            )
        ):
            return "ota"
    return "?"


def is_write(name: str) -> bool:
    return name.startswith(WRITE_PREFIXES) or name in WRITE_EXACT


def main() -> None:
    sim_path, out_path = Path(sys.argv[1]), Path(sys.argv[2])
    data = json.loads(sim_path.read_text(encoding="utf-8"))
    lines: list[str] = []
    add = lines.append

    add(f"# Landing-gap audit — `{sim_path.name}`")
    add("")
    add("Units whose instruction compiles to `action=commit` but which contain no")
    add("create/book/reservation call. Reward is shown only to bucket the unit.")
    add("")

    n_total = n_gap = 0
    by_domain: dict[str, int] = {}
    n_candidates = 0

    for sim in data.get("simulations") or []:
        info = (sim.get("reward_info") or {}).get("info") or {}
        per = info.get("subtask_rewards") or {}
        for tr in (sim.get("states") or {}).get("integrity_subtask_trajectories") or []:
            idx = tr.get("subtask_idx")
            key = f"subtask_{idx}_reward"
            if key not in per:
                continue
            msgs = tr.get("messages") or []
            instr = next(
                (
                    m["content"].strip()
                    for m in msgs
                    if m.get("role") == "user"
                    and isinstance(m.get("content"), str)
                    and m["content"].strip()
                ),
                "",
            )
            names = [
                c["name"]
                for m in msgs
                for c in (m.get("tool_calls") or [])
                if isinstance(c, dict) and c.get("name")
            ]
            if not instr:
                continue
            n_total += 1
            if TaskSpec.compile(instr).action != "commit":
                continue
            if any(is_write(n) for n in names):
                continue
            n_gap += 1

            # Did any search actually return candidate rows?
            searches: list[tuple[str, str]] = []
            pending: dict[str, str] = {}
            saw_candidates = False
            for m in msgs:
                for c in m.get("tool_calls") or []:
                    if isinstance(c, dict) and c.get("name"):
                        args = c.get("arguments")
                        pending[str(c.get("id"))] = json.dumps(
                            args, ensure_ascii=False
                        )[:160]
                if m.get("role") == "tool":
                    cid = str(m.get("id"))
                    if cid in pending:
                        searches.append((pending.pop(cid), str(m.get("content") or "")))
            for _, content in searches:
                if "(" in content and ("Shop" in content or "Product" in content or "Store" in content or "Train" in content or "Flight" in content or "Hotel" in content or "Attractions" in content):
                    saw_candidates = True
            if saw_candidates:
                n_candidates += 1

            dom = dom_of(names)
            by_domain[dom] = by_domain.get(dom, 0) + 1

            last_assistant = ""
            for m in reversed(msgs):
                if m.get("role") == "assistant" and isinstance(m.get("content"), str) and m["content"].strip():
                    last_assistant = m["content"].strip().replace("\n", " ")
                    break

            add(f"## {sim.get('task_id')} / sub{idx}  [{dom}]  reward={per[key]}")
            add("")
            add(f"- instruction: {instr[:120]}")
            add(f"- searches={len(searches)}  msgs={len(msgs)}  term={tr.get('termination_reason')}")
            add(f"- candidates_returned={saw_candidates}")
            add(f"- tools: {', '.join(names[:14]) or '(none)'}")
            for args, content in searches[:4]:
                head = content.strip().replace("\n", " ")[:90]
                add(f"  - search {args} -> {head}")
            add(f"- last assistant: {last_assistant[:240] or '(silent)'}")
            add("")

    ratio = (n_gap / n_total) if n_total else 0.0
    header = [
        "## Summary",
        "",
        f"- units scanned: {n_total}",
        f"- commit-type units with no write: {n_gap} ({ratio:.1%})",
        f"- of those, a search returned candidates: {n_candidates}",
        f"- by domain: {json.dumps(by_domain, ensure_ascii=False)}",
        "",
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(header + lines) + "\n", encoding="utf-8")
    print(f"wrote {out_path}")
    print(json.dumps({"scanned": n_total, "gap": n_gap, "with_candidates": n_candidates, "by_domain": by_domain}, ensure_ascii=False))


if __name__ == "__main__":
    main()
