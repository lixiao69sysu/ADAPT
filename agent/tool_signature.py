"""Canonical identity of a tool call and of a tool result.

Kept free of any vendored VitaBench import on purpose: the offline measurement
tooling (`scripts/runaway_autopsy.py`) uses these names to count repeated calls
and repeated results without pulling the benchmark into the analysis. The
runtime guard that once shared this vocabulary was removed (E-086); the
definitions stay because the autopsy still needs them, and because a
disagreement between two namings is exactly how E-053's guard ended up firing
zero times while its unit tests stayed green.

Two outputs:

- `signature_of(name, arguments)` identifies a *call*. Two calls share a
  signature when the tool name and the full argument mapping are equal. Argument
  order never matters.
- `digest_of(content)` identifies a *result*. Two results share a digest when
  their text is byte-identical.

Nothing here decides anything; it only names things.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# Arguments are truncated before hashing so that a very large search argument
# cannot dominate the key. Two calls agreeing on the first 240 canonical
# characters are treated as the same call; that is deliberately conservative,
# because treating two *different* calls as identical would let the guard
# discard a call that could have carried new information.
SIGNATURE_WIDTH = 240

# The human-readable form is bounded for the same reason.
LABEL_WIDTH = 80


def canonical_arguments(arguments: Any) -> str:
    """Stable textual form of tool arguments, independent of key order."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (TypeError, ValueError):
            pass
    try:
        blob = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        blob = str(arguments)
    return blob[:SIGNATURE_WIDTH]


def signature_of(name: Any, arguments: Any) -> str:
    """Short stable identity of a tool call."""
    canonical = canonical_arguments(arguments)
    digest = hashlib.sha1(canonical.encode("utf-8", "replace")).hexdigest()[:8]
    return f"{name or '?'}:{digest}"


def label_of(name: Any, arguments: Any) -> str:
    """Readable form of a call, for notices and event records."""
    text = canonical_arguments(arguments)
    if len(text) > LABEL_WIDTH:
        text = text[:LABEL_WIDTH] + "..."
    return f"{name or '?'}({text})"


def digest_of(content: Any) -> str:
    """Short stable identity of a tool result."""
    if content is None:
        blob = ""
    elif isinstance(content, str):
        blob = content
    else:
        blob = repr(content)
    return hashlib.sha1(blob.encode("utf-8", "replace")).hexdigest()[:8]


def content_length(content: Any) -> int:
    """Character count of a tool result, reported alongside events."""
    if content is None:
        return 0
    if isinstance(content, str):
        return len(content)
    return len(repr(content))
