import json, collections
from pathlib import Path

rows = [json.loads(l) for l in Path("data/simulations/ab_guard_E057330.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
reasons = collections.Counter()
for r in rows:
    if r.get("event") == "preflight_rejected":
        for p in r.get("problems") or []:
            reasons[p.split(":")[0][:70]] += 1
print("=== preflight rejection reasons (fixed version) ===")
for k, v in reasons.most_common(12):
    print(f"  {v:3d}  {k}")


def outcomes(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for sim in d["simulations"]:
        rewards = ((sim.get("reward_info") or {}).get("info") or {}).get("subtask_rewards") or {}
        for t in ((sim.get("states") or {}).get("integrity_subtask_trajectories") or []):
            idx = t.get("subtask_idx")
            names = [c.get("name", "") for m in (t.get("messages") or []) if m.get("role") == "assistant" for c in (m.get("tool_calls") or [])]
            writes = [n for n in names if n.startswith("create_") or n in ("instore_book", "instore_reservation")]
            out.setdefault(idx, []).append((float(rewards.get(f"subtask_{idx}_reward") or 0), len(writes), len(t["states"]["new_states"])))
    return out


a = outcomes("data/simulations/ab_guard_E057330.json")
s = outcomes("data/simulations/stock_avg4_8u.json")
print("\n=== per-unit: ADAPT(fixed, 1 trial) vs stock(4 trials) ===")
print(" idx  A_rw A_wr A_ord | S_rw S_wr")
for idx in sorted(a):
    av = a[idx][0]
    sv = s.get(idx) or []
    s_rw = sum(x[0] for x in sv) / len(sv) if sv else 0.0
    s_wr = sum(x[1] for x in sv) / len(sv) if sv else 0.0
    mark = "  <-- stock solves, we do not" if (s_rw >= 0.5 and av[0] < 0.5) else ""
    print(f"{idx:>4}  {av[0]:>4.1f} {av[1]:>4} {av[2]:>5} | {s_rw:>4.2f} {s_wr:>4.1f}{mark}")
