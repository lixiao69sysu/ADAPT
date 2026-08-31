"""Typed preference facts used by the ADAPT decision layer.

The existing :class:`Signal` stream remains the evidence log.  PreferenceFact
is the compact, task-facing projection used for conflict resolution and prompt
construction; it deliberately contains no VitaBench evaluation fields.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from hashlib import sha1

from agent.memory.signals import Signal


@dataclass
class PreferenceFact:
    """A scoped preference derived from one or more observable interactions."""

    fact_id: str
    scope: str
    facet: str
    dimension: str
    value: str
    polarity: str
    confidence: float
    observed_at: str
    source_type: str
    category: str = "general"
    evidence_ids: list[str] = field(default_factory=list)
    status: str = "active"
    evidence_types: list[str] = field(default_factory=list)
    decision_eligible: bool = True

    @property
    def slot_key(self) -> tuple[str, str, str, str]:
        category = self.value if self.polarity == "negative" else self.category
        return self.scope, self.facet, self.dimension, category


_FACET_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("hotel", ("酒店", "宾馆", "民宿", "房型", "大床房", "海景房")),
    ("train", ("高铁", "火车", "动车", "车次", "座位")),
    ("flight", ("飞机", "航班", "机票", "经济舱", "商务舱")),
    ("attraction", ("景点", "门票", "乐园", "博物馆")),
    ("taxi", ("打车", "出租", "网约车")),
    (
        "beverage",
        (
            "奶茶",
            "咖啡",
            "饮品",
            "茶饮",
            "喝",
            "杯",
            "无糖",
            "少糖",
            "三分糖",
            "多冰",
            "去冰",
            "热饮",
            "常温",
        ),
    ),
    ("wellness", ("足疗", "按摩", "美容", "理发", "美甲")),
    ("entertainment", ("电玩", "KTV", "猫咖", "剧本杀", "密室")),
    (
        "restaurant",
        (
            "餐厅",
            "饭",
            "菜",
            "吃",
            "聚餐",
            "团建餐",
            "包间",
            "火锅",
            "汤锅",
            "烧烤",
            "外卖",
            "小吃",
        ),
    ),
    ("retail", ("鼠标", "拖鞋", "衣服", "鞋", "手办", "充电宝", "果切", "水果", "零售", "商品")),
)

_SCOPE_TAG = re.compile(r"\[scope=(delivery|instore|ota|general)\]")
_FACET_TAG = re.compile(r"\[facet=([a-z_]+)\]")

_CATEGORY_MARKERS: dict[str, tuple[str, ...]] = {
    "beverage": ("奶茶", "咖啡", "果茶", "茶饮"),
    "restaurant": ("火锅", "汤锅", "烧烤"),
    "retail": ("拖鞋", "运动鞋", "休闲鞋", "板鞋", "跑鞋", "鞋", "衣服", "外套", "鼠标", "充电宝", "手办"),
}

_STRUCTURED_SCENARIO_FACETS = {
    "hotel": "hotel",
    "flight": "flight",
    "train": "train",
    "attraction": "attraction",
    "taxi": "taxi",
}


def infer_facet(text: str, domain: str | None = None) -> str:
    blob = text or ""
    for facet, markers in _FACET_MARKERS:
        if any(marker in blob for marker in markers):
            return facet
    if domain == "ota":
        return "travel"
    if domain == "instore":
        return "service"
    if domain == "delivery":
        return "retail"
    return "general"


def infer_scope(facet: str, domain: str | None = None) -> str:
    if domain in {"delivery", "instore", "ota", "general"}:
        return domain
    if facet in {"hotel", "train", "flight", "attraction", "taxi", "travel"}:
        return "ota"
    if facet in {"wellness", "entertainment", "service"}:
        return "instore"
    if facet in {"beverage", "restaurant", "retail"}:
        return "local_commerce"
    return "general"


def _structured_context(raw: str, text: str) -> tuple[str | None, str | None]:
    """Read observable interaction roles before falling back to surface words."""
    try:
        payload = json.loads(raw) if raw and raw.lstrip().startswith("{") else None
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = None
    if not isinstance(payload, dict):
        return None, None
    scenario = str(payload.get("scenario", "")).strip().lower()
    if scenario in _STRUCTURED_SCENARIO_FACETS:
        return "ota", _STRUCTURED_SCENARIO_FACETS[scenario]
    if scenario == "travel_ticket":
        facet = infer_facet(text, "ota")
        return "ota", facet if facet in {"flight", "train"} else "travel"
    if scenario in {"delivery", "instore"}:
        return scenario, infer_facet(text, scenario)
    return None, None


def infer_category(text: str, facet: str) -> str:
    """Infer a within-facet category used to prevent preference bleed."""
    blob = text or ""
    for marker in _CATEGORY_MARKERS.get(facet, ()):
        if marker in blob:
            if facet == "retail" and marker.endswith("鞋"):
                return "鞋"
            return marker
    return facet


def infer_dimension(signal: Signal) -> str:
    if signal.predicate == "avoids_food":
        evidence = f"{signal.object} {signal.raw}"
        if any(marker in evidence for marker in ("过敏", "忌口", "不能吃", "不耐受", "禁食")):
            return "safety"
        return "avoid"
    if signal.predicate == "brand_loyalty":
        return "brand"
    if signal.predicate in {"prefers_product", "intent_product", "observable_interest"}:
        return "product"
    if signal.predicate == "taste_preference":
        value = signal.object
        if any(x in value for x in ("冰", "热", "常温")):
            return "temperature"
        if any(x in value for x in ("糖", "甜")):
            return "sweetness"
        if any(x in value for x in ("辣", "清淡", "酸", "麻")):
            return "taste"
        if any(x in value for x in ("珍珠", "布蕾", "椰果", "小料")):
            return "topping"
        return "attribute"
    if signal.predicate == "attribute_preference":
        if "地铁" in signal.object:
            return "location"
        if signal.object in {"经济型"}:
            return "budget"
        return "attribute"
    if signal.predicate == "conditional_preference":
        return "conditional"
    if signal.predicate == "likes_food":
        return "like"
    if signal.predicate == "explicit_preference":
        text = f"{signal.object} {signal.raw}"
        dimension_markers = (
            ("transport", ("出行", "高铁", "飞机", "火车")),
            ("room_type", ("房型", "大床", "双床", "住宿")),
            ("party_size", ("几个人", "人数", "人一起")),
            ("time", ("什么时间", "几点", "上午", "下午", "晚上")),
            ("caffeine", ("咖啡因",)),
            ("taste", ("口味", "清淡", "麻辣", "香辣")),
            ("budget", ("预算", "价格范围")),
        )
        for dimension, markers in dimension_markers:
            if any(marker in text for marker in markers):
                return dimension
        return "explicit"
    return signal.predicate


def fact_from_signal(signal: Signal, evidence_id: str = "") -> PreferenceFact:
    text = f"{signal.object} {signal.raw}"
    scope_match = _SCOPE_TAG.search(text)
    facet_match = _FACET_TAG.search(text)
    structured_domain, structured_facet = _structured_context(signal.raw, text)
    domain = scope_match.group(1) if scope_match else structured_domain
    facet = (
        facet_match.group(1)
        if facet_match
        else structured_facet or infer_facet(text, domain)
    )
    polarity = "negative" if signal.predicate == "avoids_food" else "positive"
    category = infer_category(text, facet)
    raw_id = f"{signal.predicate}|{signal.object}|{signal.timestamp}|{evidence_id}"
    fact_id = sha1(raw_id.encode("utf-8")).hexdigest()[:16]
    return PreferenceFact(
        fact_id=fact_id,
        scope=infer_scope(facet, domain),
        facet=facet,
        dimension=infer_dimension(signal),
        value=signal.object.strip(),
        polarity=polarity,
        confidence=signal.confidence,
        observed_at=signal.timestamp,
        source_type=signal.type,
        category=category,
        evidence_ids=[evidence_id] if evidence_id else [],
        evidence_types=[signal.type] if signal.type else [],
        # Search and repeated browsing may help rank candidates, but a single
        # weak behavioral trace must not deterministically lock a WRITE.
        decision_eligible=signal.type not in {"search", "high_freq_browse", "browse"},
    )


def project_facts(signals: Iterable[Signal]) -> list[PreferenceFact]:
    return [
        fact_from_signal(signal, str(index)) for index, signal in enumerate(signals)
    ]
