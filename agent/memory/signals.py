"""Signal parser: raw interactions -> structured preference signals.

Each user interaction (order, search, conversation, browse, review, ...) is
converted into a structured signal (subject, predicate, object, confidence,
timestamp). Different interaction types carry different information density:

    complaint  (5/5) - negative preference, highest weight
    order      (5/5) - what was actually bought
    review     (4/5) - satisfaction signal
    add_to_cart(3/5) - strong intent (maybe not purchased yet)
    conversation(3/5)- explicit/implicit preference in dialogue
    search     (2/5) - interest signal
    high_freq_browse(3/5) - repeated interest
    browse     (1/5) - weak signal
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, List, Optional

# --- Interaction type metadata -------------------------------------------------

# Importance prior (0-10) per behavior type. complaint highest because a
# negative signal is the most informative about what NOT to recommend.
TYPE_IMPORTANCE: dict[str, float] = {
    "complaint": 9.0,
    "order": 8.0,
    "review": 7.0,
    "comment": 7.0,
    "rate": 6.0,
    "add_to_cart": 6.0,
    "favorite": 6.0,
    "conversation": 5.0,
    "high_freq_browse": 5.0,
    "search": 3.0,
    "browse": 2.0,
}

# Types that carry explicit preference content we can parse.
# Others are treated as weak/noisy signals and stored raw.
PREFERENCE_TYPES = {
    "order", "complaint", "review", "comment", "add_to_cart",
    "favorite", "high_freq_browse", "conversation", "search",
}

# Taste/temperature/sweetness dimensions worth lifting from specific products
# and order tags into *general* preference signals. Product names carry specs
# like "豆乳黑麒麟（少糖多冰）" — those specs are general preferences (sweetness/
# ice/temperature), not product identity. Each (canonical, keywords) pair emits
# one taste_preference signal when any keyword matches the order text.
TASTE_DIMENSIONS: List[tuple[str, List[str]]] = [
    # 甜度
    ("少糖", ["少糖", "少甜", "三分糖", "半糖", "0卡糖", "零卡糖"]),
    ("不加糖/无糖", ["不加糖", "无糖", "不额外加糖", "不另外加糖"]),
    # 温度/冰度（"（热"/"（冰" 匹配商品名里的括号规格，如 "黑糖布蕾奶茶（热/三分糖）"）
    ("冰镇", ["冰镇"]),
    ("多冰", ["多冰"]),
    ("少冰/去冰", ["少冰", "去冰"]),
    ("冰饮", ["冰饮", "冰沙", "（冰", "冰的", "冰奶茶"]),
    ("热饮", ["热饮", "（热", "热/", "热的"]),
    ("常温", ["常温"]),
    # 口味/汤底
    ("重口味/麻辣", ["重口味", "麻辣", "香辣", "中辣", "重辣", "加辣", "微辣"]),
    ("清淡", ["清淡", "少油", "清汤", "养生"]),
    ("酸辣/酸汤", ["酸辣", "酸汤"]),
    ("菌汤/菌菇", ["菌汤", "菌菇"]),
    # 小料（奶茶/糖水 topping）。只提升可独立选择的配料；商品名中的
    # 风味词不自动成为跨商品的规格约束。
    ("布蕾", ["布蕾"]),
    ("珍珠", ["珍珠"]),
    ("芋泥", ["芋泥"]),
    ("芋圆", ["芋圆"]),
    ("波霸", ["波霸"]),
    ("椰果", ["椰果"]),
    ("仙草", ["仙草"]),
]

# Observable attributes from completed orders that generalize within a facet.
# Keep this list semantic and small: arbitrary merchant tags would crowd the
# Decision Card and turn one-off metadata into durable preferences.
SERVICE_ATTRIBUTE_DIMENSIONS: List[tuple[str, List[str]]] = [
    ("近地铁", ["近地铁", "地铁站", "离地铁近"]),
    ("简约风格", ["简约风格", "简约装修", "简约大床房"]),
    ("经济型", ["经济型", "性价比高", "价格适中"]),
    ("无烟区", ["无烟区", "无烟房", "禁烟"]),
    ("设备新", ["设备新", "新设备", "设施新"]),
    ("低饱和色系", ["低饱和色系", "低饱和", "基础色", "基础黑"]),
    (
        "配送30分钟内",
        ["0-30分钟配送", "30分钟内配送", "配送时间不超过30分钟", "配送时长不超过30分钟"],
    ),
]


@dataclass
class Signal:
    """A single structured preference signal extracted from an interaction."""

    predicate: str              # e.g. "prefers_food", "avoids_food", "brand_loyalty"
    object: str                 # e.g. "川菜", "食尚轻厨"
    confidence: float           # 0-1
    timestamp: str              # YYYY-MM-DD HH:MM:SS
    type: str                   # original interaction type
    raw: str = ""               # raw text for later reflection/retrieval
    importance: float = 5.0     # type prior

    def to_dict(self) -> dict:
        return {
            "predicate": self.predicate,
            "object": self.object,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
            "type": self.type,
            "raw": self.raw,
            "importance": self.importance,
        }


class SignalParser:
    """Convert VitaBench interaction formats into Signal objects.

    Handles two input formats (both appear in VitaBench 2.0):
    1. Interaction objects: {type, timestamp, content}
    2. init_gen format: {date, behavior: [{behavior_type, content}], dialogue: [...]}
    """

    def parse(self, interactions: List[Any]) -> List[Signal]:
        signals: List[Signal] = []
        for inter in interactions:
            if isinstance(inter, dict):
                if "type" in inter and "timestamp" in inter:
                    signals.extend(self._parse_interaction_obj(inter))
                elif "date" in inter or "behavior" in inter:
                    signals.extend(self._parse_init_gen(inter))
                else:
                    # Unknown dict: keep raw, low confidence
                    signals.append(self._raw_signal(str(inter)))
            elif hasattr(inter, "type") and hasattr(inter, "content"):
                # Pydantic Interaction object
                signals.extend(self._parse_interaction_obj({
                    "type": inter.type, "timestamp": inter.timestamp, "content": inter.content,
                }))
            else:
                signals.append(self._raw_signal(str(inter)))
        return signals

    # -- format 1: {type, timestamp, content} -----------------------------

    def _parse_interaction_obj(self, inter: dict) -> List[Signal]:
        itype = inter.get("type", "unknown")
        ts = inter.get("timestamp", "")
        content = inter.get("content", {})
        importance = TYPE_IMPORTANCE.get(itype, 3.0)
        raw = json.dumps(content, ensure_ascii=False) if isinstance(content, (dict, list)) else str(content)

        if itype == "order":
            return self._extract_order(content, ts, raw, importance)
        if itype in ("complaint", "comment", "review"):
            return self._extract_opinion(content, ts, raw, importance, negative=(itype == "complaint"))
        if itype in ("add_to_cart", "favorite"):
            return self._extract_cart(content, ts, raw, importance, itype)
        if itype == "high_freq_browse":
            return self._extract_interest(content, ts, raw, importance, itype)
        if itype == "search":
            return self._extract_search(content, ts, raw, importance)
        # browse / high_freq_browse / conversation / unknown: store raw signal
        return [self._raw_signal(raw, ts=ts, importance=importance, itype=itype)]

    # -- format 2: {date, behavior: [...], dialogue: [...]} -----------------

    def _parse_init_gen(self, inter: dict) -> List[Signal]:
        date = inter.get("date", "")
        signals: List[Signal] = []
        for beh in inter.get("behavior", []):
            if not isinstance(beh, dict):
                continue
            btype = beh.get("behavior_type", "unknown")
            content = beh.get("content", {})
            ts = f"{date} 00:00:00"
            signals.extend(self._parse_interaction_obj({
                "type": btype, "timestamp": ts, "content": content,
            }))
        # Dialogue: parse each user turn for explicit preferences, then keep raw
        dialogue = inter.get("dialogue", [])
        if dialogue:
            ts = f"{date} 00:00:00"
            for turn in dialogue:
                if not isinstance(turn, dict):
                    continue
                role = turn.get("role", turn.get("speaker", "")).lower()
                content_text = turn.get("content", turn.get("message", ""))
                if not isinstance(content_text, str):
                    continue
                # Only parse user utterances — assistant/system turns are noise
                if role in ("user", "human", "客户", "顾客", "我"):
                    signals.extend(self._parse_dialogue_turn(content_text, ts))
            # Also keep truncated raw for fallback retrieval
            signals.append(self._raw_signal(
                json.dumps(dialogue, ensure_ascii=False),
                ts=f"{date} 00:00:00",
                importance=5.0,
                itype="conversation",
            ))
        return signals

    def _parse_dialogue_turn(self, text: str, ts: str) -> List["Signal"]:
        """Extract structured preference signals from a single user dialogue turn.

        Patterns (rule-based, no LLM):
        - LIKE:    我喜欢/我爱吃/最爱/偏好... → likes_food (conf 0.8)
        - DISLIKE: 我不吃/不喜欢/讨厌/过敏/忌口... → avoids_food (conf 0.9)
        - WANT:    我要/帮我订/来个/我想吃... → explicit_preference (conf 0.75)
        - BRAND:   我常去/我喜欢去/常点... + 店名 → brand_loyalty (conf 0.75)
        """
        signals: List[Signal] = []
        text = text.strip()
        if not text or len(text) < 3:
            return signals

        import re

        # --- DISLIKE / avoidance (highest confidence — negative signals are durable) ---
        dislike_patterns = [
            r"(?:我?不吃|不要|别放|不加|不能吃|过敏|讨厌|忌口|排斥|不碰)(.{2,12})",
            r"(.{2,12})(?:我不喜欢|我讨厌|吃不了|受不了)",
        ]
        for pat in dislike_patterns:
            for m in re.finditer(pat, text):
                obj = (m.group(1) or "").strip().rstrip("的了啊呢嗯哦")
                if obj and 2 <= len(obj) <= 12:
                    signals.append(Signal("avoids_food", obj, 0.85, ts, "conversation",
                                          text, importance=7.0))

        # --- LIKE / preference ---
        like_patterns = [
            r"(?:我喜欢吃|我爱吃|最爱吃|偏好|偏爱|喜欢吃|爱吃|爱喝)(.{2,15})",
            r"(.{2,15})(?:是我最爱|我最喜欢|我很喜欢)",
        ]
        for pat in like_patterns:
            for m in re.finditer(pat, text):
                obj = (m.group(1) or "").strip().rstrip("的了啊呢嗯哦，,。.！!？?")
                if obj and 2 <= len(obj) <= 15:
                    signals.append(Signal("likes_food", obj, 0.75, ts, "conversation",
                                          text, importance=5.0))

        # --- EXPLICIT WANT ---
        want_patterns = [
            r"(?:我想吃|我要吃|来个|给我来|我要订|帮我订|我想要)(.{2,20})",
        ]
        for pat in want_patterns:
            for m in re.finditer(pat, text):
                obj = (m.group(1) or "").strip().rstrip("的了吧啊呢，,。.")
                if obj and 2 <= len(obj) <= 20:
                    signals.append(Signal("explicit_preference", obj, 0.75, ts, "conversation",
                                          text, importance=6.0))

        # --- REUSABLE DEFAULT / future intent ---
        # Statements such as "以后吃火锅都得加一份冰汤圆" are stronger
        # than a one-off order, but do not use the ordinary "喜欢" vocabulary.
        # Preserve the requested object as a structured fact instead of leaving
        # it buried in the fallback prose summary.
        future_default_patterns = [
            r"(?:以后|下次).{0,16}?(?:都得|都要|默认|一定要)(?:加|点|选|来)?(?:一份|一个|一杯|一些)?(.{2,12})",
        ]
        for pat in future_default_patterns:
            for m in re.finditer(pat, text):
                obj = (m.group(1) or "").strip().rstrip("的了吧啊呢，,。.！!？?")
                if obj and 2 <= len(obj) <= 12:
                    signals.append(
                        Signal(
                            "explicit_preference",
                            obj,
                            0.9,
                            ts,
                            "conversation",
                            text,
                            importance=8.0,
                        )
                    )

        # --- CONDITIONAL preference ---
        # Keep the condition attached to the choice. Flattening both sides of
        # "四人以上吃麻辣，人少吃菌汤" into two unconditional
        # taste facts makes delegated decisions systematically wrong.
        conditional_patterns = (
            (
                r"(?:四个?人以上|4个?人以上|人多|多人|聚餐).{0,16}?"
                r"(麻辣火锅|麻辣锅|菌汤火锅|菌汤锅|番茄锅|清汤锅)",
                "party>=4",
            ),
            (
                r"(?:人少|两个?人|2个?人|双人).{0,16}?"
                r"(麻辣火锅|麻辣锅|菌汤火锅|菌汤锅|番茄锅|清汤锅)",
                "party<=2",
            ),
        )
        for pattern, condition in conditional_patterns:
            for match in re.finditer(pattern, text):
                signals.append(
                    Signal(
                        "conditional_preference",
                        f"{condition}=>{match.group(1)}",
                        0.9,
                        ts,
                        "conversation",
                        text,
                        importance=8.0,
                    )
                )

        # --- BRAND LOYALTY ---
        brand_patterns = [
            r"(?:我(?:经)?常(?:去|点|在)|我喜欢去|我总是去|经常光顾)(.{2,15})(?:店|餐厅|外卖|馆|家)?",
        ]
        for pat in brand_patterns:
            for m in re.finditer(pat, text):
                obj = (m.group(1) or "").strip().rstrip("的那里")
                if obj and 2 <= len(obj) <= 15:
                    signals.append(Signal("brand_loyalty", obj, 0.75, ts, "conversation",
                                          text, importance=6.0))

        return signals

    # -- extractors -----------------------------------------------------------

    def _extract_order(self, content, ts, raw, importance) -> List[Signal]:
        """Order content usually has store/product info. Extract brand + food signals."""
        signals: List[Signal] = []
        text = json.dumps(content, ensure_ascii=False) if isinstance(content, (dict, list)) else str(content)

        # VitaBench orders store the brand in `merchant_name` (not store_name/merchant).
        store = self._dig(content, "merchant_name", "store_name", "store", "merchant")
        if store:
            signals.append(Signal("brand_loyalty", store, 0.8, ts, "order", raw, importance))

        # Product names are valuable preference signals
        products = self._dig_list(content, "product_name", "products", "items")
        for p in products[:3]:
            signals.append(Signal("prefers_product", p, 0.6, ts, "order", raw, importance))

        # User remarks/notes carry explicit preferences (e.g. "少糖", "不要辣").
        remark = self._dig(content, "remark", "note")
        if remark and 2 <= len(remark) <= 60:
            signals.append(Signal("explicit_preference", remark, 0.8, ts, "order",
                                  raw, importance=7.0))

        # Generalize product specs / tags into taste dimensions (少糖/多冰/重口味…)
        signals.extend(self._extract_taste_dimensions(content, ts, importance))
        signals.extend(self._extract_service_attributes(content, ts, importance))

        if not signals:
            signals.append(self._raw_signal(text, ts=ts, importance=importance, itype="order"))
        return signals

    def _extract_service_attributes(self, content, ts, importance) -> List[Signal]:
        parts: List[str] = []
        parts.extend(self._dig_list(content, "tags", "tag"))
        parts.extend(self._dig_list(content, "product_name", "products", "items"))
        blob = " ".join(parts)
        signals: List[Signal] = []
        for canonical, keywords in SERVICE_ATTRIBUTE_DIMENSIONS:
            if any(keyword in blob for keyword in keywords):
                signals.append(
                    Signal(
                        "attribute_preference",
                        canonical,
                        0.7,
                        ts,
                        "order",
                        raw=json.dumps(content, ensure_ascii=False),
                        importance=importance,
                    )
                )
        return signals

    def _extract_taste_dimensions(self, content, ts, importance) -> List[Signal]:
        """Lift taste/temperature dimensions from order tags + product specs.

        Surface "口味偏好: 少糖、多冰" in read() instead of only the specific
        product name, so the agent can generalize across products.
        """
        parts: List[str] = []
        parts.extend(self._dig_list(content, "tags", "tag"))
        parts.extend(self._dig_list(content, "product_name", "products", "items"))
        blob = " ".join(parts)

        # A catalog name is not always the fulfilled variant. Histories can
        # contain a topping in the product name while the explicit order note
        # says not to add toppings. In that case the name is not evidence of a
        # topping preference. Keep other dimensions (temperature/sweetness),
        # but suppress topping generalization from this order.
        remark = self._dig(content, "remark", "note") or ""
        fulfillment_text = f"{remark} {blob}"
        no_topping = any(
            marker in fulfillment_text
            for marker in ("不加小料", "无小料", "不要小料", "不放小料")
        )
        topping_dimensions = {
            "布蕾",
            "珍珠",
            "芋泥",
            "芋圆",
            "波霸",
            "椰果",
            "仙草",
        }

        signals: List[Signal] = []
        for canonical, keywords in TASTE_DIMENSIONS:
            if no_topping and canonical in topping_dimensions:
                continue
            if any(kw in blob for kw in keywords):
                signals.append(Signal(
                    "taste_preference", canonical, 0.65, ts, "order",
                    blob, importance=6.0,
                ))
        return signals

    def _extract_opinion(self, content, ts, raw, importance, negative=False) -> List[Signal]:
        """Review/comment/complaint: extract the target (store/product) the user
        liked/disliked, so drift detection can compare like-for-like (store name
        vs store name) instead of full JSON blobs (which never match)."""
        # Prefer the reviewed target name (store / product / attraction).
        target = self._dig(content, "target_name", "store_name", "name", "product_name")
        if not target and isinstance(content, dict):
            # Fall back to any short string field that looks like a name.
            for v in content.values():
                if isinstance(v, str) and 2 <= len(v) <= 40 and "comment" not in v.lower():
                    target = v
                    break
        if not target:
            text = json.dumps(content, ensure_ascii=False) if isinstance(content, (dict, list)) else str(content)
            target = text[:60]

        predicate = "avoids_food" if negative else "likes_food"
        conf = 0.9 if negative else 0.6
        return [Signal(predicate, target, conf, ts, "opinion", raw, importance)]

    def _extract_cart(self, content, ts, raw, importance, itype="add_to_cart") -> List[Signal]:
        text = json.dumps(content, ensure_ascii=False) if isinstance(content, (dict, list)) else str(content)
        # Preserve the exact visible item name. Serializing the whole cart row
        # turns price/quantity metadata into a long, unusable preference value.
        item = self._dig(
            content, "target_name", "item_name", "product_name", "name"
        )
        confidence = 0.82 if itype == "favorite" else 0.7
        return [
            Signal(
                "intent_product",
                (item or text)[:100],
                confidence,
                ts,
                itype,
                raw,
                importance,
            )
        ]

    def _extract_interest(self, content, ts, raw, importance, itype) -> List[Signal]:
        """Normalize repeated browsing without encoding any domain vocabulary."""
        values = self._dig_list(
            content, "target_name", "item_name", "keyword", "keywords", "query"
        )
        if not values:
            return [self._raw_signal(raw, ts=ts, importance=importance, itype=itype)]
        return [
            Signal("observable_interest", value, 0.6, ts, itype, raw, importance)
            for value in values[:3]
        ]

    def _extract_search(self, content, ts, raw, importance) -> List[Signal]:
        keywords = self._dig_list(content, "keyword", "keywords", "query")
        if not keywords:
            keywords = [str(content)[:80]]
        return [Signal("searches", kw, 0.4, ts, "search", raw, importance) for kw in keywords[:3]]

    def _raw_signal(self, text: str, ts: str = "", importance: float = 3.0, itype: str = "raw") -> Signal:
        return Signal("raw_observation", text[:120], 0.3, ts, itype, text, importance)

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _dig(content: Any, *keys: str) -> Optional[str]:
        """Extract a string field from nested dict."""
        if not isinstance(content, dict):
            return None
        for k in keys:
            v = content.get(k)
            if isinstance(v, str) and v:
                return v
            if isinstance(v, dict):
                for vk, vv in v.items():
                    if isinstance(vv, str) and vv:
                        return vv
        return None

    @staticmethod
    def _dig_list(content: Any, *keys: str) -> List[str]:
        """Extract a list of strings from nested dict."""
        if not isinstance(content, dict):
            return []
        for k in keys:
            v = content.get(k)
            if isinstance(v, str):
                return [v]
            if isinstance(v, list):
                out = []
                for item in v:
                    if isinstance(item, str):
                        out.append(item)
                    elif isinstance(item, dict):
                        for vv in item.values():
                            if isinstance(vv, str):
                                out.append(vv)
                if out:
                    return out
        return []
