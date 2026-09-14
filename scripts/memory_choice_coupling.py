"""Memory -> choice coupling: does the ordered product come from the memory?

The mechanism hypothesis under test: what separates the arm is not more
information (its memory block is 2-4x SHORTER than the baseline's) but a
structured, attribute-bearing slot list whose entries are directly passable --
so the product the agent orders should be readable in its own memory.

The test is deliberately blunt and falsifiable:
  for every subtask, collect the product names in the agent's own order
  (trajectory states.new_states[*].products[*].name), and check whether the same
  name appears verbatim in the memory block the agent read for that subtask
  (states.memory_snapshots["subtask_<idx>_memory"]).

Reported per arm, and split by whether the unit flipped relative to the
baseline's trial-0 slice, so "the memory explained the win" can be told apart
from "the memory explained nothing".

Zero-model; reads saved artifacts only.
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


def per_subtask(path, trial_filter=None):
    """(task_id, idx) -> {memory, ordered names, reward}."""
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for sim in data.get("simulations") or []:
        if trial_filter is not None and (sim.get("trial", 0) or 0) != trial_filter:
            continue
        tid = str(sim["task_id"])
        states = sim.get("states") or {}
        mems = states.get("memory_snapshots") or {}
        rewards = ((sim.get("reward_info") or {}).get("info") or {}).get(
            "subtask_rewards"
        ) or {}
        for tr in states.get("integrity_subtask_trajectories") or []:
            idx = tr.get("subtask_idx")
            names = []
            for st in (tr.get("states") or {}).get("new_states") or []:
                for p in st.get("products") or []:
                    if isinstance(p, dict) and p.get("name"):
                        names.append(str(p["name"]))
            out[(tid, idx)] = {
                "memory": mems.get(f"subtask_{idx}_memory") or "",
                "ordered": names,
                "reward": float(rewards.get(f"subtask_{idx}_reward", 0.0)),
            }
    return out


def norm(text):
    return re.sub(r"[\s（）()【】·、,，。]", "", text)


def pref_line(memory):
    for line in memory.splitlines():
        if line.strip().startswith("PREFER"):
            return line
    return ""


def coupling(rec):
    """Is any ordered product name present in the memory block?"""
    if not rec["ordered"]:
        return None
    mem = norm(rec["memory"])
    hits = [n for n in rec["ordered"] if norm(n) and norm(n) in mem]
    return len(hits) / len(rec["ordered"])


def pref_coupling(rec):
    if not rec["ordered"]:
        return None
    line = norm(pref_line(rec["memory"]))
    if not line:
        return None
    hits = [n for n in rec["ordered"] if norm(n) and norm(n) in line]
    return len(hits) / len(rec["ordered"])


def _ngrams(text, n=3):
    clean = norm(text)
    return {clean[i : i + n] for i in range(len(clean) - n + 1)}


def coupling_loose(rec, n=3):
    """Memory-backed if any n-gram of the ordered name occurs in the memory.

    The exact-substring test is a lower bound: the store's canonical name and the
    memory's shorthand differ ('郁金香鲜花花束（10枝）' vs '粉色郁金香10枝',
    '瑞士莲经典牛奶巧克力排块100g' vs '瑞士莲巧克力'), so a real memory-backed
    choice can fail it. This metric is deliberately generous and is reported
    alongside, never instead of, the strict one.
    """
    if not rec["ordered"]:
        return None
    mem = norm(rec["memory"])
    hits = 0
    for name in rec["ordered"]:
        if any(g in mem for g in _ngrams(name, n)):
            hits += 1
    return hits / len(rec["ordered"])


def _mean_or_nan(values):
    return statistics.mean(values) if values else float("nan")


def main() -> None:
    A = per_subtask(ARM)
    B_all = per_subtask(BASE)
    B = per_subtask(BASE, trial_filter=0)

    print("=" * 78)
    print("MEMORY -> CHOICE COUPLING  (ordered product name found in the memory read)")
    for label, data in (("ARM  (adapt+summary)", A), ("BASE (rewrite,trial0)", B)):
        vals = [c for c in (coupling(r) for r in data.values()) if c is not None]
        pc = [c for c in (pref_coupling(r) for r in data.values()) if c is not None]
        loose = [c for c in (coupling_loose(r) for r in data.values()) if c is not None]
        lens = [len(r["memory"]) for r in data.values() if r["memory"]]
        print(f"  {label:22} units with an order {len(vals):3}   "
              f"exact {_mean_or_nan(vals):.3f}   3-gram {_mean_or_nan(loose):.3f}   "
              f"in-PREFER {_mean_or_nan(pc):.3f}   "
              f"memory chars mean {_mean_or_nan(lens):.0f}")

    # split by whether the unit flipped
    B4 = defaultdict(list)
    for (tid, idx), rec in B_all.items():
        B4[(tid, idx)].append(rec["reward"])
    fixes, breaks, same = [], [], []
    for key, rec in A.items():
        if key not in B:
            continue
        base_t0 = B[key]["reward"]
        row = (key, rec, base_t0, coupling(rec), pref_coupling(rec), coupling_loose(rec))
        if rec["reward"] > base_t0:
            fixes.append(row)
        elif rec["reward"] < base_t0:
            breaks.append(row)
        else:
            same.append(row)

    print()
    print("  flip split (arm vs baseline trial-0):")
    for label, rows in (("FIXES ", fixes), ("BREAKS", breaks), ("SAME  ", same)):
        vals = [r[3] for r in rows if r[3] is not None]
        loose = [r[5] for r in rows if r[5] is not None]
        pc = [r[4] for r in rows if r[4] is not None]
        if not vals:
            continue
        print(f"    {label} n={len(rows):3}  exact {_mean_or_nan(vals):.3f}   "
              f"3-gram {_mean_or_nan(loose):.3f}   "
              f"in-PREFER {_mean_or_nan(pc):.3f}")

    print()
    print("  the fixes that landed on never-passed subtasks -- what was ordered,")
    print("  and was it already in the arm's own memory?")
    never_passed = {
        key for key, recs in B4.items() if all(r == 0.0 for r in recs)
    }
    for key, rec, base_t0, c, pc, loose in fixes:
        name = rec["ordered"][0] if rec["ordered"] else "(no order)"
        mark = "YES" if c else ("no-order" if c is None else "NO ")
        tag = " [0/4]" if key in never_passed else ""
        print(f"    {key[0]} idx{key[1]:<3} in-memory={mark:8} "
              f"in-PREFER={'Y' if pc else 'n'}{tag}  ordered: {name[:38]}")

    print()
    print("  counter-check: the BREAKS -- was the arm's order also memory-backed?")
    for key, rec, base_t0, c, pc, loose in breaks[:12]:
        name = rec["ordered"][0] if rec["ordered"] else "(no order)"
        print(f"    {key[0]} idx{key[1]:<3} in-memory="
              f"{'YES' if c else ('no-order' if c is None else 'NO '):8} "
              f"in-PREFER={'Y' if pc else 'n'}  ordered: {name[:38]}")


if __name__ == "__main__":
    main()
