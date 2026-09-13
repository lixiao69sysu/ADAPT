"""Sub-metrics aligned with what this change actually touches.

The official metric (Avg@4 over the 100 personalization units) is too coarse and
too noisy on 8 users to describe a data-layer change. These sub-metrics each
correspond to one thing the arm does differently, and every one is computable
from the two saved checkpoints with no model call.

Groups:
  A. memory layer   -- footprint, structure, polarity-tagged slots
  B. grounding      -- are writes made against ids that were actually observed?
  C. transaction    -- does a subtask end with a completed, paid order?
  D. proactiveness  -- does a committed question ever become a slot value?
  E. efficiency     -- turns, searches, thrash

Baseline is the cached stock+rewrite reference. Where trial count matters the
baseline is restricted to trial 0 (like-for-like with the 1-trial arm); rates
over all 4 baseline trials are shown separately and labelled.

Zero-model. Reads saved artifacts only.
"""

from __future__ import annotations

import json
import pathlib
import re
import statistics
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ARM = pathlib.Path("data/simulations/adapt8_1t.json")
BASE = pathlib.Path("data/simulations/stock_avg4_8u.json")

WRITE_PREFIXES = ("create_", "pay_", "modify_", "cancel_")
CREATE_PREFIXES = ("create_",)
BOOK_TOOLS = {"instore_book", "instore_reservation"}


