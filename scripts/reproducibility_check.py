"""Do two independent runs of the same (user, trial, seed) produce the same trajectory?

Every checkpoint records a `trajectory_hash` per simulation. When two checkpoints
were produced with the same configuration and cover the same `(task_id, seed)`,
comparing their hashes answers the reproducibility question directly and at zero
model cost.

This matters because the entire promotion method rests on comparing a new arm
against a *cached* control at a matched seed. If the same seed does not reproduce
the same trajectory, a cached control is not a control, and every comparison in
the repository has to be re-run as two contemporaneous arms instead.

Configurations are only compared when their `info` blocks agree on the fields
that can change behaviour (memory type, models, trial count, split seed), because
a hash difference caused by a config difference proves nothing.

Usage:
    python scripts/reproducibility_check.py data/simulations/*.json
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
from typing import Any

BEHAVIOUR_FIELDS = ("memory_type", "llm_agent", "llm_user", "llm_evaluator", "split_seed", "max_steps")

# Fields that differ between two runs by construction and therefore cannot carry
# reproducibility information: wall-clock stamps, per-call costs, and the raw API
# response (which embeds a fresh `chatcmpl-*` id on every request). The runner's
# own `trajectory_hash` covers `messages` verbatim, so it includes all of these
# and is guaranteed to differ between runs even when the run is perfectly
# reproducible. Comparing it is a control that can never be false.
VOLATILE_MESSAGE_FIELDS = {"timestamp", "cost", "raw_data", "id"}


def content_signature(messages: list[dict[str, Any]]) -> str:
    """Hash only what a reproduced trajectory must share."""
    stripped = []
    for message in messages:
        if not isinstance(message, dict):
            stripped.append(message)
            continue
        clean = {k: v for k, v in message.items() if k not in VOLATILE_MESSAGE_FIELDS}
        calls = clean.get("tool_calls")
        if isinstance(calls, list):
            clean["tool_calls"] = [
                {k: v for k, v in call.items() if k not in VOLATILE_MESSAGE_FIELDS}
                if isinstance(call, dict)
                else call
                for call in calls
            ]
        stripped.append(clean)
    blob = json.dumps(stripped, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load(path: pathlib.Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or "simulations" not in payload:
        return None
    return payload


def config_key(info: dict[str, Any]) -> tuple:
    return tuple(str(info.get(field)) for field in BEHAVIOUR_FIELDS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoints", nargs="+")
    args = parser.parse_args()

    seen: dict[tuple, list[dict[str, Any]]] = collections.defaultdict(list)
    for raw in args.checkpoints:
        path = pathlib.Path(raw)
        payload = load(path)
        if payload is None:
            continue
        key_cfg = config_key(payload.get("info") or {})
        for sim in payload.get("simulations") or []:
            seed = sim.get("seed")
            if seed is None:
                continue
            seen[(key_cfg, str(sim.get("task_id")), int(seed))].append(
                {
                    "file": path.name,
                    "trial": sim.get("trial"),
                    "hash": content_signature(sim.get("messages") or []),
                    "runner_hash": sim.get("trajectory_hash"),
                    "messages": len(sim.get("messages") or []),
                    "reward": (sim.get("reward_info") or {}).get("reward"),
                    "termination_reason": sim.get("termination_reason"),
                }
            )

    shared = {key: rows for key, rows in seen.items() if len(rows) > 1}
    if not shared:
        print("no (config, user, seed) key appears in more than one checkpoint")
        print(f"distinct keys: {len(seen)}")
        return 0

    same_hash = 0
    diff_hash = 0
    for key, rows in sorted(shared.items()):
        cfg, user, seed = key
        hashes = {row["hash"] for row in rows if row["hash"]}
        agree = len(hashes) == 1
        same_hash += agree
        diff_hash += not agree
        print(f"{user} seed={seed} config={cfg}")
        for row in rows:
            print(
                f"    {row['file']:<44} trial={row['trial']} "
                f"hash={str(row['hash'])[:12]} msgs={row['messages']:<4} "
                f"reward={row['reward']} term={row['termination_reason']}"
            )
        print(f"    -> {'IDENTICAL' if agree else 'DIVERGED'}")

    total = same_hash + diff_hash
    print()
    print(f"repeated keys: {total}  identical: {same_hash}  diverged: {diff_hash}")
    if diff_hash:
        print("VERDICT: the same (config, user, seed) does not reproduce its trajectory.")
        print("A cached control is therefore not a matched control.")
    else:
        print("VERDICT: repeated keys reproduce exactly; a cached control is usable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
