"""Proactive asking engine: detect information gaps and ask targeted questions.

VitaBench 2.0's proactive subtasks hide `user_intention` — it is only
disclosed when the agent proactively asks a directly relevant question. The
rubric then checks the agent picked the *right* option for that hidden intent.

Two gap patterns drive asking:
1. Missing decision dimension: the instruction omits a key choice the rubric
   grades (e.g. "买去迪的票" doesn't say 高铁/飞机/汽车 — must ask).
2. Vague + no memory: instruction is uncertain AND memory lacks a preference.

Key heuristics (domain-specific):
- ota: if instruction mentions a trip but not the transport mode -> ask
- delivery/instore: if instruction vague about taste/type -> ask

Phase 4 improvement: Expanded missing-dimension detection across more
domains and service types (delivery product type, OTA date/hotel type,
instore occasion). Added should_stop_asking() to avoid over-asking.
"""

from __future__ import annotations

from typing import List, Optional

VAGUE_MARKERS = [
    "随便", "帮我挑", "帮我看", "没想好", "不知道", "都可以", "听你的",
    "你看着", "推荐", "哪家", "帮我选", "不想纠结", "帮我想想",
]

# Decision dimensions per domain that the rubric is likely to grade.
# (dimension_name, question)
DOMAIN_QUESTIONS: dict[str, List[tuple[str, str]]] = {
    "delivery": [
        ("口味偏好", "您更倾向什么口味？比如清淡、麻辣、烧烤等。"),
        ("预算", "这餐大概的预算范围是多少呢？"),
    ],
    "instore": [
        ("场景人数", "大概是几个人一起呢？有包间或其他要求吗？"),
        ("餐厅类型", "您更想吃什么类型的呢？火锅、川菜、西餐或其他？"),
    ],
    "ota": [
        ("出行方式", "这趟出行您是倾向飞机还是高铁呢？"),
        ("预算", "大概的预算范围是多少呢？"),
    ],
}

# Missing-dimension detectors: given an instruction, return the dimension that
# is *missing* (None if the instruction already specifies it).
TRANSPORT_KEYWORDS = ("高铁", "飞机", "动车", "火车", "机票", "航班", "经济舱")
HOTEL_TYPE_KEYWORDS = ("大床房", "双床房", "套房", "标间", "豪华", "经济")
DATE_KEYWORDS = ("明天", "后天", "周一", "周二", "周三", "周四", "周五", "周六", "周日",
                 "下周一", "下周二", "这周", "下周", "今天", "号", "月")
FOOD_TYPE_KEYWORDS = ("火锅", "川菜", "粤菜", "西餐", "烧烤", "面条", "米饭", "饺子",
                      "粥", "粉", "米线", "汉堡", "披萨", "寿司", "麻辣烫", "海鲜")
DELIVERY_PRODUCT_KEYWORDS = ("衣服", "外套", "裤子", "鞋", "帽", "包", "手表", "口红",
                             "面膜", "手机", "电脑", "书", "水果", "花", "礼品")


