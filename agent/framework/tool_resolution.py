"""Legacy exact-ID compatibility helper.

Global fuzzy name resolution is disabled. Runtime writes are validated against
the current subtask's CandidateLedger instead.
"""


def resolve_id_or_name(id_to_name, key, kind="id", score_cutoff=55):
    """Return an exact canonical ID or fail with observed alternatives.

    Args:
        id_to_name: mapping ``{canonical_id: display_name}``.
        key: the value the agent actually passed.
        kind: human label used in error messages ("store", "product", "flight"...).
        score_cutoff: ignored; retained only for source compatibility.

    Returns:
        ``(canonical_id, note)`` with an empty note.

    Raises:
        ValueError: when ``key`` is not an exact observed ID.
    """
    if key in id_to_name:
        return key, ""
    nearest = ", ".join(list(id_to_name.keys())[:3])
    raise ValueError(
        f"{kind} '{key}' not found. Search results carry a long ID (e.g. {nearest}); "
        "always pass that exact ID, never a name or an invented number."
    )
