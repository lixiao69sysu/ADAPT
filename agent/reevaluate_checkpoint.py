"""Re-evaluate integrity-failed VitaBench checkpoints without agent replay."""

from __future__ import annotations

import argparse
from pathlib import Path

from agent.vitabench_runner import reevaluate_checkpoint


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--evaluator-llm", required=True)
    parser.add_argument("--language", default="chinese")
    parser.add_argument("--evaluator-retries", type=int, default=2)
    parser.add_argument("--evaluator-retry-backoff-seconds", type=float, default=1.0)
    args = parser.parse_args()
    reevaluate_checkpoint(
        args.checkpoint,
        llm_evaluator=args.evaluator_llm,
        language=args.language,
        evaluator_retries=args.evaluator_retries,
        evaluator_retry_backoff_seconds=args.evaluator_retry_backoff_seconds,
    )


if __name__ == "__main__":
    main()