def subtasks(path, trial=None):
    """(task_id, trial, idx) -> record.

    The key carries the trial: without it a 4-trial baseline collapses onto 100
    keys and the "all trials" column silently becomes the last trial only, which
    is exactly the artifact that made the arm look chattier than it is.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for sim in data.get("simulations") or []:
        if trial is not None and (sim.get("trial", 0) or 0) != trial:
            continue
        tid = str(sim["task_id"])
        sim_trial = sim.get("trial", 0) or 0
        states = sim.get("states") or {}
        mems = states.get("memory_snapshots") or {}
        rewards = ((sim.get("reward_info") or {}).get("info") or {}).get(
            "subtask_rewards"
        ) or {}
        for tr in states.get("integrity_subtask_trajectories") or []:
            idx = tr.get("subtask_idx")
            msgs = tr.get("messages") or []
            writes, creates = [], []
            for m in msgs:
                for c in m.get("tool_calls") or []:
                    name = c.get("name") or ""
                    args = c.get("arguments")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {}
                    if not isinstance(args, dict):
                        args = {}
                    if name.startswith(WRITE_PREFIXES) or name in BOOK_TOOLS:
                        writes.append((name, args))
                    if name.startswith(CREATE_PREFIXES) or name in BOOK_TOOLS:
                        creates.append((name, args))
            out[(tid, sim_trial, idx)] = {
                "memory": mems.get(f"subtask_{idx}_memory") or "",
                "messages": msgs,
                "reward": float(rewards.get(f"subtask_{idx}_reward", 0.0)),
                "writes": writes,
                "creates": creates,
                "new_states": (tr.get("states") or {}).get("new_states") or [],
                "idx": idx,
                "task_id": tid,
            }
    return out


def _slot_line(memory, tag):
    for line in memory.splitlines():
        if line.strip().startswith(tag):
            return line.strip()
    return ""


def slot_entries(memory, tag="PREFER"):
    line = _slot_line(memory, tag)
    if not line:
        return []
    body = line.split(":", 1)[1] if ":" in line else ""
    return [p.strip() for p in re.split(r"[|｜]", body) if p.strip()]


def ordered_ids(rec):
    ids = []
    for _name, args in rec["creates"]:
        for key in ("product_ids", "room_ids", "ticket_ids", "ids"):
            val = args.get(key)
            if isinstance(val, list):
                ids.extend(str(v) for v in val)
        for key in ("product_id", "room_id", "ticket_id", "id"):
            val = args.get(key)
            if isinstance(val, str):
                ids.append(val)
    return ids


def observed_ids(rec):
    """All ids printed by the environment anywhere in this subtask."""
    text = " ".join(
        str(m.get("content") or "") for m in rec["messages"] if m.get("role") == "tool"
    )
    return set(re.findall(r"S\d{10,}_[A-Z]\d{4,}", text))


def grounded(rec):
    ids = ordered_ids(rec)
    if not ids:
        return None
    seen = observed_ids(rec)
    return sum(1 for i in ids if i in seen) / len(ids)


def search_signatures(rec):
    sigs = []
    for m in rec["messages"]:
        for c in m.get("tool_calls") or []:
            name = c.get("name") or ""
            if "search" not in name and "recommand" not in name:
                continue
            args = c.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            sigs.append((name, json.dumps(args, sort_keys=True, ensure_ascii=False)))
    return sigs


def max_repeat(rec):
    counts = defaultdict(int)
    for sig in search_signatures(rec):
        counts[sig] += 1
    return max(counts.values()) if counts else 0


def paid(rec):
    """Did any created order reach a terminal paid status?"""
    blob = json.dumps(rec["new_states"], ensure_ascii=False)
    return "paid" in blob


def pct(num, den):
    return f"{100 * num / den:5.1f}%" if den else "  n/a"


def main() -> None:
    A3 = subtasks(ARM)
    B03 = subtasks(BASE, trial=0)
    B = subtasks(BASE)
    # Flat (task, idx) views for the like-for-like arm-vs-baseline rows.
    A = {(t, i): r for (t, _tr, i), r in A3.items()}
    B0 = {(t, i): r for (t, _tr, i), r in B03.items()}

    n = len(A)
    print("=" * 78)
    print(f"units: arm {n} (1 trial)   baseline {len(B0)} (trial 0)   "
          f"baseline all-trials {len(B)}")
    print("=" * 78)

    print()
    print("A. MEMORY LAYER")
    for label, data in (("arm ", A), ("base", B0)):
        mems = [len(r["memory"]) for r in data.values() if r["memory"]]
        pref = [r for r in data.values() if slot_entries(r["memory"])]
        avoid = [r for r in data.values() if _slot_line(r["memory"], "AVOID")]
        entries = [len(slot_entries(r["memory"])) for r in data.values()]
        print(f"  {label}: memory chars mean {statistics.mean(mems):6.0f}   "
              f"blocks with a PREFER slot {pct(len(pref), len(data))}   "
              f"mean entries {statistics.mean(entries):4.1f}   "
              f"blocks with an AVOID slot {pct(len(avoid), len(data))}")
    ma = statistics.mean([len(r["memory"]) for r in A.values() if r["memory"]])
    mb = statistics.mean([len(r["memory"]) for r in B0.values() if r["memory"]])
    print(f"  -> memory footprint change: {100 * (ma - mb) / mb:+.1f}% "
          f"({mb:.0f} -> {ma:.0f} chars per block)")
    all_pref = sum(len(slot_entries(r["memory"])) for r in A.values())
    print(f"  -> structured preference slots surfaced: {all_pref} entries over "
          f"{sum(1 for r in A.values() if slot_entries(r['memory']))} blocks "
          f"(baseline: 0, the rewrite memory has no such slot)")

    print()
    print("B. GROUNDING -- are writes made against ids the environment printed?")
    for label, data in (("arm     ", A), ("base t0 ", B0), ("base all", B)):
        g = [x for x in (grounded(r) for r in data.values()) if x is not None]
        writes = [r for r in data.values() if r["writes"]]
        ungrounded = sum(
            len(ordered_ids(r)) - sum(1 for i in ordered_ids(r) if i in observed_ids(r))
            for r in data.values()
        )
        print(f"  {label}: units with >=1 write {pct(len(writes), len(data))}   "
              f"ordered-id grounding {statistics.mean(g):.4f}   "
              f"ungrounded ids {ungrounded}   (n={len(g)} units that ordered)")

    print()
    print("C. TRANSACTION COMPLETION")
    for label, data in (("arm     ", A), ("base t0 ", B0), ("base all", B)):
        creates = [r for r in data.values() if r["creates"]]
        unpaid = [r for r in creates if not paid(r)]
        print(f"  {label}: units that created something {pct(len(creates), len(data))}   "
              f"of those, none reached paid {pct(len(unpaid), len(creates))}")

    print()
    print("D. PROACTIVENESS (arm only; the baseline has no question engine)")
    raw = json.loads(ARM.read_text(encoding="utf-8"))
    tot = defaultdict(int)
    users_live = 0
    for sim in raw.get("simulations") or []:
        ev = (sim.get("states") or {}).get("adapt_agent") or {}
        for k in ("questions_committed", "answers_linked",
                  "answers_resolved_to_a_value"):
            tot[k] += ev.get(k, 0)
        if ev.get("questions_committed"):
            users_live += 1
    q = tot["questions_committed"]
    print(f"  questions committed {q}   linked {tot['answers_linked']}   "
          f"resolved to a slot value {tot['answers_resolved_to_a_value']}   "
          f"-> conversion {pct(tot['answers_resolved_to_a_value'], q)}")
    print(f"  users where the loop fired at all: {users_live}/8")

    print()
    print("E. EFFICIENCY / THRASH")
    print("   NOTE: the baseline's four trials are four draws of one script, so the")
    print("   all-trials column (n=400) is the fair comparator. Reading only trial 0")
    print("   made the arm look 9% chattier and 47% searchier; both were artifacts.")
    for label, data in (("arm     ", A), ("base t0 ", B0), ("base all", B)):
        turns = [len(r["messages"]) for r in data.values()]
        thr = [r for r in data.values() if max_repeat(r) >= 3]
        sigs = [len(search_signatures(r)) for r in data.values()]
        print(f"  {label}: messages per subtask mean {statistics.mean(turns):5.1f}   "
              f"search calls mean {statistics.mean(sigs):4.2f}   "
              f"units with >=3 identical search signatures {pct(len(thr), len(data))}")

    print()
    print("F. THE ONE SLOT->CHOICE ASYMMETRY (arm only)")
    arm_raw = json.loads(ARM.read_text(encoding="utf-8"))
    B4 = defaultdict(list)
    for sim in json.loads(BASE.read_text(encoding="utf-8")).get("simulations") or []:
        for k, v in (((sim.get("reward_info") or {}).get("info") or {})
                     .get("subtask_rewards") or {}).items():
            B4[(str(sim["task_id"]), int(k.split("_")[1]))].append(float(v))
    hit_fix = hit_break = hit_same = 0
    for key, rec in A.items():
        prefs = slot_entries(rec["memory"])
        if not prefs or key not in B0:
            continue
        ids = ordered_ids(rec)
        if not ids:
            continue
        if not any(p and p in rec["memory"] for p in prefs):
            continue
        # did the arm order something named in its own PREFER slot?
        names = [
            p.get("name", "")
            for st in rec["new_states"]
            for p in st.get("products") or []
            if isinstance(p, dict)
        ]
        if not any(nm and any(nm in e or e in nm for e in prefs) for nm in names):
            continue
        base_r = statistics.mean(B4.get(key, [0.0]))
        if rec["reward"] > base_r:
            hit_fix += 1
        elif rec["reward"] < base_r:
            hit_break += 1
        else:
            hit_same += 1
    print(f"  units whose ordered product came from the arm's own PREFER slot: "
          f"{hit_fix + hit_break + hit_same}")
    print(f"    -> baseline 4-trial mean lower (fix) {hit_fix}   "
          f"higher (break) {hit_break}   equal {hit_same}")


if __name__ == "__main__":
    main()
