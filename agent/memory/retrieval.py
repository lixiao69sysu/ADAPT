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
- tokenization: jieba (if installed) > Chinese bigram fallback
- synonym expansion: domain-specific synonym groups for cross-term matching
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from agent.memory.stream import MemoryEvent, MemoryStream, parse_timestamp

# --- Jieba lazy import (gracefully degrade to bigram if unavailable) ----------
try:
    import jieba
    jieba.setLogLevel(60)   # suppress INFO logs
    _JIEBA_AVAILABLE = True
except ImportError:
    _JIEBA_AVAILABLE = False

# --- Synonym groups for Chinese food/travel domain ----------------------------
# Each group: if ANY term in the group appears in query, ALL terms get credit.
SYNONYM_GROUPS: List[Set[str]] = [
    # Transport modes
    {"高铁", "动车", "高速铁路", "G车", "G列"},
    {"飞机", "航班", "机票", "航空", "空中"},
    {"火车", "普铁", "K字头", "Z字头"},
    # Food / taste
    {"烧烤", "烤串", "BBQ", "炭烤", "烤肉"},
    {"川菜", "四川菜", "麻辣", "麻辣烫", "川"},
    {"日料", "日本菜", "日式", "寿司", "刺身", "拉面"},
    {"粤菜", "广东菜", "粤式", "早茶"},
    {"火锅", "麻辣锅", "鸳鸯锅", "涮锅"},
    {"快餐", "外卖", "便当", "快食"},
    {"米粉", "粉", "线粉", "螺蛳粉"},
    {"面条", "拉面", "面", "刀削面", "炸酱面"},
    # Accommodation
    {"大床房", "大床", "King床"},
    {"双人间", "双床房", "标准间"},
    {"酒店", "宾馆", "旅馆", "民宿", "客栈"},
    # Attraction / OTA
    {"景点", "景区", "旅游", "观光", "门票"},
    {"出行", "旅行", "旅游", "度假", "游览"},
]

# Build reverse lookup: term -> frozenset of all synonyms in its group
_SYNONYM_EXPAND: Dict[str, Set[str]] = {}
for _grp in SYNONYM_GROUPS:
    for _term in _grp:
        if _term not in _SYNONYM_EXPAND:
            _SYNONYM_EXPAND[_term] = set()
        _SYNONYM_EXPAND[_term].update(_grp)


def _expand_synonyms(terms: Set[str]) -> Set[str]:
    """Expand a set of tokens with their synonyms."""
    expanded = set(terms)
    for t in list(terms):
        extra = _SYNONYM_EXPAND.get(t)
        if extra:
            expanded.update(extra)
    return expanded


# --- Domain keyword tables ---------------------------------------------------
# Map subtask domain -> keywords that indicate relevance. This is a lightweight
# "task-aware retrieval" — only facts whose predicate/object touches the domain
# get relevance boost. Injection of irrelevant facts is the #1 precision killer.

DOMAIN_KEYWORDS: Dict[str, List[str]] = {
    "delivery": ["外卖", "吃", "餐", "饭", "食", "店", "商家", "单", "送", "点", "菜", "饮品", "夜宵", "早餐", "午餐", "晚餐"],
    "instore": ["探店", "餐厅", "吃", "餐", "到店", "预约", "包间", "桌", "聚餐", "饭", "食", "店", "菜"],
    "ota": ["酒店", "机票", "航班", "火车", "高铁", "旅游", "旅行", "景点", "门票", "住宿", "出行", "度假", "房间", "大床房", "订票", "高铁票", "飞机", "动车", "去", "逛", "订房", "两晚", "大床", "入住"],
}

# Strong single markers that alone identify the domain. Needed because OTA
# queries are often short ("帮我订个酒店" = 1 keyword < 3-count threshold),
# so the accumulated-count gate below would return None and disable domain
# gating entirely — v12 leaked 锦州喜来登 into a 长春 hotel hint because of it.
# Substring-matched: none of these compound markers can be a false positive.
# Ambiguous travel words (旅游/旅行/去) stay in DOMAIN_KEYWORDS to avoid
# misfiring on food-narrative queries.
STRONG_KEYWORDS: Dict[str, List[str]] = {
    "ota": ["酒店", "机票", "车票", "门票", "高铁票", "火车票", "航班", "火车", "高铁",
            "动车", "景点", "订票", "民宿", "宾馆", "房间", "大床房", "大床", "入住", "住宿"],
    "delivery": ["外卖", "闪购", "配送", "下单", "送到"],
    "instore": ["探店", "到店", "包间", "预约"],
}

