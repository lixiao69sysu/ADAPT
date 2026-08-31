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
- instore: if group dining but no headcount/room specified -> ask
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
        ("餐厅类型", "您想吃什么类型的美食？比如中餐、西餐、日料等。"),
    ],
    "instore": [
        ("场景人数", "大概是几个人一起呢？有包间或其他要求吗？"),
        ("餐厅类型", "您更想吃什么类型的呢？火锅、川菜、西餐或其他？"),
        ("预算", "这顿饭大概的预算范围是多少呢？"),
    ],
    "ota": [
        ("出行方式", "这趟出行您是倾向飞机还是高铁呢？"),
        ("住宿类型", "您住宿有特别要求吗？比如大床房、亲子房，或者特定品牌酒店？"),
        ("预算", "大概的预算范围是多少呢？"),
    ],
}

# Missing-dimension detectors: given an instruction, return the dimension that
# is *missing* (None if the instruction already specifies it).
TRANSPORT_KEYWORDS = ("高铁", "飞机", "动车", "火车", "机票", "航班", "经济舱", "高铁票", "火车票")

# Keywords indicating instore group/private dining
GROUP_DINING_KEYWORDS = ("聚餐", "包间", "订座", "预约", "几个人", "朋友", "家人", "同事", "请客", "宴请")
GROUP_SPEC_KEYWORDS = ("个人", "位", "人吃饭", "人的包间", "人桌", "包厢")

# Keywords indicating hotel/accommodation request
HOTEL_KEYWORDS = ("酒店", "住宿", "宾馆", "旅馆", "民宿", "房间", "入住", "预订房")
HOTEL_TYPE_KEYWORDS = ("大床", "双床", "标准间", "套房", "亲子", "豪华", "商务")

# Time-of-day / time-anchor keywords for the "missing time" gap. The hidden
# intent of many proactive subtasks is a specific time (e.g. "下午三点喝"),
# which the rubric then checks ("送达时间 15点左右").
TIME_OF_DAY_KEYWORDS = ("下午", "晚上", "中午", "上午", "早上", "凌晨", "傍晚", "几点", "点半", "点整")
TIME_ANCHOR_KEYWORDS = ("明天", "今天", "后天", "号", "周末", "下周", "下个月", "这周", "今晚", "明晚")

# Caffeine signal words (user wants a functional effect, so strength matters).
CAFFEINE_SIGNAL_KEYWORDS = ("提神", "开会", "加班", "熬夜", "检查", "犯困", "困", "备考", "赶")
CAFFEINE_LEVEL_KEYWORDS = ("高咖啡因", "低咖啡因", "浓", "淡", "脱因", "无咖啡因", "双份浓缩")

# Substrings containing 票 that are NOT travel tickets (男票/女票 = BF/GF slang,
# 发票/股票/彩票/传票). Guards the bare "票" OTA marker against romance slang.
_TICKET_GUARD: tuple = ("男票", "女票", "发票", "股票", "彩票", "传票")


