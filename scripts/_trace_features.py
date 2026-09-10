"""Trace features per subtask, ADAPT (1 trial) vs stock (4 trials), finished users only."""
import json
from pathlib import Path

USERS = {"E057330", "E941775"}


def features(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = {}
    for sim in d["simulations"]:
        user = sim["task_id"]
        if user not in USERS:
            continue
        trajs = ((sim.get("states") or {}).get("integrity_subtask_trajectories") or [])
        for t in trajs:
            unit = (user, t.get("subtask_idx"))
            msgs = t.get("messages") or []
            assistants = [m for m in msgs if m.get("role") == "assistant"]
            tools = [c.get("name", "") for m in assistants for c in (m.get("tool_calls") or [])]
            questions = [
                m for m in assistants
                if not (m.get("tool_calls") or []) and ("？" in (m.get("content") or "") or "?" in (m.get("content") or ""))
            ]
            def count(prefixes):
                return sum(1 for name in tools if any(name.startswith(p) for p in prefixes))
            rows.setdefault(unit, []).append({
                "reward": float(((sim.get("reward_info") or {}).get("info") or {}).get("subtask_rewards", {}).get(f"subtask_{t.get('subtask_idx')}_reward", 0) or 0),
                "term": t.get("termination_reason"),
                "msgs": len(msgs),
                "assistant": len(assistants),
                "questions": len(questions),
                "search": count(["instore_shop_search", "instore_product_search", "hotel_search", "train_ticket_search", "attraction", "flight_search", "delivery_", "shop_search"]),
                "read": count(["get_", "query_"]),
                "write": count(["create_", "instore_book", "instore_reservation"]),
                "pay": count(["pay_"]),
            })
    return rows


def mean(values):
    return sum(values) / len(values) if values else 0.0


adapt = features("data/simulations/adapt_avg1_8u.json")
stock = features("data/simulations/stock_avg4_8u.json")
units = sorted(set(adapt) | set(stock))

print(f"{'unit':16}{'A_rw':>6}{'S_rw':>6}{'A_msg':>6}{'S_msg':>6}{'A_q':>5}{'S_q':>5}{'A_wr':>6}{'S_wr':>6}  A_term")
for unit in units:
    a = (adapt.get(unit) or [{}])[0]
    s_rows = stock.get(unit) or [{}]
    print(
        f"{unit[0][:5] + '_' + str(unit[1]):16}"
        f"{a.get('reward', 0):>6.2f}{mean([r.get('reward', 0) for r in s_rows]):>6.2f}"
        f"{a.get('msgs', 0):>6}{mean([r.get('msgs', 0) for r in s_rows]):>6.0f}"
        f"{a.get('questions', 0):>5}{mean([r.get('questions', 0) for r in s_rows]):>5.0f}"
        f"{a.get('write', 0):>6}{mean([r.get('write', 0) for r in s_rows]):>6.1f}"
        f"  {a.get('term', '-')}"
    )

a_rows = [r for rows in adapt.values() for r in rows]
s_rows = [r for rows in stock.values() for r in rows]
print("\n--- aggregates ---")
for label, rows in (("ADAPT", a_rows), ("stock", s_rows)):
    print(
        f"{label:6} units={len(rows):3d} reward={mean([r['reward'] for r in rows]):.4f} "
        f"msgs={mean([r['msgs'] for r in rows]):.1f} questions={mean([r['questions'] for r in rows]):.2f} "
        f"search={mean([r['search'] for r in rows]):.2f} read={mean([r['read'] for r in rows]):.2f} "
        f"write={mean([r['write'] for r in rows]):.2f} pay={mean([r['pay'] for r in rows]):.2f} "
        f"zero_write_units={sum(1 for r in rows if r['write'] == 0)}/{len(rows)}"
    )
from collections import Counter
print("ADAPT terminations:", dict(Counter(r['term'] for r in a_rows)))
print("stock terminations:", dict(Counter(r['term'] for r in s_rows)))
