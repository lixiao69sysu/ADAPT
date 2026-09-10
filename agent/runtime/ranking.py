"""Deterministic ranking for the bounded candidate shortlist."""

from __future__ import annotations

import re
from typing import Any

from agent.runtime.alignment import EvidenceAlignment


class CandidateRanker:
    """Hard-filter exclusions, then score evidence rather than model prose."""

    @staticmethod
    def preference_alignment(candidates: list[Any], card: Any) -> EvidenceAlignment:
        sources = (
            card.alignment_source_records()
            if hasattr(card, "alignment_source_records")
            else getattr(card, "prefer", [])
        )
        return EvidenceAlignment(
            candidates, sources, _preference_matches
        )

    @classmethod
    def preference_match_count(
        cls, candidate: Any, card: Any, candidates: list[Any] | None = None
    ) -> int:
        alignment = cls.preference_alignment(candidates or [candidate], card)
        return alignment.coverage(candidate)

    @classmethod
    def preference_match_counts(
        cls, candidates: list[Any], card: Any
    ) -> dict[str, int]:
        alignment = cls.preference_alignment(candidates, card)
        return {
            str(candidate.candidate_id): alignment.coverage(candidate)
            for candidate in candidates
        }

    @classmethod
    def preference_scores(
        cls, candidates: list[Any], card: Any
    ) -> dict[str, float]:
        alignment = cls.preference_alignment(candidates, card)
        return {
            str(candidate.candidate_id): alignment.score(candidate)
            for candidate in candidates
        }

    @classmethod
    def decisive_preference_scores(
        cls, candidates: list[Any], card: Any
    ) -> dict[str, float]:
        alignment = cls.preference_alignment(candidates, card)
        return {
            str(candidate.candidate_id): alignment.decisive_score(candidate)
            for candidate in candidates
        }

    def rank(
        self,
        candidates: list[Any],
        card: Any,
        limit: int = 5,
        location_tokens: list[str] | None = None,
        parent_ranks: dict[str, int] | None = None,
    ) -> list[Any]:
        """Order compliant candidates, then break ties observably.

        Evidence score stays primary. Equal scores are broken by proximity to
        the user's registered home (district, then city), then by the rating the
        tool result published, then by recency and price. Without this, a tie
        was decided by observation order alone, which let a candidate in
        another district be presented as the top recommendation.

        A product row carries no address of its own, so it inherits the best
        proximity rank of its observed parent (the shop that sells it).
        """
        from agent.runtime.location import candidate_rating, location_rank

        parent_ranks = parent_ranks or {}

        def proximity(candidate: Any) -> int:
            rank = location_rank(candidate, location_tokens or [])
            if rank:
                return rank
            parents = getattr(candidate, "parent_ids", None) or []
            return max(
                (parent_ranks.get(str(parent), 0) for parent in parents),
                default=0,
            )

        alignment = self.preference_alignment(candidates, card)
        constraints = [
            constraint
            for constraint in getattr(card, "constraints", [])
            if getattr(getattr(constraint, "target", None), "value", "candidate")
            == "candidate"
        ]
        scored: list[tuple[float, int, float, Any]] = []
        groundable_categories = {
            constraint.value
            for constraint in constraints
            if getattr(constraint, "kind", "") == "category"
            and any(
                _hard_constraint_matches(constraint.value, candidate.raw, candidate)
                for candidate in candidates
            )
        }
        for candidate in candidates:
            raw = candidate.raw or ""
            if _strong_preference_conflict(raw, getattr(card, "prefer", [])):
                continue
            required = [
                constraint
                for constraint in constraints
                if getattr(getattr(constraint, "operator", None), "value", "")
                != "excludes"
                and getattr(constraint, "hard", True)
                and constraint.value
                # A user may name an open-world hypernym (for example a broad
                # goods class) that no returned item serializes literally. It
                # is unsafe to let that lexical mismatch erase the complete
                # candidate set. If at least one item grounds the category,
                # normal strict filtering remains in force.
                and (
                    getattr(constraint, "kind", "") != "category"
                    or constraint.value in groundable_categories
                )
            ]
            if any(
                not _hard_constraint_matches(constraint.value, raw, candidate)
                for constraint in required
            ):
                continue
            excluded = any(
                getattr(getattr(constraint, "operator", None), "value", "")
                == "excludes"
                and constraint.value
                and violates_exclusion(
                    raw, constraint.value, getattr(card, "prefer", [])
                )
                for constraint in constraints
            )
            if excluded or candidate.inventory == 0:
                continue
            hard_matches = sum(
                1
                for constraint in constraints
                if getattr(getattr(constraint, "operator", None), "value", "")
                != "excludes"
                and constraint.value
                and _hard_constraint_matches(constraint.value, raw, candidate)
            )
            preference_matches = alignment.score(candidate)
            exact_preference_matches = sum(
                1
                for value in getattr(card, "prefer", [])
                if value and value in raw
            )
            category_matches = sum(
                1
                for feature in _CATEGORY_FEATURES
                if feature in raw
                and any(
                    feature in value
                    and not (feature == "汤锅" and value.endswith("锅底"))
                    for value in getattr(card, "prefer", [])
                )
            )
            # Exact evidence should break a tie between a literal preference
            # and a semantic alias, while semantic matches still dominate
            # price.  Do not award isolated category substrings here: e.g.
            # ``汤锅`` inside ``菌汤锅底`` previously boosted every
            # unrelated soup-pot candidate.
            score = (
                hard_matches * 5.0
                + preference_matches
                + exact_preference_matches * 0.25
                + category_matches
            )
            if candidate.inventory is not None and candidate.inventory > 0:
                score += 0.5
            scored.append(
                (
                    score,
                    proximity(candidate),
                    candidate_rating(candidate),
                    candidate.observed_turn,
                    -(candidate.price or 0),
                    candidate,
                )
            )
        scored.sort(key=lambda item: item[:5], reverse=True)
        return [item[-1] for item in scored[:limit]]


