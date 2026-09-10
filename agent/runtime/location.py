"""Observable location prior for candidate ranking.

The runtime profile carries the user's registered home address, and every tool
result that matters for a consumer choice serializes the merchant's or venue's
own address. Ranking previously ignored both, so a candidate in another
district could win a tie purely because it was observed first (or later).

This module turns those two observable strings into a small ordinal prior:

* a candidate whose address or name names the same district/town as the user's
  home outranks a candidate in another district of the same city;
* a candidate in the same city (but another district) outranks one outside it;
* otherwise the prior is neutral, so tasks whose candidates are all far from
  home (a hotel in another province, a train to another city) are unaffected.

Nothing here resolves IDs, reads evaluator data or contains user-, task- or
candidate-specific values.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

_SUFFIX_CHARS = "省市区县镇乡旗"
_DISTRICT_SUFFIXES = ("区", "县", "镇", "乡", "街道", "旗")
_CITY_SUFFIXES = ("市",)

_ADDRESS_KEYS = ("常住住址", "住址", "家庭住址", "地址", "home", "address")

DISTRICT_MATCH = 2
CITY_MATCH = 1
LOCATION_NEUTRAL = 0


def address_text(user_profile: Any) -> str:
    """The user's own registered address, from an observable profile only."""
    if not isinstance(user_profile, dict):
        return ""
    for key in _ADDRESS_KEYS:
        value = user_profile.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def administrative_tokens(text: str) -> list[str]:
    """Ordered, deduplicated administrative units of a Chinese address.

    Each run of CJK characters is cut at every administrative suffix, so
    "四川省成都市温江区寿安镇" yields 成都市, 温江区 and 寿安镇 instead of one
    over-long province token.
    """
    tokens: list[str] = []
    for run in re.findall(r"[\u4e00-\u9fff]+", text or ""):
        start = 0
        index = 0
        while index < len(run):
            if run.startswith("街道", index):
                token = run[start : index + 2]
                index += 1
            elif run[index] in _SUFFIX_CHARS:
                token = run[start : index + 1]
            else:
                index += 1
                continue
            if len(token) >= 2 and not token.endswith("省") and token not in tokens:
                # The province cut only resets the scan window; a province name
                # is a prefix of every local candidate and cannot discriminate.
                tokens.append(token)
            start = index + 1
            index += 1
    return tokens


def home_tokens(user_profile: Any) -> list[str]:
    """Administrative units of the user's registered home address."""
    return administrative_tokens(address_text(user_profile))


def _district_stem(token: str) -> str:
    """The abbreviated form of a district token, when it is safe to match.

    Only 区/县 count: town and street names repeat across a city (a 寿安镇 in
    one county and a 寿安街道 in another), so their stems must not be treated
    as the same place. District stems are stable enough that shop names
    abbreviate them ("温江万春店" for 温江区).
    """
    for suffix in ("区", "县"):
        if token.endswith(suffix):
            return token[: -len(suffix)]
    return ""


def location_rank(candidate: Any, tokens: Iterable[str]) -> int:
    """How close a candidate is to the home units, on an ordinal scale."""
    home = [token for token in tokens if token]
    if not home:
        return LOCATION_NEUTRAL
    name = str(getattr(candidate, "name", "") or "")
    raw = str(getattr(candidate, "raw", "") or "")
    text = f"{name} {raw}"
    for token in home:
        if not token.endswith(_DISTRICT_SUFFIXES):
            continue
        stem = _district_stem(token)
        # A shop name often abbreviates its branch district ("温江万春店"), so
        # the bare district stem counts as a match as well.
        if token in text or (len(stem) >= 2 and stem in text):
            return DISTRICT_MATCH
    for token in home:
        if token.endswith(_CITY_SUFFIXES) and token in text:
            return CITY_MATCH
    return LOCATION_NEUTRAL


def candidate_rating(candidate: Any) -> float:
    """The numeric rating a tool result published for this candidate."""
    attributes = getattr(candidate, "attributes", None) or {}
    for key in ("score", "rating", "star_rating"):
        value = attributes.get(key)
        if value is None:
            continue
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            continue
    return 0.0
