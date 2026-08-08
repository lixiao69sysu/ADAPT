"""Memory Summarizer: convert raw fact list into structured preference summary.

Instead of injecting raw facts like:
  - 用户偏好商品: 水煮鱼 (置信 0.60)
  - 用户偏好商品: 牛肉盖饭 (置信 0.60)
  - 观察[2023-11-22] (conversation): [...]

We generate a structured summary like:
  ## 口味偏好
  - 喜欢：川菜（水煮鱼、火锅），置信高
  - 不喜欢：香菜、辣椒
  
  ## 消费习惯
  - 常点：牛肉盖饭、水煮鱼
  - 预算：中等（50-100元/餐）
  
  ## 配送信息
  - 常用地址：天津市河东华龙道月光园小区
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional

from agent.memory.signals import Signal


class MemorySummarizer:
    """Summarize raw memory facts into structured preference sections."""

    # Predicate → section name mapping
    PREDICATE_SECTIONS = {
        "likes_food": "口味偏好",
        "avoids_food": "口味偏好",
        "prefers_product": "消费习惯",
        "brand_loyalty": "消费习惯",
        "searches": "搜索历史",
        "intent_product": "购买意向",
        "delivery_address": "配送信息",
        "taste_preference": "口味偏好",
        "budget_conscious": "消费习惯",
        "quality_prefer": "消费习惯",
        "urgency": "其他偏好",
        "explicit_preference": "明确偏好",
    }

    # Section display order
    SECTION_ORDER = ["口味偏好", "消费习惯", "购买意向", "搜索历史", "配送信息", "明确偏好", "其他偏好"]

    def summarize(self, signals: List[Signal]) -> str:
        """Convert list of signals into structured summary string."""
        if not signals:
            return ""

        # Resolve conflicts before summarization
        signals = self.resolve_conflicts(signals)

        # Group signals by section
        sections: Dict[str, List[Signal]] = defaultdict(list)
        for sig in signals:
            section = self.PREDICATE_SECTIONS.get(sig.predicate, "其他偏好")
            sections[section].append(sig)

        # Build summary
        lines = ["【用户偏好摘要】"]
        for section_name in self.SECTION_ORDER:
            if section_name not in sections:
                continue
            section_signals = sections[section_name]
            lines.append(f"\n## {section_name}")
            lines.extend(self._summarize_section(section_name, section_signals))

        return "\n".join(lines)

    def _summarize_section(self, section_name: str, signals: List[Signal]) -> List[str]:
        """Summarize signals within a section."""
        if section_name == "口味偏好":
            return self._summarize_taste(signals)
        elif section_name == "消费习惯":
            return self._summarize_consumption(signals)
        elif section_name == "购买意向":
            return self._summarize_intent(signals)
        elif section_name == "搜索历史":
            return self._summarize_searches(signals)
        elif section_name == "配送信息":
            return self._summarize_address(signals)
        elif section_name == "明确偏好":
            return self._summarize_explicit(signals)
        else:
            return self._summarize_generic(signals)

    def _summarize_taste(self, signals: List[Signal]) -> List[str]:
        """Summarize taste preferences."""
        likes = []
        avoids = []
        for sig in signals:
            if sig.predicate == "likes_food":
                likes.append(sig.object)
            elif sig.predicate == "avoids_food":
                avoids.append(sig.object)
            elif sig.predicate == "taste_preference":
                likes.append(sig.object)

        lines = []
        if likes:
            # Deduplicate and sort by confidence
            unique_likes = sorted(set(likes), key=lambda x: -self._get_confidence(x, signals))
            lines.append(f"- 喜欢：{', '.join(unique_likes[:5])}")
        if avoids:
            unique_avoids = sorted(set(avoids), key=lambda x: -self._get_confidence(x, signals))
            lines.append(f"- 不喜欢/避免：{', '.join(unique_avoids[:5])}")
        return lines

    def _summarize_consumption(self, signals: List[Signal]) -> List[str]:
        """Summarize consumption habits."""
        products = []
        brands = []
        for sig in signals:
            if sig.predicate in ("prefers_product", "intent_product"):
                products.append(sig.object)
            elif sig.predicate == "brand_loyalty":
                brands.append(sig.object)
            elif sig.predicate == "budget_conscious":
                products.append("预算敏感/偏好实惠")
            elif sig.predicate == "quality_prefer":
                products.append("注重品质")

        lines = []
        if products:
            unique_products = sorted(set(products), key=lambda x: -self._get_confidence(x, signals))
            lines.append(f"- 常点/偏好：{', '.join(unique_products[:5])}")
        if brands:
            unique_brands = sorted(set(brands), key=lambda x: -self._get_confidence(x, signals))
            lines.append(f"- 忠诚店铺：{', '.join(unique_brands[:3])}")
        return lines

    def _summarize_intent(self, signals: List[Signal]) -> List[str]:
        """Summarize purchase intentions."""
        intents = [sig.object for sig in signals if sig.predicate == "intent_product"]
        if not intents:
            return []
        unique_intents = sorted(set(intents), key=lambda x: -self._get_confidence(x, signals))
        return [f"- 有意向：{', '.join(unique_intents[:5])}"]

    def _summarize_searches(self, signals: List[Signal]) -> List[str]:
        """Summarize search history."""
        searches = [sig.object for sig in signals if sig.predicate == "searches"]
        if not searches:
            return []
        # Keep only recent/important searches
        unique_searches = sorted(set(searches), key=lambda x: -self._get_confidence(x, signals))
        return [f"- 搜索过：{', '.join(unique_searches[:5])}"]

    def _summarize_address(self, signals: List[Signal]) -> List[str]:
        """Summarize delivery addresses."""
        addresses = [sig.object for sig in signals if sig.predicate == "delivery_address"]
        if not addresses:
            return []
        # Keep unique addresses
        unique_addresses = sorted(set(addresses))
        return [f"- 常用地址：{', '.join(unique_addresses[:3])}"]

    def _summarize_explicit(self, signals: List[Signal]) -> List[str]:
        """Summarize explicit preferences from proactive answers."""
        prefs = [sig.object for sig in signals if sig.predicate == "explicit_preference"]
        if not prefs:
            return []
        return [f"- 明确偏好：{', '.join(prefs[:3])}"]

    def _summarize_generic(self, signals: List[Signal]) -> List[str]:
        """Generic summarization for uncategorized signals."""
        items = []
        for sig in signals:
            items.append(f"- {sig.predicate}: {sig.object}")
        return items[:5]

    @staticmethod
    def _get_confidence(obj: str, signals: List[Signal]) -> float:
        """Get max confidence for an object across signals."""
        return max((sig.confidence for sig in signals if sig.object == obj), default=0.5)

    @staticmethod
    def resolve_conflicts(signals: List[Signal]) -> List[Signal]:
        """Detect and resolve conflicting preferences.
        
        Conflict patterns:
        - likes:X vs avoids:X → keep the one with higher confidence * recency
        - multiple brand_loyalty → keep most recent
        """
        from collections import defaultdict
        from datetime import datetime
        
        # Group by (normalized_object, predicate_group)
        likes_map = defaultdict(list)
        avoids_map = defaultdict(list)
        
        for sig in signals:
            obj = sig.object.strip()
            if sig.predicate == "likes_food":
                likes_map[obj].append(sig)
            elif sig.predicate == "avoids_food":
                avoids_map[obj].append(sig)
        
        # Find conflicts
        conflicting = set(likes_map.keys()) & set(avoids_map.keys())
        
        if not conflicting:
            return signals
        
        # Resolve: keep higher score (confidence * recency_factor)
        to_remove = set()
        
        def score(sig):
            conf = sig.confidence
            # Recency bonus
            try:
                ts = parse_timestamp(sig.timestamp)
                if ts:
                    days_ago = (datetime.now() - ts).days
                    recency = max(0.5, 1.0 - days_ago / 365.0)
                else:
                    recency = 0.5
            except Exception:
                recency = 0.5
            return conf * recency
        
        for obj in conflicting:
            best_like = max(likes_map[obj], key=score)
            best_avoid = max(avoids_map[obj], key=score)
            
            if score(best_like) >= score(best_avoid):
                # Remove all avoids for this object
                for sig in avoids_map[obj]:
                    to_remove.add(id(sig))
            else:
                # Remove all likes for this object
                for sig in likes_map[obj]:
                    to_remove.add(id(sig))
        
        return [sig for sig in signals if id(sig) not in to_remove]


def parse_timestamp(ts: str):
    """Parse timestamp string."""
    if not ts:
        return None
    from datetime import datetime
    try:
        return datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(ts[:10], "%Y-%m-%d")
        except ValueError:
            return None