def _preference_matches(value: str, raw: str) -> bool:
    """Normalize preference phrases for ranking without resolving any ID."""
    preference = (value or "").strip()
    if not preference:
        return False
    if preference == "低饱和色系":
        color = _candidate_color(raw)
        return bool(color) and _is_muted_color(color)
    if preference == "配送30分钟内":
        durations = [
            int(duration)
            for duration in re.findall(
                r"配送(?:时长|时间)\s*[:：]?\s*(\d+)\s*分钟", raw
            )
        ]
        return bool(durations) and min(durations) <= 30
    # Normalize a common food-service dimension rather than letting price
    # break a semantic tie. ``菌汤锅底`` is the preference dimension;
    # candidates usually serialize it as ``菌汤火锅`` or ``菌汤汤锅``.
    if preference.endswith("锅底"):
        broth = preference.removesuffix("锅底").strip()
        if len(broth) >= 2 and broth in raw and any(
            marker in raw for marker in ("火锅", "汤锅", "锅底")
        ):
            return True
    if any(topping in preference and topping in raw for topping in _TOPPING_MARKERS):
        if any(
            negation in preference
            for negation in ("不加小料", "无小料", "不要小料", "不放小料")
        ):
            return not _contains_forbidden(raw, "小料")
        preferred_temperatures = _temperature_values(preference)
        candidate_temperatures = _temperature_values(raw)
        return not (
            preferred_temperatures
            and candidate_temperatures
            and preferred_temperatures.isdisjoint(candidate_temperatures)
        )
    if preference in raw:
        return True
    normalized = preference
    for prefix in ("喜欢", "偏好", "常选", "常点", "经常选择"):
        normalized = normalized.removeprefix(prefix)
    roots = {normalized}
    for separator in ("（", "(", "酒店", "宾馆", "店"):
        if separator in normalized:
            roots.add(normalized.split(separator, 1)[0])
    if any(len(root.strip()) >= 2 and root.strip() in raw for root in roots):
        return True
    brand = re.match(r"([A-Za-z][A-Za-z0-9&.'-]{1,20})", normalized)
    if brand and brand.group(1).lower() in raw.lower():
        return True
    return False


