"""3D retrieval: relevance x recency x importance.

The core of the ADAPT memory system. Given a subtask instruction (query),
retrieve the most useful preference facts. Three dimensions:

1. relevance  - how well the fact matches the current task domain/intent
2. recency    - how recent the fact is (decay over time)
3. importance - type prior + confidence of the fact

Design decisions:
- domain/intent keywords in the instruction gate which predicates matter
- recency uses exponential decay (half-life in days)
- composite score = w_rel * relevance + w_rec * recency + w_imp * importance
- weights are configurable (for ablation / sensitivity analysis)

Phase 2 improvement: Adaptive retrieval routing — dynamically adjusts the
three weights based on query type. Specific queries (e.g. "和上次一样") need
higher relevance; exploratory queries (e.g. "推荐一个") need higher importance
to surface high-value historical behavior.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from agent.memory.signals import Signal
from agent.memory.stream import MemoryEvent, MemoryStream, parse_timestamp

# --- Domain keyword tables ---------------------------------------------------
# Map subtask domain -> keywords that indicate relevance. This is a lightweight
# "task-aware retrieval" — only facts whose predicate/object touches the domain
# get relevance boost. Injection of irrelevant facts is the #1 precision killer.

DOMAIN_KEYWORDS: Dict[str, List[str]] = {
    "delivery": ["外卖", "吃", "餐", "饭", "食", "店", "商家", "单", "送", "点", "菜", "饮品", "夜宵", "早餐", "午餐", "晚餐"],
    "instore": ["探店", "餐厅", "吃", "餐", "到店", "预约", "包间", "桌", "聚餐", "饭", "食", "店", "菜"],
    "ota": ["酒店", "机票", "航班", "火车", "高铁", "旅游", "旅行", "景点", "门票", "住宿", "出行", "度假", "房间", "大床房", "订票", "高铁票", "飞机", "动车", "去", "逛"],
}

# Predicate -> which domains it matters for. Keeps OTA facts from leaking into
# delivery tasks and vice versa.
PREDICATE_DOMAINS: Dict[str, str] = {
    "brand_loyalty": "*",
    "prefers_product": "*",
    "avoids_food": "*",
    "likes_food": "*",
    "intent_product": "*",
    "searches": "*",
    "raw_observation": "*",
}

# Type prior: how important each signal type is regardless of content.
# Complaints and orders outweigh browsing.
TYPE_PRIOR: Dict[str, float] = {
    "complaint": 9.0,
    "order": 8.0,
    "review": 7.0,
    "comment": 7.0,
    "add_to_cart": 6.0,
    "favorite": 6.0,
    "conversation": 5.0,
    "high_freq_browse": 5.0,
    "search": 3.0,
    "browse": 2.0,
}


@dataclass
class RetrievalConfig:
    """Weights for the 3D score. Exposed for ablation."""

    w_relevance: float = 0.5
    w_recency: float = 0.2
    w_importance: float = 0.3
    half_life_days: float = 180.0     # recency decay half-life
    top_k: int = 20
    product_type_expansions: Dict[str, List[str]] = field(default_factory=dict)
    ignore_generic_bigrams: bool = False
    structured_signal_boost: Dict[str, float] = field(default_factory=dict)
    enable_adaptive_routing: bool = True


QUERY_TYPE_WEIGHTS = {
    "specific":    {"w_relevance": 0.70, "w_recency": 0.15, "w_importance": 0.15},
    "exploratory": {"w_relevance": 0.25, "w_recency": 0.20, "w_importance": 0.55},
    "time_sensitive": {"w_relevance": 0.40, "w_recency": 0.45, "w_importance": 0.15},
    "balanced":    {"w_relevance": 0.50, "w_recency": 0.20, "w_importance": 0.30},
}

SPECIFIC_MARKERS = ["上次", "之前", "那个", "一样", "还是", "上次那个", "和之前"]
EXPLORATORY_MARKERS = ["推荐", "随便", "帮我挑", "不知道", "没想好", "都可以", "你看着", "帮我选"]
TIME_SENSITIVE_MARKERS = ["今天", "明天", "下周", "下个月", "最近", "这几天"]


class RetrievalScorer:
    """Compute 3D relevance scores for stream events given a query."""

    def __init__(self, config: Optional[RetrievalConfig] = None) -> None:
        self.config = config or RetrievalConfig()
        self._kw_cache: Dict[str, frozenset] = {}

    def _keywords(self, query: str) -> frozenset:
        """Extract 2-gram Chinese keyword tokens from a string.

        Uses character bigrams so short meaningful words ("面包", "酒店") match
        inside longer phrases ("面包坊", "酒店预订"). This is a lightweight
        substitute for a proper tokenizer — good enough for relevance gating.
        """
        if query not in self._kw_cache:
            chars = [c for c in query if "一" <= c <= "鿿"]
            grams = {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}
            self._kw_cache[query] = frozenset(grams)
        return self._kw_cache[query]

    def normalize_query(self, query: str) -> str:
        additions = []
        for source, targets in self.config.product_type_expansions.items():
            if source in query:
                additions.extend(targets)
        return " ".join([query, *additions])

    def domain(self, query: str) -> Optional[str]:
        """Guess the task domain from the instruction (delivery/instore/ota).

        Uses character-level matching so short/mixed queries still hit.
        """
        best_domain, best_score = None, 0
        for d, dkw in DOMAIN_KEYWORDS.items():
            # Character-overlap scoring: count domain keyword chars present in query.
            score = sum(1 for dk in dkw if dk in query)
            if score > best_score:
                best_score = score
                best_domain = d
        # Too low to be confident -> None (no domain gating).
        return best_domain if best_score >= 3 else None

    def classify_query(self, query: str) -> str:
        """Classify query into a type that determines retrieval strategy.

        Returns one of: "specific", "exploratory", "time_sensitive", "balanced".
        """
        if any(m in query for m in SPECIFIC_MARKERS):
            return "specific"
        if any(m in query for m in EXPLORATORY_MARKERS):
            return "exploratory"
        if any(m in query for m in TIME_SENSITIVE_MARKERS):
            return "time_sensitive"
        return "balanced"

    def _apply_adaptive_weights(self, query_type: str) -> dict:
        """Return the weight dict for a given query type."""
        return QUERY_TYPE_WEIGHTS.get(query_type, QUERY_TYPE_WEIGHTS["balanced"])

    def relevance_score(self, event: MemoryEvent, query: str, domain: Optional[str]) -> float:
        """How relevant is this event to the query? 0-1.

        Combines keyword overlap with a semantic-ish boost:
        - exact keyword overlap in the signal object/text -> high score
        - domain gate: if the query is clearly a food task, food signals get
          a boost and OTA signals get suppressed (and vice versa)
        - base floor keeps memory non-empty but low for generic queries
        """
        sig = event.signal
        if sig is None:
            return 0.05

        text = f"{sig.predicate} {sig.object} {sig.raw}"
        qkw = set(self._keywords(query))
        textkw = set(self._keywords(text))
        if self.config.ignore_generic_bigrams:
            generic = {"帮我", "给我", "送到", "家里", "赶紧", "新的", "上次", "一个", "现在"}
            qkw -= generic
            textkw -= generic
        overlap = qkw & textkw

        # 1. Exact keyword overlap is the strongest relevance signal.
        if overlap:
            return min(1.0, 0.6 + 0.1 * len(overlap))

        # 2. Domain gating: suppress out-of-domain facts.
        #    A food query should not surface travel/hotel facts (and vice versa).
        if domain:
            event_dom = self._event_domain(sig, text)
            if event_dom and event_dom != domain:
                return 0.02  # strongly suppress mismatched domain

        # 3. Generic food/travel facts get a modest floor so memory isn't starved.
        return 0.25

    def _event_domain(self, sig, text: str) -> Optional[str]:
        """Best-effort domain classification of a signal from its content."""
        # OTA signals: travel / hotel / flight / train / attraction content.
        for kw in ("酒店", "机票", "航班", "火车", "高铁", "动车", "景点", "门票", "房间",
                   "大床房", "度假", "旅行", "出游", "高铁票", "机票", "酒店预订"):
            if kw in text:
                return "ota"
        # Food signals: meals, stores, dishes, delivery.
        for kw in ("外卖", "餐厅", "店铺", "菜品", "吃饭", "吃的", "餐", "菜", "饭",
                   "米线", "火锅", "面", "店", "食", "碗", "鸡", "肉"):
            if kw in text:
                return "delivery"
        # Explicit preference from a proactive answer defaults to food context.
        if sig and sig.predicate == "explicit_preference":
            return "delivery"
        return None

    def recency_score(self, event: MemoryEvent, now: Optional[datetime] = None) -> float:
        """Exponential decay by timestamp. 1.0 = now, 0.5 at half-life."""
        dt = parse_timestamp(event.timestamp)
        if dt is None:
            return 0.5  # unknown time: neutral
        ref = now or datetime.now()
        age_days = max(0.0, (ref - dt).total_seconds() / 86400.0)
        hl = self.config.half_life_days
        return math.exp(-math.log(2) * age_days / hl) if hl > 0 else 1.0

    def importance_score(self, event: MemoryEvent) -> float:
        """Normalized importance 0-1."""
        base = TYPE_PRIOR.get(event.type, 3.0)
        conf = event.signal.confidence if event.signal else 0.5
        return min(1.0, (base / 10.0) * (0.5 + 0.5 * conf))

    def score(self, event: MemoryEvent, query: str, domain: Optional[str], now: Optional[datetime] = None) -> float:
        rel = self.relevance_score(event, query, domain)
        rec = self.recency_score(event, now)
        imp = self.importance_score(event)
        c = self.config
        return (
            c.w_relevance * rel
            + c.w_recency * rec
            + c.w_importance * imp
            + c.structured_signal_boost.get(event.type, 0.0)
        )

    def retrieve(self, stream: MemoryStream, query: str, now: Optional[datetime] = None) -> List[MemoryEvent]:
        """Return top-k events by 3D score, marking them as retrieved.

        When adaptive routing is enabled, adjusts retrieval weights based on
        query type (specific/exploratory/time_sensitive/balanced).
        """
        query = self.normalize_query(query)
        domain = self.domain(query)

        if self.config.enable_adaptive_routing:
            query_type = self.classify_query(query)
            adaptive = self._apply_adaptive_weights(query_type)
            orig_weights = (self.config.w_relevance, self.config.w_recency, self.config.w_importance)
            self.config.w_relevance = adaptive["w_relevance"]
            self.config.w_recency = adaptive["w_recency"]
            self.config.w_importance = adaptive["w_importance"]

        scored: List[Tuple[float, MemoryEvent]] = []
        for ev in stream.all():
            s = self.score(ev, query, domain, now)
            if s > 0:
                scored.append((s, ev))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = [ev for _, ev in scored[: self.config.top_k]]
        for ev in top:
            stream.mark_retrieved(ev)

        if self.config.enable_adaptive_routing:
            self.config.w_relevance, self.config.w_recency, self.config.w_importance = orig_weights

        return top
