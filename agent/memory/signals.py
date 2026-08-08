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

Phase 1 improvement: ConversationParser extracts structured preference facts
from dialogue text with confidence levels (explicit=high, implicit=low),
instead of storing raw conversation blobs that the agent cannot use.
"""

from __future__ import annotations

import json
import re
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


@dataclass
class ConversationPattern:
    """A regex pattern for extracting preference from dialogue."""
    pattern: str
    predicate: str
    confidence: float
    group_index: int = 1


class ConversationParser:
    """Extract structured preference signals from conversation text.

    Two tiers of extraction:
    - Explicit statements (high confidence): "我喜欢X", "我不吃Y"
    - Implicit cues (low confidence): "不辣", "清淡", "尽快"
    """

    EXPLICIT: List[ConversationPattern] = [
        ConversationPattern(r"我(?:最|超)?喜欢(?:吃|喝|用|买|去)?([^，。, .]+)", "likes_food", 0.90),
        ConversationPattern(r"我不喜欢(?:吃|喝|用|买|去)?([^，。, .]+)", "avoids_food", 0.90),
        ConversationPattern(r"我不(?:吃|喝|用|喜欢)([^，。, .]+)", "avoids_food", 0.90),
        ConversationPattern(r"别(?:放|加)([^，。, .]+)", "avoids_food", 0.85),
        ConversationPattern(r"不要(?:放|加)?([^，。, .]+)", "avoids_food", 0.85),
        ConversationPattern(r"帮我找(?:个|一家|一间)?([^，。, .]+)", "searches", 0.70),
        ConversationPattern(r"上次那个(.+)", "prefers_product", 0.80),
        ConversationPattern(r"和上次一样", "prefers_product", 0.75),
        ConversationPattern(r"跟上次一样", "prefers_product", 0.75),
        ConversationPattern(r"送到([^，。, .]+)", "delivery_address", 0.95),
        ConversationPattern(r"来一份([^，。, .]+)", "prefers_product", 0.80),
        ConversationPattern(r"点个([^，。, .]+)", "prefers_product", 0.80),
        ConversationPattern(r"想吃([^，。, .]+)", "likes_food", 0.85),
        ConversationPattern(r"要([^，。, .]+?)(?:送到|外卖|吧)", "prefers_product", 0.75),
        ConversationPattern(r"([^，。, .]+?)(?:真的?好(?:吃|喝|用)|挺(?:好|不错)(?:的)?)", "likes_food", 0.70),
    ]

    _VERB_PREFIXES = ("吃", "喝", "用", "买", "去", "来", "做", "送")

    IMPLICIT: List[ConversationPattern] = [
        ConversationPattern(r"(不辣|微辣|清淡)", "taste_preference", 0.40),
        ConversationPattern(r"(麻辣|重口|辣一点)", "taste_preference", 0.40),
        ConversationPattern(r"(尽快|赶紧|快)", "urgency", 0.30),
        ConversationPattern(r"(便宜|实惠|省钱)", "budget_conscious", 0.35),
        ConversationPattern(r"(贵|好一点|品质)", "quality_prefer", 0.35),
    ]

    _CHINESE_CHAR = re.compile(r"[一-鿿]")
    _LEADING_JUNK = re.compile(r"^[，。, .~～！!？;；：]+")
    _TRAILING_JUNK = re.compile(r"[，。, .~～！!？;；：了啦]+$")

    @classmethod
    def _is_valid_object(cls, obj: str) -> bool:
        """Check if extracted object is meaningful (not punctuation/noise)."""
        if len(obj) < 2:
            return False
        if not cls._CHINESE_CHAR.search(obj):
            return False
        stripped = cls._TRAILING_JUNK.sub("", cls._LEADING_JUNK.sub("", obj))
        if len(stripped) < 2:
            return False
        return True

    @classmethod
    def _clean_object(cls, obj: str) -> str:
        """Strip leading verb prefixes and punctuation from extracted objects."""
        obj = cls._LEADING_JUNK.sub("", obj)
        obj = cls._TRAILING_JUNK.sub("", obj)
        obj = obj.strip()
        for prefix in cls._VERB_PREFIXES:
            if obj.startswith(prefix) and len(obj) > len(prefix) + 1:
                obj = obj[len(prefix):]
                break
        return obj.strip()

    @classmethod
    def extract(cls, text: str, ts: str, raw: str, importance: float) -> List[Signal]:
        """Extract structured signals from a piece of dialogue text."""
        signals: List[Signal] = []
        seen: set[tuple[str, str]] = set()

        for cp in cls.EXPLICIT:
            for m in re.finditer(cp.pattern, text):
                obj = cls._clean_object(m.group(cp.group_index))[:40]
                if not cls._is_valid_object(obj):
                    continue
                key = (cp.predicate, obj)
                if key in seen:
                    continue
                seen.add(key)
                signals.append(Signal(cp.predicate, obj, cp.confidence, ts, "conversation", raw, importance))

        for cp in cls.IMPLICIT:
            for m in re.finditer(cp.pattern, text):
                obj = cls._clean_object(m.group(cp.group_index))[:40]
                if not cls._is_valid_object(obj):
                    continue
                key = (cp.predicate, obj)
                if key in seen:
                    continue
                seen.add(key)
                signals.append(Signal(cp.predicate, obj, cp.confidence, ts, "conversation", raw, importance))

        return cls._deduplicate(signals)

    @classmethod
    def _deduplicate(cls, signals: List[Signal]) -> List[Signal]:
        """Remove signals whose object is a substring of another signal's object."""
        if len(signals) <= 1:
            return signals
        result = []
        for i, sig in enumerate(signals):
            is_substring = False
            for j, other in enumerate(signals):
                if i != j:
                    if sig.object in other.object and len(sig.object) < len(other.object):
                        is_substring = True
                        break
            if not is_substring:
                result.append(sig)
        return result


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
            return self._extract_cart(content, ts, raw, importance)
        if itype == "search":
            return self._extract_search(content, ts, raw, importance)
        if itype == "conversation":
            return self._extract_conversation(content, ts, raw, importance)
        # browse / high_freq_browse / unknown: store raw signal
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
        dialogue = inter.get("dialogue", [])
        if dialogue:
            ts = f"{date} 00:00:00"
            raw = json.dumps(dialogue, ensure_ascii=False)
            conv_signals = self._extract_conversation(dialogue, ts, raw, 5.0)
            if conv_signals:
                signals.extend(conv_signals)
            else:
                signals.append(self._raw_signal(raw, ts=ts, importance=5.0, itype="conversation"))
        return signals

    # -- extractors -----------------------------------------------------------

    def _extract_order(self, content, ts, raw, importance) -> List[Signal]:
        """Order content usually has store/product info. Extract brand + food signals."""
        signals: List[Signal] = []
        text = json.dumps(content, ensure_ascii=False) if isinstance(content, (dict, list)) else str(content)

        store = self._dig(content, "store_name", "store", "merchant")
        if store:
            signals.append(Signal("brand_loyalty", store, 0.8, ts, "order", raw, importance))

        # Product names are valuable preference signals
        products = self._dig_list(content, "product_name", "products", "items")
        for p in products[:3]:
            signals.append(Signal("prefers_product", p, 0.6, ts, "order", raw, importance))

        if not signals:
            signals.append(self._raw_signal(text, ts=ts, importance=importance, itype="order"))
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

    def _extract_cart(self, content, ts, raw, importance) -> List[Signal]:
        text = json.dumps(content, ensure_ascii=False) if isinstance(content, (dict, list)) else str(content)
        return [Signal("intent_product", text[:100], 0.7, ts, "cart", raw, importance)]

    def _extract_search(self, content, ts, raw, importance) -> List[Signal]:
        keywords = self._dig_list(content, "keyword", "keywords", "query")
        if not keywords:
            keywords = [str(content)[:80]]
        return [Signal("searches", kw, 0.4, ts, "search", raw, importance) for kw in keywords[:3]]

    def _extract_conversation(self, content, ts: str, raw: str, importance: float) -> List[Signal]:
        """Extract structured preference signals from conversation content.

        Handles both:
        - String content: direct regex extraction
        - List/dict content (dialogue format): extract from each turn
        """
        signals: List[Signal] = []

        if isinstance(content, str):
            signals.extend(ConversationParser.extract(content, ts, raw, importance))
        elif isinstance(content, list):
            for turn in content:
                if isinstance(turn, dict):
                    text = turn.get("content", "")
                    if isinstance(text, str) and text:
                        signals.extend(ConversationParser.extract(text, ts, raw, importance))
                elif isinstance(turn, str):
                    signals.extend(ConversationParser.extract(turn, ts, raw, importance))
        elif isinstance(content, dict):
            text = content.get("content", "")
            if isinstance(text, str) and text:
                signals.extend(ConversationParser.extract(text, ts, raw, importance))

        return signals

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
