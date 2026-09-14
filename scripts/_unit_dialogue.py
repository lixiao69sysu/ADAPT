"""Read a runner log and show, per unit, what the agent said before the reward.

Usage: python scripts/_unit_dialogue.py data/simulations/iso_R8.log [P1]

The log is Tee-Object output: UTF-16, and hard-wrapped at the console width, so
identifiers and prose can be split mid-token. Patterns below tolerate that.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REWARD_RE = re.compile(r"\w*_([A-Z]\d{5,})_(\d+) evaluation: reward=([\d.]+)")
MESSAGE_RE = re.compile(
    r"From role: (\w+)\r\nTo role: (\w+)\r\nMessage: (\w+)\r\n"
    r"timestamp: [^\r\n]*\r\ncontent: (.*?)\r\ncost:",
    re.S,
)


def _read_log(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-16", "utf-8", "gbk"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "evaluation: reward" in text:
            return text
    return raw.decode("utf-8", errors="replace")


def _flatten(text: str) -> str:
    return " ".join((text or "").replace("\r", " ").split())


def main() -> None:
    path = Path(sys.argv[1])
    if not path.is_absolute():
        path = ROOT / path
    task_filter = sys.argv[2] if len(sys.argv) > 2 else ""
    text = _read_log(path)

    marks = list(REWARD_RE.finditer(text))
    if not marks:
        print("no reward lines found")
        return
    boundaries = [0] + [mark.end() for mark in marks]
    for index, mark in enumerate(marks):
        task, unit, reward = mark.group(1), mark.group(2), float(mark.group(3))
        if task_filter and task != task_filter:
            continue
        chunk = text[boundaries[index] : mark.start()]
        print(f"=== {task} unit {unit}: reward={reward}")
        for match in MESSAGE_RE.finditer(chunk):
            if match.group(3) != "AssistantMessage":
                continue
            content = _flatten(match.group(4))
            if not content:
                continue
            asks = "?" in content or "？" in content
            print(f"  {'ASK ' if asks else '    '}{content[:160]}")


if __name__ == "__main__":
    main()