_CATEGORY_FEATURES = (
    "奶茶",
    "咖啡",
    "果茶",
    "火锅",
    "汤锅",
    "烧烤",
    "大床房",
    "双床房",
)


# Product categories must be proved by the product itself.  Searching the
# entire serialized row lets unrelated attributes (for example
# ``低咖啡因``) or merchant metadata satisfy the category ``咖啡``.
_PRODUCT_CATEGORY_VALUES = {
    "鼠标",
    "拖鞋",
    "运动鞋",
    "休闲鞋",
    "板鞋",
    "跑鞋",
    "鞋",
    "衣服",
    "充电宝",
    "手办",
    "奶茶",
    "咖啡",
    "饮品",
    "汤锅",
    "火锅",
    "汤",
    "饭",
    "健身",
    "撸铁",
    "真人CS",
    "密室逃脱",
    "猫咖",
    "团购券",
    "套餐",
}

_SHOE_ACCESSORY_MARKERS = {
    "鞋垫",
    "鞋带",
    "鞋刷",
    "鞋油",
    "鞋盒",
    "鞋套",
    "鞋撑",
    "鞋拔",
}


def _product_category_evidence(candidate: Any) -> tuple[str, set[str]]:
    """Return category-bearing product evidence, excluding specifications.

    Product names and explicit tags describe what an entity is.  Attributes
    such as caffeine level, temperature and size describe a specification and
    must not be allowed to impersonate the product category.
    """
    name = getattr(candidate, "name", "") or ""
    raw = getattr(candidate, "raw", "") or ""
    # Parenthesized product-name suffixes are specifications in VitaBench
    # (e.g. 热可可（低咖啡因）), not category evidence.
    base_name = re.split(r"[（(]", name, maxsplit=1)[0]
    tag_blobs = re.findall(r"tags\s*[=:]\s*(\[[^\]]*\])", raw)
    tags = {
        tag.strip()
        for blob in tag_blobs
        for tag in re.findall(r"['\"]([^'\"]+)['\"]", blob)
        if tag.strip()
    }
    return base_name, tags


def _hard_constraint_matches(value: str, raw: str, candidate: Any = None) -> bool:
    if (
        candidate is not None
        and getattr(candidate, "entity_type", "") == "product"
        and value in _PRODUCT_CATEGORY_VALUES
    ):
        name, tags = _product_category_evidence(candidate)
        if value == "鞋":
            if "配件" in tags or any(marker in name for marker in _SHOE_ACCESSORY_MARKERS):
                return False
            return "鞋" in name or any(tag.endswith("鞋") for tag in tags)
        if value in {"汤锅", "火锅"}:
            return (
                "汤锅" in name
                or "火锅" in name
                or bool({"汤锅", "火锅"} & tags)
            )
        if value == "咖啡":
            # ``咖啡因`` is a specification and does not establish that the
            # item itself is coffee (tea/cocoa can both be low-caffeine).
            return "咖啡" in name.replace("咖啡因", "") or value in tags
        return value in name or value in tags
    parent_ids = getattr(candidate, "parent_ids", []) or []
    parent_types = {
        match.group(1)
        for parent_id in parent_ids
        if (match := re.search(r"_([A-Z])\d+$", parent_id))
    }
    if value == "酒店" and (
        getattr(candidate, "entity_type", "") == "hotel" or "H" in parent_types
    ):
        return True
    if value in {"景点", "门票"} and (
        getattr(candidate, "entity_type", "") == "attraction" or "A" in parent_types
    ):
        return True
    if value == "机票" and (
        getattr(candidate, "entity_type", "") == "flight" or "F" in parent_types
    ):
        return True
    if value in {"火车票", "高铁票"} and (
        getattr(candidate, "entity_type", "") == "train" or "T" in parent_types
    ):
        return True
    return value in raw


