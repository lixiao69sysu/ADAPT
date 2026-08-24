"""Runtime-only compatibility helpers for pristine VitaBench on Windows."""

from __future__ import annotations

import builtins
import os
import sys
from pathlib import Path
from typing import Any


_ORIGINAL_OPEN = builtins.open
_ENABLED = False


def enable_vitabench_utf8() -> None:
    """Default text config reads under VitaBench to UTF-8.

    Upstream opens several YAML prompt/config files without an encoding. On a
    GBK Windows locale that fails before the external agent can start. This
    process-local adapter is scoped to text YAML/JSON files below the vendored
    VitaBench directory; it does not edit or monkeypatch VitaBench modules.
    """
    global _ENABLED
    if _ENABLED:
        return

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")

    def _open(file: Any, mode: str = "r", *args, **kwargs):
        if kwargs.get("encoding") is None and "b" not in mode:
            try:
                path = Path(os.fspath(file)).resolve()
                normalized = str(path).replace("\\", "/").lower()
                if "/evaluation/vitabench/" in normalized and path.suffix.lower() in {".yaml", ".yml", ".json"}:
                    kwargs["encoding"] = "utf-8"
            except (TypeError, ValueError, OSError):
                pass
        return _ORIGINAL_OPEN(file, mode, *args, **kwargs)

    builtins.open = _open
    _ENABLED = True
