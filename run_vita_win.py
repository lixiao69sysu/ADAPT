#!/usr/bin/env python
"""
run_vita_win.py — Windows shim for running vita on Chinese (GBK) systems.

VitaBench's orchestrator prints emoji (e.g. 🚀) that crash on Windows console
with GBK code page.  This shim applies two fixes before vita loads anything:

  1. SetConsoleOutputCP(65001) — tells the Win32 console to accept UTF-8 bytes.
  2. Replace sys.stdout/stderr with UTF-8 TextIOWrapper — so Python encodes
     emoji as UTF-8 bytes instead of trying (and failing) to encode as GBK.

Unlike PYTHONUTF8=1, this does NOT change the locale/filesystem codec, so
Anaconda .pth files with GBK-encoded paths continue to work.

Usage (from evaluation/vitabench/):
    python ../../run_vita_win.py run --domain personalization ...

All arguments are forwarded verbatim to vita's CLI entry point.
"""
import sys

if sys.platform == "win32":
    import io
    import ctypes

    # 1. Tell Win32 console to interpret bytes as UTF-8.
    try:
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass

    # 2. Replace sys.stdout / sys.stderr with UTF-8 TextIOWrapper.
    #    errors='replace' ensures a stray unencodable char prints '?' not crash.
    try:
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )
    except AttributeError:
        pass  # already a wrapper without .buffer (e.g. piped in some IDEs)

    try:
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )
    except AttributeError:
        pass

# Forward to vita's own CLI entry point — identical to `vita run ...`
from vita.cli import main  # noqa: E402  (must come after sys.stdout patch)
sys.exit(main())