def category_is_groundable(value: str, candidates: list[Any]) -> bool:
    """Whether the observed set contains literal typed evidence for a category."""
    return any(
        _hard_constraint_matches(value, candidate.raw or "", candidate)
        for candidate in candidates
    )


def _contains_forbidden(raw: str, value: str) -> bool:
    if value not in raw:
        return False
    if value == "小料":
        field = re.search(r"小料\s*[:：=]\s*([^,，)\]]+)", raw)
        if field and field.group(1).strip() in {"无", "不加", "不要", "无小料", "不加小料"}:
            return False
    remainder = raw
    for phrase in (f"无{value}", f"不加{value}", f"不含{value}", f"去{value}"):
        remainder = remainder.replace(phrase, "")
    return value in remainder


def violates_exclusion(raw: str, value: str, preferences: list[str]) -> bool:
    """Canonical exclusion check shared by ranking and WRITE validation."""
    return _contains_forbidden(raw, value) and not _preferred_exception(
        raw, value, preferences
    )


def _preferred_exception(raw: str, forbidden: str, preferences: list[str]) -> bool:
    """Allow an explicit preferred subtype inside a broader avoided class."""
    if forbidden != "小料":
        return False
    negations = ("不加小料", "无小料", "不要小料", "不放小料")
    beverage_markers = ("奶茶", "奶绿", "烤奶", "饮品", "果茶")
    return any(
        any(
            topping in preference and preference.strip() != topping
            for topping in _TOPPING_MARKERS
        )
        and any(marker in preference for marker in beverage_markers)
        and not any(negation in preference for negation in negations)
        and _preference_matches(preference, raw)
        for preference in preferences
    )


_TOPPING_MARKERS = (
    "布蕾",
    "珍珠",
    "芋泥",
    "芋圆",
    "波霸",
    "椰果",
    "仙草",
    "布丁",
    "红豆",
    "奶冻",
)


def _temperature_values(text: str) -> set[str]:
    values: set[str] = set()
    if any(marker in text for marker in ("热饮", "(热", "（热", "/热", "温热")):
        values.add("hot")
    if any(marker in text for marker in ("冷饮", "(冰", "（冰", "/冰", "冰沙")):
        values.add("cold")
    if "常温" in text:
        values.add("ambient")
    return values


def _candidate_color(raw: str) -> str:
    match = re.search(r"颜色\s*[:：=]\s*([^,，)\]]+)", raw)
    return match.group(1).strip() if match else ""


def _is_muted_color(color: str) -> bool:
    reduced = color
    for muted_hue in ("雾蓝", "浅蓝", "冰蓝", "藏青"):
        reduced = reduced.replace(muted_hue, "")
    if any(marker in reduced for marker in ("红", "橙", "黄", "绿", "蓝", "紫", "粉")):
        return False
    return any(
        marker in color
        for marker in ("黑", "白", "灰", "米色", "奶油", "卡其", "藏青", "棕", "浅驼", "雾蓝")
    )


def _strong_preference_conflict(raw: str, preferences: list[str]) -> bool:
    """Reject only observable contradictions; unknown evidence remains eligible."""
    if "低饱和色系" in preferences:
        color = _candidate_color(raw)
        if color and not _is_muted_color(color):
            return True
    if "配送30分钟内" in preferences:
        durations = [
            int(duration)
            for duration in re.findall(
                r"配送(?:时长|时间)\s*[:：]?\s*(\d+)\s*分钟", raw
            )
        ]
        if durations and min(durations) > 30:
            return True
    has_visible_brand_intent = any(
        re.match(r"[A-Za-z][A-Za-z0-9&.'-]{1,20}", preference or "")
        for preference in preferences
    )
    if has_visible_brand_intent and re.search(r"品牌\s*[:：=]\s*无(?:\W|$)", raw):
        return True
    return False