class ProactiveEngine:
    """Detect information gaps and decide whether/what to ask."""

    def __init__(self, max_questions: int = 2) -> None:
        self.max_questions = max_questions
        self.asked_this_subtask: int = 0
        self.asked_questions: set[str] = set()
        self.pending_question: Optional[str] = None

    def reset_subtask(self) -> None:
        self.asked_this_subtask = 0
        self.asked_questions.clear()
        self.pending_question = None

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
        return any(k in low for k in ("口味", "喜欢", "爱吃", "偏好", "餐", "店", "常用商家"))

    # -- missing-dimension detectors ---------------------------------------

    def _missing_transport(self, instruction: str) -> bool:
        """OTA: instruction clearly involves buying travel tickets but does not
        specify the transport mode (high-speed rail vs plane vs car).

        Requires a STRONG travel-buying signal — just saying "去" (go) is not
        enough (e.g. "去聚餐"). The presence of 票/订票/出行/机票 strongly
        implies travel ticketing.
        """
        travel_buying = any(k in instruction for k in ("票", "机票", "订票", "出行", "车票")) \
            and not any(g in instruction for g in _TICKET_GUARD)
        specifies_transport = any(k in instruction for k in TRANSPORT_KEYWORDS)
        return travel_buying and not specifies_transport

    def _missing_hotel_type(self, instruction: str) -> bool:
        """OTA: instruction involves booking a hotel but no room type specified."""
        has_hotel = any(k in instruction for k in HOTEL_KEYWORDS)
        specifies_type = any(k in instruction for k in HOTEL_TYPE_KEYWORDS)
        return has_hotel and not specifies_type

    def _missing_group_size(self, instruction: str) -> bool:
        """Instore: group/private dining context but headcount not specified."""
        has_group = any(k in instruction for k in GROUP_DINING_KEYWORDS)
        specifies_size = any(k in instruction for k in GROUP_SPEC_KEYWORDS)
        return has_group and not specifies_size

    def _missing_taste(self, instruction: str) -> bool:
        """delivery/instore: vague about what to eat."""
        if any(k in instruction for k in ("吃", "餐", "饭", "菜", "外卖", "店")):
            # If no explicit taste/cuisine is given, it's a gap.
            return not any(k in instruction for k in ("辣", "清淡", "火锅", "烧烤", "川菜", "粤菜", "西餐", "类型", "日料", "面", "米粉", "烤", "炸"))
        return False

    def _missing_time(self, instruction: str) -> bool:
        """Instruction has a concrete time anchor (明天/今天/N号) and a time-sensitive
        action, but no time-of-day. The hidden intent often carries a specific time
        (e.g. "下午三点喝") that the rubric then checks.

        Excludes transport bookings (机票/高铁/航班) — there the date anchor is the
        travel date, not a time-of-day the user needs to pin down upfront.
        """
        has_anchor = any(k in instruction for k in TIME_ANCHOR_KEYWORDS)
        time_sensitive = any(k in instruction for k in
                             ("点", "订", "买", "推荐", "送", "约", "下单", "外卖", "咖啡", "奶茶", "餐", "玩", "吃"))
        specifies_time = any(k in instruction for k in TIME_OF_DAY_KEYWORDS)
        is_transport = any(k in instruction for k in TRANSPORT_KEYWORDS)
        return has_anchor and time_sensitive and not specifies_time and not is_transport

    def _missing_caffeine(self, instruction: str) -> bool:
        """Coffee + a functional signal (提神/开会/加班) but no caffeine level.
        The rubric often checks high vs low caffeine."""
        has_coffee = "咖啡" in instruction
        has_signal = any(k in instruction for k in CAFFEINE_SIGNAL_KEYWORDS)
        specifies_level = any(k in instruction for k in CAFFEINE_LEVEL_KEYWORDS)
        return has_coffee and has_signal and not specifies_level

    def _personalized_question(self, domain: Optional[str], memory_text: str,
                                dimension_idx: int = 0) -> str:
        """Return a question, optionally personalized with existing memory context.

        If memory already contains a preference for the asked dimension, frame it as
        a confirmation question instead of an open-ended ask.
        """
        domain = domain or "delivery"
        base_q = DOMAIN_QUESTIONS[domain][dimension_idx][1]

        # Check if memory has a relevant past value — confirm rather than ask cold
        if memory_text and memory_text != "No user preference information available yet.":
            if domain == "ota" and dimension_idx == 0:
                # Check if we know a transport preference
                for kw in ("高铁", "飞机", "动车"):
                    if kw in memory_text:
                        return f"您之前偏好{kw}出行，这次也一样吗？"
            elif domain in ("delivery", "instore") and dimension_idx == 0:
                # Check if we know a taste preference
                for kw in ("麻辣", "清淡", "川菜", "粤菜", "火锅", "烧烤"):
                    if kw in memory_text:
                        return f"您平时喜欢{kw}，这次有什么特别的口味要求吗？"

        return base_q

    def propose_question(
        self,
        instruction: str,
        memory_text: str,
        domain: Optional[str],
        known_slots: Optional[dict[str, str]] = None,
    ) -> Optional[str]:
        """Purely propose a question, or return None if no gap/budget exists.

        This method never consumes budget. Only :meth:`commit_question` records
        that the agent actually sent a question to the user.

        Precedence:
        1. Missing critical decision dimension (transport) -> ask, regardless of
           the domain classifier (which may be None for short/terse queries).
        2. Instore group dining without headcount -> ask.
        3. Missing hotel type in OTA accommodation request -> ask.
        4. Vague instruction + domain not covered by memory -> ask.
        5. Missing taste type in food tasks -> ask.
        """
        if self.asked_this_subtask >= self.max_questions:
            return None
        known = known_slots or {}

        # Pattern 1: missing transport mode — the highest-value proactive case.
        # Checked independently of domain classification.
        if self._missing_transport(instruction) and "transport" not in known:
            return self._personalized_question("ota", memory_text, 0)

        domain = domain or "delivery"

        # Pattern 2: coffee + functional signal but no caffeine level.
        if self._missing_caffeine(instruction) and "caffeine" not in known:
            return "您需要高咖啡因还是低咖啡因的咖啡？"

        # Pattern 3: time-anchored action but no time-of-day.
        if self._missing_time(instruction) and "time" not in known:
            return "您希望什么时间呢？比如下午三点、中午等，我好按时间安排。"

        # Pattern 4: instore group dining without headcount.
        if domain == "instore" and self._missing_group_size(instruction) and "party_size" not in known:
            return DOMAIN_QUESTIONS["instore"][0][1]

        # Pattern 5: hotel booking without room type.
        if domain == "ota" and self._missing_hotel_type(instruction) and "room_type" not in known:
            return self._personalized_question("ota", memory_text, 1)

        # Pattern 6: vague + memory doesn't cover the domain.
        if self.is_vague(instruction) and not self._domain_covered(memory_text, domain):
            return self._personalized_question(domain, memory_text, 0)

        # Pattern 7: missing taste type in food tasks.
        if domain in ("delivery", "instore") and self._missing_taste(instruction) and "taste" not in known:
            return self._personalized_question(domain, memory_text, 0)

        return None

    def decide_to_ask(self, instruction: str, memory_text: str, domain: Optional[str]) -> Optional[str]:
        """Backward-compatible pure alias for :meth:`propose_question`."""
        return self.propose_question(instruction, memory_text, domain)

    def commit_question(self, question: str) -> bool:
        """Record a question only when it was actually sent to the user."""
        normalized = (question or "").strip()
        if not normalized or normalized in self.asked_questions:
            return False
        if self.asked_this_subtask >= self.max_questions:
            return False
        self.asked_questions.add(normalized)
        self.asked_this_subtask += 1
        self.pending_question = normalized
        return True

    def record_answer(self, question: str, answer: str, memory) -> None:
        """Store a confirmed preference from a user answer back into memory."""
        from agent.memory.signals import Signal

        sig = Signal(
            predicate="explicit_preference",
            object=answer.strip()[:80],
            confidence=0.95,
            timestamp="",
            type="conversation",
            raw=f"主动询问: {question}; 用户回答: {answer}",
            importance=8.0,
        )
        memory.stream.add(sig)
        self.pending_question = None