class ProactiveEngine:
    """Detect information gaps and decide whether/what to ask."""

    def __init__(self, max_questions: int = 2) -> None:
        self.max_questions = max_questions
        self.asked_this_subtask: int = 0

    def reset_subtask(self) -> None:
        self.asked_this_subtask = 0

    def is_vague(self, instruction: str) -> bool:
        """Whether the instruction signals uncertainty / wants us to decide."""
        return any(m in instruction for m in VAGUE_MARKERS)

    # -- domain coverage ---------------------------------------------------

    def _domain_covered(self, memory_text: str, domain: Optional[str]) -> bool:
        low = memory_text or ""
        if not low or low == "No user preference information available yet.":
            return False
        if domain == "ota":
            return any(k in low for k in ("酒店", "机票", "航班", "高铁", "出行", "住宿", "房间", "景点"))
        return any(k in low for k in ("口味", "喜欢", "爱吃", "偏好", "餐", "店"))

    # -- missing-dimension detectors ---------------------------------------

    def _missing_transport(self, instruction: str) -> bool:
        """OTA: instruction clearly involves buying travel tickets but does not
        specify the transport mode (high-speed rail vs plane vs car).

        Requires a STRONG travel-buying signal — just saying "去" (go) is not
        enough (e.g. "去聚餐"). The presence of 票/订票/出行/机票 strongly
        implies travel ticketing.
        """
        travel_buying = any(k in instruction for k in ("票", "机票", "订票", "出行", "车票"))
        specifies_transport = any(k in instruction for k in TRANSPORT_KEYWORDS)
        return travel_buying and not specifies_transport

    def _missing_taste(self, instruction: str) -> bool:
        """delivery/instore: vague about what to eat."""
        if any(k in instruction for k in ("吃", "餐", "饭", "菜", "外卖", "店")):
            # If no explicit taste/cuisine is given, it's a gap.
            return not any(k in instruction for k in ("辣", "清淡", "火锅", "烧烤", "川菜", "粤菜", "西餐", "类型"))
        return False

    def _missing_food_type(self, instruction: str) -> bool:
        """Delivery: instruction says '点个外卖' but doesn't say what kind of food."""
        if any(k in instruction for k in ("外卖", "点个", "来一份")):
            return not any(k in instruction for k in FOOD_TYPE_KEYWORDS)
        return False

    def _missing_delivery_product_type(self, instruction: str) -> bool:
        """Delivery (non-food): '帮我买X' but X is vague or generic."""
        if any(k in instruction for k in ("买", "帮我买")):
            has_product = any(k in instruction for k in DELIVERY_PRODUCT_KEYWORDS)
            is_food = any(k in instruction for k in FOOD_TYPE_KEYWORDS)
            if not has_product and not is_food:
                return True
        return False

    def _missing_hotel_type(self, instruction: str) -> bool:
        """OTA: booking hotel but room type not specified."""
        if any(k in instruction for k in ("订酒店", "住宿", "住酒店", "酒店")):
            return not any(k in instruction for k in HOTEL_TYPE_KEYWORDS)
        return False

    def _missing_date(self, instruction: str) -> bool:
        """OTA/instore: planning a trip or reservation but no specific date."""
        has_travel = any(k in instruction for k in ("去", "订票", "出行", "旅游", "订酒店", "预约"))
        has_date = any(k in instruction for k in DATE_KEYWORDS)
        return has_travel and not has_date

    def _missing_instore_occasion(self, instruction: str) -> bool:
        """Instore: dining out but occasion/size unspecified."""
        if any(k in instruction for k in ("探店", "到店", "餐厅", "吃")):
            has_occasion = any(k in instruction for k in ("聚餐", "约会", "生日", "商务", "家庭", "朋友"))
            has_size = any(k in instruction for k in ("个人", "两个人", "几个人", "大家"))
            return not has_occasion and not has_size
        return False

    def _count_missing_dimensions(self, instruction: str, domain: str) -> list[tuple[str, str]]:
        """Return all missing dimensions as [(dimension_name, question), ...].
        Sorted by estimated information gain (highest first).
        """
        gaps = []

        if domain == "ota":
            if self._missing_transport(instruction):
                gaps.append(("出行方式", "这趟出行您是倾向飞机还是高铁呢？"))
            if self._missing_hotel_type(instruction):
                gaps.append(("房间类型", "您对房间类型有偏好吗？比如大床房、双床房等。"))
            if self._missing_date(instruction):
                gaps.append(("出行日期", "请问您计划什么时候出发呢？"))

        elif domain == "delivery":
            if self._missing_food_type(instruction):
                gaps.append(("餐品类型", "您更想吃哪类餐品？比如面条、米饭、火锅、烧烤等。"))
            elif self._missing_taste(instruction):
                gaps.append(("口味偏好", "您更倾向什么口味？清淡、麻辣、或其他？"))
            if self._missing_delivery_product_type(instruction):
                gaps.append(("商品类型", "您具体想买哪一类商品呢？"))

        elif domain == "instore":
            if self._missing_instore_occasion(instruction):
                gaps.append(("用餐场景", "大概是几个人一起呢？有包间或其他要求吗？"))
            if self._missing_taste(instruction):
                gaps.append(("餐厅类型", "您更想吃什么类型的呢？火锅、川菜、西餐或其他？"))

        return gaps

    def should_stop_asking(self, instruction: str, memory_text: str, domain: str) -> bool:
        """Determine if we already have enough information to proceed.

        Returns True when memory covers the key decision dimensions,
        meaning further asking would be counterproductive.
        """
        gaps = self._count_missing_dimensions(instruction, domain)
        if not gaps:
            return True
        if memory_text and memory_text != "No user preference information available yet.":
            uncovered = [g for g in gaps if not self._domain_covered(memory_text, domain)]
            if not uncovered:
                return True
        return False

    def decide_to_ask(
        self,
        instruction: str,
        memory_text: str,
        domain: Optional[str],
        consume: bool = False,
    ) -> Optional[str]:
        """Return the question to ask, or None if no gap / budget exhausted.

        Uses multi-dimension gap detection to find the highest-information-gap
        question. Falls back to vague + memory-uncovered pattern.
        """
        if consume and self.asked_this_subtask >= self.max_questions:
            return None

        if self.should_stop_asking(instruction, memory_text, domain or "delivery"):
            return None

        question = None

        # Pattern 1: missing transport mode — the highest-value proactive case.
        if self._missing_transport(instruction):
            question = "这趟出行您是倾向飞机还是高铁呢？"
        else:
            domain = domain or "delivery"

            # Pattern 2: multi-dimension gap detection.
            gaps = self._count_missing_dimensions(instruction, domain)
            if gaps:
                uncovered = [
                    g for g in gaps
                    if not self._domain_covered(memory_text, domain)
                ]
                if uncovered:
                    question = uncovered[0][1]
                elif gaps:
                    question = gaps[0][1]

            # Pattern 3: vague + memory doesn't cover the domain.
            if not question and self.is_vague(instruction) and not self._domain_covered(memory_text, domain):
                question = DOMAIN_QUESTIONS[domain][0][1]

        if question and consume:
            self.asked_this_subtask += 1
        return question

    def record_answer(self, question: str, answer: str, memory) -> None:
        """Store a confirmed preference from a user answer back into memory."""
        from agent.memory.signals import Signal

        sig = Signal(
            predicate="explicit_preference",
            object=answer.strip()[:80],
            confidence=0.95,
            timestamp="",
            type="conversation",
            raw=f"用户回答: {answer}",
            importance=8.0,
        )
        memory.stream.add(sig)