# Substrings containing 票 that are NOT tickets (男票/女票 = BF/GF slang, 发票…).
# Bare "票" is an OTA marker (车票/门票/机票/去桂林的票) but must not fire here.
_TICKET_GUARD: Tuple[str, ...] = ("男票", "女票", "发票", "股票", "彩票", "传票")

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


def _normalize_domain(d: Optional[str]) -> Optional[str]:
    """Canonicalize a domain label for gating.

    `instore` and `delivery` are the same local consumption domain (food/retail):
    `_event_domain` only knows the "delivery" label, so normalizing instore ->
    delivery stops food facts from being suppressed to 0.02 on instore queries
    (instore is detected via 探店/到店/包间/预约 but food signals never carry it).
    OTA stays distinct and is still suppressed.
    """
    return "delivery" if d == "instore" else d


@dataclass
class RetrievalConfig:
    """Weights for the 3D score. Exposed for ablation."""

    w_relevance: float = 0.5
    w_recency: float = 0.2
    w_importance: float = 0.3
    half_life_days: float = 180.0     # recency decay half-life
    top_k: int = 20


class RetrievalScorer:
    """Compute 3D relevance scores for stream events given a query."""

    def __init__(self, config: Optional[RetrievalConfig] = None) -> None:
        self.config = config or RetrievalConfig()
        self._kw_cache: Dict[str, frozenset] = {}

    def _tokenize(self, text: str) -> Set[str]:
        """Tokenize text using jieba (if available) else Chinese bigrams.

        Returns a set of tokens, expanded with domain synonyms so that
        "动车" matches stored "高铁" tokens and vice versa.
        """
        if _JIEBA_AVAILABLE:
            tokens = set(jieba.lcut(text))
            # Keep only CJK tokens with len >= 2 (single chars are too noisy)
            tokens = {t for t in tokens if len(t) >= 2 and any("一" <= c <= "鿿" for c in t)}
        else:
            # Fallback: character bigrams
            chars = [c for c in text if "一" <= c <= "鿿"]
            tokens = {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}
        return _expand_synonyms(tokens)

    def _keywords(self, query: str) -> frozenset:
        """Tokenize+expand query, cached."""
        if query not in self._kw_cache:
            self._kw_cache[query] = frozenset(self._tokenize(query))
        return self._kw_cache[query]

    def domain(self, query: str) -> Optional[str]:
        """Guess the task domain from the instruction (delivery/instore/ota).

        Two-tier: a strong marker (酒店/机票/外卖/送到…) identifies the domain
        directly — needed because short OTA queries ("帮我订个酒店") fall under
        the accumulated-count threshold and lose gating entirely (v12 leaked
        锦州喜来登 into a 长春 hotel hint because of it). Bare "票" is an OTA
        marker guarded against romance slang 男票/女票.
        """
        for d, skw in STRONG_KEYWORDS.items():
            for k in skw:
                if k in query:
                    return d
        if "票" in query and not any(g in query for g in _TICKET_GUARD):
            return "ota"
        best_domain, best_score = None, 0
        for d, dkw in DOMAIN_KEYWORDS.items():
            # Character-overlap scoring: count domain keyword chars present in query.
            score = sum(1 for dk in dkw if dk in query)
            if score > best_score:
                best_score = score
                best_domain = d
        # Too low to be confident -> None (no domain gating).
        return best_domain if best_score >= 3 else None

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
        qkw = self._keywords(query)
        overlap = qkw & self._tokenize(text)

        # 1. Exact keyword overlap is the strongest relevance signal.
        if overlap:
            return min(1.0, 0.6 + 0.1 * len(overlap))

        # 2. Domain gating: suppress out-of-domain facts.
        #    A food query should not surface travel/hotel facts (and vice versa).
        if domain:
            event_dom = self._event_domain(sig, text)
            if event_dom and _normalize_domain(event_dom) != _normalize_domain(domain):
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
        return c.w_relevance * rel + c.w_recency * rec + c.w_importance * imp

    def retrieve(
        self,
        stream: MemoryStream,
        query: str,
        now: Optional[datetime] = None,
        k: Optional[int] = None,
        mark_retrieved: bool = False,
    ) -> List[MemoryEvent]:
        """Return top-k events by 3D score.

        Retrieval is pure by default because VitaBench may call ``memory.read``
        several times while constructing and logging a single system prompt.
        Callers doing explicit analytics may opt into salience accounting.
        """
        domain = self.domain(query)
        scored: List[Tuple[float, MemoryEvent]] = []
        for ev in stream.all():
            s = self.score(ev, query, domain, now)
            if s > 0:
                scored.append((s, ev))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = [ev for _, ev in scored[: (k or self.config.top_k)]]
        if mark_retrieved:
            for ev in top:
                stream.mark_retrieved(ev)
        return top
