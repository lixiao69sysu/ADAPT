"""Run ADAPT through an unchanged official VitaBench checkout."""

from __future__ import annotations

import argparse
import importlib
import subprocess
import sys
from pathlib import Path


DEFAULT_OFFICIAL_COMMIT = "f60169e89f30499cb7883f3dad76bd03facc908d"


def _git(vita_root: Path, *args: str):
    return subprocess.run(
        ["git", "-C", str(vita_root), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def verify_frozen_vita(vita_root: Path, expected_commit: str) -> None:
    """Refuse evaluation unless tracked VitaBench source is the official tree."""
    head = _git(vita_root, "rev-parse", "HEAD")
    if head.returncode != 0 or head.stdout.strip() != expected_commit:
        actual = head.stdout.strip() or "unavailable"
        raise RuntimeError(
            f"expected VitaBench commit {expected_commit}, got {actual}"
        )

    changed = _git(
        vita_root,
        "diff",
        "--name-only",
        "HEAD",
        "--",
        "src/vita",
    )
    if changed.returncode != 0:
        raise RuntimeError("could not verify frozen VitaBench source")
    if changed.stdout.strip():
        raise RuntimeError(
            "tracked VitaBench source changes detected:\n"
            f"{changed.stdout.strip()}"
        )


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--vita-root", type=Path, required=True)
    parser.add_argument(
        "--expected-vita-commit",
        default=DEFAULT_OFFICIAL_COMMIT,
    )
    known, vita_args = parser.parse_known_args(argv)

    sys.path.insert(0, str(known.vita_root / "src"))
    vita_run = importlib.import_module("vita.run")
    vita_registry = importlib.import_module("vita.registry").registry

    from agent.adapt_agent import ADAPTAgent

    if "adapt_agent" not in vita_registry.get_agents():
        vita_registry.register_agent(ADAPTAgent, "adapt_agent")
    vita_run.PersonalizationAgent = ADAPTAgent

    vita_cli = importlib.import_module("vita.cli")
    original_argv = sys.argv
    try:
        sys.argv = ["vita", *vita_args]
        return vita_cli.main()
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    main()
