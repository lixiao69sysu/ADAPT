"""Typed task compilation, candidate observations, ranking, and validation.

Holds the *shared* data structures: TaskSpec, DecisionCard, Constraint and
build_decision_card. The retired controller's CandidateLedger was split out into
``agent/candidate_ledger.py`` (it has no production call site) so that this
module's import closure stays free of the ledger and the runtime rankers, and so
the forced-ordering / leader / gate logic cannot drift back into the structures
the data layer depends on.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agent.intent import is_transaction_request
from agent.memory.facts import PreferenceFact, infer_facet


class ConstraintTarget(str, Enum):
    CANDIDATE = "candidate"
    ARGUMENT = "argument"
    WORKFLOW = "workflow"


class ConstraintOperator(str, Enum):
    CONTAINS = "contains"
    EQUALS = "equals"
    EXCLUDES = "excludes"
    RESOLVES_PROFILE = "resolves_profile"
    ALLOWS = "allows"


_DATE_RE = re.compile(
    r"\b20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\b|\d{1,2}月\d{1,2}日|\d{1,2}号"
)
# "先定30和31号的" asks for two nights. The single-day pattern above matched only
# the second one, so the card demanded the 31st and vetoed every attempt to book
# the 30th -- 103 identical rejections in one subtask, until the step budget was
# gone (E-052).
_DAY_LIST_RE = re.compile(r"(\d{1,2})\s*(?:和|、|及|与|,|，)\s*(\d{1,2})\s*号")
_EXACT_ENTITY_RE = re.compile(
    # A bare 就 is a selection marker only at the start of a clause ("就糯糯青山吧")
    # or after a selection verb. Mid-clause it is an everyday adverb ("猪瘾就犯了",
    # "就到了"), and capturing what followed it produced the requirement
    # ('犯了', hard) -- which then reached every consumer of card.must (E-074
    # measured the damage on the candidate-correspondence view). The discriminator
    # is structural (clause position), not a list of verbs, so a legitimate
    # selection is still captured.
    r"(?:就选|就要|就来|就订|就买|指定|要的是"
    r"|(?:^|(?<=[,，。!！?？、；;\s]))就)"
    r"([\u4e00-\u9fffA-Za-z0-9··・（）()_-]{2,24}?)(?:吧|[,，。!！?？]|$)"
)
_GENERIC_ENTITY_FRAGMENTS = (
    "吃外卖",
    "对付一下",
    "帮我",
    "送到",
    "送去",
    "安排",
    "把肌肉",
    "下单",
    "买个",
    "订个",
)
_ID_RE = re.compile(r"\b(?:S\d+_[A-Z]\d+|O[A-Z]?[A-Za-z0-9]+|B[A-Za-z0-9]{5,})\b")
_FIELD_RE = re.compile(r"([A-Za-z_]+)=([^,)\n]+)")
_ADDRESS_RE = re.compile(r"(?:送到|送去)([^，。；;!！?？]{2,40})")
# "到家来", "到单位来", "去公司": a registered place named with a direction
# verb instead of 送到. Without this the instruction carried no address
# contract at all and the write used whatever the model guessed (E-051).
_ALIAS_ADDRESS_RE = re.compile(
    r"(?:到|去|在)(家|家里|家中|我家|公司|单位|店里|公司前台|办公室|学校|宿舍)"
)
_ADDRESS_PARTICLES = "吧呀啊哦呢嘛了啦来去"
_ADDRESS_SUFFIXES = ("就行", "就成", "就好", "可以了", "谢谢", "麻烦")
# Structural evidence that a captured phrase is a real address rather than a
# registered place name.
_ADDRESS_STRUCTURE_MARKERS = (
    "路", "街", "道", "巷", "弄", "号", "栋", "幢", "座", "层", "室", "楼",
    "小区", "大厦", "广场", "公寓", "花园", "园区", "学校", "医院", "机场",
    "车站", "酒店", "宾馆",
)
_ADDRESS_ALIASES = {
    "家": "home",
    "家里": "home",
    "家中": "home",
    "我家": "home",
    "公司": "company",
    "单位": "company",
    "店里": "company",
    "公司前台": "company",
    "办公室": "company",
}
_PARTY_SIZE_RE = re.compile(r"([一二两三四五六七八九十\d]+)\s*(?:个)?人")
_ROUTE_RE = re.compile(
    r"(?:从)([^，。；;!！?？]{1,24}?)(?:到|去)([^，。；;!！?？]{1,24})"
)
_ATTRIBUTE_TERMS = (
    "少糖",
    "半糖",
    "无糖",
    "正常糖",
    "多冰",
    "少冰",
    "去冰",
    "常温",
    "热饮",
    "大床房",
    "双床房",
    "海景房",
    "城景房",
    "靠窗",
    "靠过道",
    "二等座",
    "一等座",
    "经济舱",
    "商务舱",
    "高铁",
    "飞机",
    "火车",
    "低咖啡因",
    "高咖啡因",
)
_PRODUCT_CATEGORIES = (
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
    # 饭 / 外卖 / 酒店 / 汤 were removed: they are not product categories and
    # produced spurious hard constraints on the most common instructions.
    # 饭 fired on 50/771 subtask definitions ("中午饭就帮我点个外卖"), 酒店 on 49
    # ("帮我定个酒店" -- the container, not a product), 外卖 on 15 (a delivery
    # channel), 汤 on 8 ("适合炖汤的食材" -- the request is the ingredients).
    # 汤锅/火锅 stay: those name a dish. Anything that legitimately refers to a
    # taste is handled by the `taste` slot's own markers.
    "机票",
    "高铁票",
    "火车票",
    "景点",
    "门票",
    "健身",
    "撸铁",
    "真人CS",
    "密室逃脱",
    "猫咖",
    "团购券",
    "套餐",
)
_TOPPING_VALUES = (
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
_TOPPING_NEGATIONS = ("不加小料", "无小料", "不要小料", "不放小料")
_DOMAIN_MARKERS = {
    "ota": (
        "酒店",
        "宾馆",
        "民宿",
        "客栈",
        "度假村",
        "机票",
        "航班",
        "火车",
        "高铁",
        "动车",
        "车票",
        "火车票",
        "高铁票",
        "航空",
        "门票",
        "景点",
        "打车",
        "住宿",
        "出行",
    ),
    "instore": (
        "探店",
        "到店",
        "预约",
        "预定",
        "包间",
        "足疗",
        "按摩",
        "电玩",
        "理发",
        "团个券",
        "团购券",
        "撸铁",
        "真人",
    ),
    "delivery": ("外卖", "配送", "送到", "送去", "下单", "闪购", "帮我点", "帮我买"),
}

# A bare 票 is a travel-ticket signal unless it is one of these: 男票/女票 are
# romance slang, and 发票/股票/彩票/传票 are unrelated nouns. Without this guard
# "帮我买去迪的票" routed to delivery/retail, because 帮我买 is a delivery marker
# and no ota marker matched -- so the ticket was never compiled as travel and the
# transport gap was never declared (E-041 recurrence, E-068).
_TICKET_FALSE_POSITIVES = ("男票", "女票", "发票", "股票", "彩票", "传票")


def _mentions_a_ticket(text: str) -> bool:
    stripped = text
    for guard in _TICKET_FALSE_POSITIVES:
        stripped = stripped.replace(guard, "")
    return "票" in stripped


@dataclass
class Constraint:
    kind: str
    value: str
    target: ConstraintTarget = ConstraintTarget.CANDIDATE
    operator: ConstraintOperator = ConstraintOperator.CONTAINS
    source: str = "instruction"
    hard: bool = True
    evidence_span: str = ""
    argument_name: str = ""
    attribute_key: str = ""
    """The typed slot this constraint is about (room_type/transport/taste/...).

    Carried so a downstream consumer can compare a candidate's printed value for
    the *same* slot. Without it a requirement can only ever be confirmed, never
    refuted, which is the bottleneck E-067 identified.
    """


@dataclass
class TaskSpec:
    instruction: str
    domain: str
    facet: str
    action: str
    must: list[Constraint] = field(default_factory=list)
    avoid: list[Constraint] = field(default_factory=list)
    required_slots: list[str] = field(default_factory=list)
    unknown_slots: list[str] = field(default_factory=list)
    resolved_slots: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def compile(cls, instruction: str) -> TaskSpec:
        text = (instruction or "").strip()
        domain = "delivery"
        if _mentions_a_ticket(text):
            # A ticket purchase is travel even when the phrasing also carries a
            # delivery verb ("帮我买…的票").
            domain = "ota"
        else:
            for candidate, markers in _DOMAIN_MARKERS.items():
                if any(marker in text for marker in markers):
                    domain = candidate
                    break
        facet = infer_facet(text, domain)
        action = "recommend"
        if is_transaction_request(text):
            action = "commit"
        elif any(k in text for k in ("取消", "改签", "修改")):
            action = "modify"

        must: list[Constraint] = []
        avoid: list[Constraint] = []
        for match in _EXACT_ENTITY_RE.finditer(text):
            value = match.group(1).strip()
            if not any(fragment in value for fragment in _GENERIC_ENTITY_FRAGMENTS):
                must.append(Constraint("entity", value, evidence_span=match.group(0)))
        for category in _PRODUCT_CATEGORIES:
            if category in text:
                must.append(Constraint("category", category, evidence_span=category))
                break
        for term in _ATTRIBUTE_TERMS:
            if term in text:
                must.append(Constraint("attribute", term, evidence_span=term))
        must.extend(_stated_slot_constraints(text, facet, must))
        if facet == "restaurant":
            party_match = _PARTY_SIZE_RE.search(text)
            if party_match:
                party_size = _normalize_chinese_count(party_match.group(1))
                must.append(
                    Constraint(
                        "party_size",
                        f"{party_size}人",
                        evidence_span=party_match.group(0),
                    )
                )
        # A day *list* is a per-night requirement: one candidate can only carry
        # one of the days, so the framework may state it but must not veto on it.
        day_list_days: list[str] = []
        masked = text
        for match in _DAY_LIST_RE.finditer(text):
            for day in (match.group(1), match.group(2)):
                if day not in day_list_days:
                    day_list_days.append(day)
            masked = masked.replace(match.group(0), " " * len(match.group(0)))
        for day in day_list_days:
            must.append(
                Constraint(
                    "date",
                    f"{int(day)}号",
                    ConstraintTarget.ARGUMENT,
                    ConstraintOperator.EQUALS,
                    hard=False,
                    evidence_span=f"{day_list_days[0]}和{day_list_days[-1]}号"
                    if len(day_list_days) > 1
                    else f"{day}号",
                    argument_name="date",
                )
            )
        for date in _DATE_RE.findall(masked):
            must.append(
                Constraint(
                    "date",
                    date,
                    ConstraintTarget.ARGUMENT,
                    ConstraintOperator.EQUALS,
                    evidence_span=date,
                    argument_name="date",
                )
            )
        address_match = _ADDRESS_RE.search(text) or _ALIAS_ADDRESS_RE.search(text)
        if address_match:
            raw_address = _strip_address_particles(address_match.group(1))
            alias = _address_alias(raw_address)
            must.append(
                Constraint(
                    "address",
                    alias,
                    ConstraintTarget.ARGUMENT,
                    ConstraintOperator.RESOLVES_PROFILE
                    if alias in {"home", "company"}
                    else ConstraintOperator.CONTAINS,
                    evidence_span=address_match.group(0),
                    argument_name="address",
                )
            )
        route_match = _ROUTE_RE.search(text)
        if route_match:
            departure = route_match.group(1).strip()
            destination = re.sub(
                r"的?(?:高铁|动车|火车|飞机|航班|车)?票?$",
                "",
                route_match.group(2).strip(),
            )
            must.extend(
                [
                    Constraint(
                        "departure",
                        departure,
                        ConstraintTarget.ARGUMENT,
                        ConstraintOperator.EQUALS,
                        evidence_span=route_match.group(0),
                        argument_name="departure",
                    ),
                    Constraint(
                        "destination",
                        destination,
                        ConstraintTarget.ARGUMENT,
                        ConstraintOperator.EQUALS,
                        evidence_span=route_match.group(0),
                        argument_name="destination",
                    ),
                ]
            )
        if action == "commit":
            must.append(
                Constraint(
                    "authorization",
                    "create",
                    ConstraintTarget.WORKFLOW,
                    ConstraintOperator.ALLOWS,
                    evidence_span=text,
                )
            )

        for marker in ("不要", "不吃", "不喜欢", "不能", "避免", "忌口"):
            start = text.find(marker)
            if start >= 0:
                value = re.split(
                    r"[,，。；;!！?？]", text[start + len(marker) :], maxsplit=1
                )[0].strip()
                if value:
                    avoid.append(
                        Constraint(
                            "negative",
                            value[:30],
                            ConstraintTarget.CANDIDATE,
                            ConstraintOperator.EXCLUDES,
                            evidence_span=text[
                                start : start + len(marker) + len(value)
                            ],
                        )
                    )

        required = _required_slots(domain, facet, action, text)
        unknown = [slot for slot in required if not _slot_is_present(slot, text)]
        resolved = {}
        if any(c.kind == "address" for c in must):
            resolved["address"] = next(c.value for c in must if c.kind == "address")
        caffeine_preference = _infer_caffeine_preference(text)
        if caffeine_preference:
            resolved["caffeine"] = caffeine_preference
        return cls(
            text,
            domain,
            facet,
            action,
            _dedup_constraints(must),
            avoid,
            required,
            unknown,
            resolved,
        )


@dataclass
class DecisionCard:
    must: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    prefer: list[str] = field(default_factory=list)
    ask: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)
    # Open-world alignment sources are deliberately not rendered directly.
    # ``task_intent`` is the current user's request; ``preference_pool`` is a
    # bounded set of this user's active positive facts. After SEARCH, the
    # candidate ledger induces the actual attribute vocabulary and only
    # grounded atoms enter ranking/evidence. This avoids deciding up front
    # that a preference must be a brand, taste, room type, etc.
    task_intent: list[str] = field(default_factory=list)
    preference_pool: list[str] = field(default_factory=list)
    preference_weights: dict[str, float] = field(default_factory=dict)
    preference_decisive: dict[str, bool] = field(default_factory=dict)
    preference_source_types: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def alignment_preferences(self) -> list[str]:
        """Sources eligible for candidate-induced, task-local grounding."""
        return _dedup([*self.task_intent, *self.prefer, *self.preference_pool])

    def alignment_source_records(
        self,
    ) -> list[tuple[str, float, bool, bool, tuple[str, ...]]]:
        """Return text, strength, fallback, decisiveness, and evidence types."""
        records: list[tuple[str, float, bool, bool, tuple[str, ...]]] = []
        records.extend(
            (value, 3.0, False, True, ("current_instruction",))
            for value in self.task_intent
        )
        records.extend(
            (
                value,
                max(2.0, self.preference_weights.get(value, 0.0)),
                True,
                self.preference_decisive.get(value, True),
                self.preference_source_types.get(value, ("decision_card",)),
            )
            for value in self.prefer
        )
        records.extend(
            (
                value,
                max(0.1, self.preference_weights.get(value, 0.1)),
                False,
                self.preference_decisive.get(value, False),
                self.preference_source_types.get(value, ("memory",)),
            )
            for value in self.preference_pool
        )
        return records

    def render(self, max_chars: int = 1200, max_facts: int = 8) -> str:
        """Render the card, budgeting by priority rather than by list position.

        MUST and AVOID are the conditions the current instruction is graded on.
        A constraint that is dropped here is dropped before the model ever sees
        it, and no later reasoning or review step can recover it (E-060). So the
        hard sections render in full and ``max_facts`` bounds only the soft
        material (PREFER / ASK / EVIDENCE).

        ``max_chars`` remains the final ceiling. It is the only place a hard
        constraint may still be cut, and only when the hard sections alone
        exceed it.
        """
        must = _dedup(self.must)
        avoid = _dedup(self.avoid)

        hard_facts = len(must) + len(avoid)
        soft_budget = max(0, max_facts - hard_facts)
        ask_reserve = 1 if self.ask and soft_budget else 0
        prefer = _dedup(self.prefer)[: max(0, soft_budget - ask_reserve)]
        soft_remaining = soft_budget - len(prefer)
        ask = _dedup(self.ask)[: min(1, soft_remaining)]
        soft_remaining -= len(ask)
        evidence = _dedup(self.evidence)[: min(1, soft_remaining)]

        ordered = (
            ("AVOID", avoid, True),
            ("MUST", must, True),
            ("PREFER", prefer, False),
            ("ASK", ask, False),
            ("EVIDENCE", evidence, False),
        )
        sections = [
            f"{title}: " + " | ".join(values)
            for title, values, _hard in ordered
            if values
        ]
        if not sections:
            return "MUST: follow the current instruction"

        # Soft sections are appended whole or not at all; a partially appended
        # section would silently truncate its own last entry, which is the
        # positional-cut failure this method exists to avoid. A section that
        # does not fit is skipped so that a shorter, lower-priority section can
        # still use the remaining budget.
        hard_count = sum(1 for _t, values, hard in ordered if hard and values)
        rendered = "\n".join(sections[:hard_count])
        for section in sections[hard_count:]:
            candidate = f"{rendered}\n{section}"
            if len(candidate) > max_chars:
                continue
            rendered = candidate
        return rendered[:max_chars]


def build_decision_card(
    spec: TaskSpec, facts: Iterable[PreferenceFact]
) -> DecisionCard:
    facts = list(facts)
    candidate_must = [
        c.value for c in spec.must if c.target == ConstraintTarget.CANDIDATE
    ]
    card = DecisionCard(
        must=candidate_must
        + [
            f"{c.kind}={c.value}"
            for c in spec.must
            if c.target != ConstraintTarget.CANDIDATE
        ],
        avoid=[c.value for c in spec.avoid],
        # Functional intent in the current instruction outranks remembered
        # soft preferences, but remains a preference rather than a hard
        # candidate constraint. This lets ranking understand requests such as
        # "coffee for staying alert" without making inference write-blocking.
        prefer=_instruction_preferences(spec),
        constraints=[*spec.must, *spec.avoid],
        task_intent=[spec.instruction] if spec.instruction else [],
    )
    food_facets = {"restaurant", "beverage"}
    local_scopes = {"delivery", "instore", "local_commerce"}
    transferable_general = {
        "hotel": {"budget", "room_type"},
        "train": {"transport", "seat", "budget"},
        "flight": {"transport", "seat", "budget"},
        "restaurant": {
            "safety"
        },
        "beverage": {
            "safety"
        },
        "retail": {"size", "color", "budget", "attribute"},
    }
    broad_local_task = (
        spec.domain in {"delivery", "instore"}
        and any(marker in spec.instruction for marker in ("外卖", "吃点", "点个吃的"))
        and not any(c.kind in {"entity", "attribute"} for c in spec.must)
    )
    latest_topping_avoidance = max(
        (
            fact.observed_at
            for fact in facts
            if fact.status == "active"
            and fact.polarity == "negative"
            and "小料" in fact.value
            and fact.observed_at
        ),
        default="",
    )
    relevant = []
    current_categories = {
        constraint.value for constraint in spec.must if constraint.kind == "category"
    }
    for fact in facts:
        if fact.status != "active":
            continue
        if (
            latest_topping_avoidance
            and fact.polarity == "positive"
            and any(topping in fact.value for topping in _TOPPING_VALUES)
            and not any(negation in fact.value for negation in _TOPPING_NEGATIONS)
            and fact.observed_at
            and fact.observed_at <= latest_topping_avoidance
        ):
            # A later broad topping rejection supersedes older specific
            # topping likes. A newer concrete topping preference remains as an
            # explicit exception (e.g. the later-discovered preference for
            # brulee).
            continue
        scope_match = fact.scope == spec.domain or (
            spec.domain in local_scopes and fact.scope in local_scopes
        )
        category_match = (
            spec.facet not in food_facets
            or fact.dimension == "safety"
            or not current_categories
            or fact.category in {"general", spec.facet, *current_categories}
            or (
                fact.category in {"火锅", "汤锅"}
                and bool(current_categories & {"火锅", "汤锅"})
            )
        )
        exact = scope_match and fact.facet == spec.facet and category_match
        general_transfer = (
            fact.scope == "general"
            and fact.facet == "general"
            and fact.dimension in transferable_general.get(spec.facet, set())
        )
        broad_local = broad_local_task and (
            fact.scope in local_scopes
            or (
                fact.scope == "general"
                and fact.dimension
                in {"brand", "product", "like", "taste", "avoid", "attribute"}
            )
        )
        directly_named = scope_match and fact.value and fact.value in spec.instruction
        food_transfer = (
            spec.facet in food_facets
            and fact.facet in food_facets
            and fact.dimension == "safety"
        )
        if exact or general_transfer or broad_local or directly_named or food_transfer:
            relevant.append(fact)
    dimension_priority = {
        "explicit": 6,
        "conditional": 6,
        "product": 5,
        "like": 5,
        "taste": 4,
        "temperature": 4,
        "sweetness": 4,
        "topping": 4,
        "attribute": 4,
        "location": 4,
        "room_type": 4,
        "brand": 3,
        "budget": 3,
    }
    relevant.sort(
        key=lambda fact: (
            dimension_priority.get(fact.dimension, 1),
            fact.confidence,
            fact.observed_at,
        ),
        reverse=True,
    )
    # Fixed facet inference is useful for the compact prompt card, but it must
    # not discard a fact before the current candidate schema is known. Facts
    # that do not ground to observed candidate attributes remain inert.
    pool_facts = sorted(
        (
            fact
            for fact in facts
            if fact.status == "active"
            and fact.polarity != "negative"
            and _valid_fact_value(fact.value)
        ),
        key=lambda fact: (fact.confidence, fact.observed_at),
        reverse=True,
    )
    card.preference_pool = _dedup([fact.value for fact in pool_facts])[:64]
    for fact in pool_facts:
        card.preference_weights[fact.value] = max(
            fact.confidence,
            card.preference_weights.get(fact.value, 0.0),
        )
        card.preference_decisive[fact.value] = (
            card.preference_decisive.get(fact.value, False)
            or fact.decision_eligible
        )
        card.preference_source_types[fact.value] = tuple(
            _dedup(
                [
                    *card.preference_source_types.get(fact.value, ()),
                    *(fact.evidence_types or [fact.source_type]),
                ]
            )
        )
    resolved_conditionals: list[str] = []
    for fact in relevant:
        if not _valid_fact_value(fact.value):
            continue
        evidence = (
            f"{fact.source_type}@{fact.observed_at}"
            if fact.observed_at
            else fact.source_type
        )
        if fact.polarity == "negative":
            card.avoid.append(fact.value)
            card.constraints.append(
                Constraint(
                    fact.dimension,
                    fact.value,
                    ConstraintTarget.CANDIDATE,
                    ConstraintOperator.EXCLUDES,
                    source="memory",
                    hard=fact.dimension in {"avoid", "safety"},
                    evidence_span=fact.value,
                )
            )
        else:
            if not fact.decision_eligible:
                # Weak behavioral interests remain available to post-search
                # candidate grounding, but are not promoted into the compact
                # prompt as if the user had stated a durable preference.
                continue
            if fact.dimension == "conditional":
                resolved = _resolve_conditional_preference(
                    fact.value, spec.instruction
                )
                if not resolved:
                    continue
                resolved_conditionals.append(resolved)
                card.prefer.append(resolved)
            else:
                card.prefer.append(fact.value)
        card.evidence.append(f"{fact.value} <- {evidence}")
    if resolved_conditionals:
        chosen_families = {
            family
            for value in resolved_conditionals
            if (family := _taste_family(value))
        }
        card.prefer[:] = _dedup(
            [
                *resolved_conditionals,
                *(
                    value
                    for value in card.prefer
                    if not _taste_family(value)
                    or _taste_family(value) in chosen_families
                ),
            ]
        )
    noncritical = {"address", "product", "shop_or_service", "city"}
    card.ask.extend(
        slot
        for slot in spec.unknown_slots
        if slot not in noncritical and slot not in spec.resolved_slots
    )
    card.ask[:] = card.ask[:2]
    return card


def _resolve_conditional_preference(value: str, instruction: str) -> str:
    if "=>" not in value:
        return ""
    condition, choice = value.split("=>", 1)
    text = instruction or ""
    party_match = _PARTY_SIZE_RE.search(text)
    party_size = int(_normalize_chinese_count(party_match.group(1))) if party_match else 0
    if condition == "party>=4" and (
        party_size >= 4 or any(marker in text for marker in ("聚餐", "多人", "聚会"))
    ):
        return choice
    if condition == "party<=2" and (
        0 < party_size <= 2
        or any(marker in text for marker in ("一个人", "两个人", "双人", "人少"))
    ):
        return choice
    return ""


def _taste_family(value: str) -> str:
    for canonical, markers in (
        ("麻辣", ("麻辣", "牛油", "红油", "辣锅")),
        ("菌汤", ("菌汤", "菌菇", "竹荪")),
        ("番茄", ("番茄",)),
        ("清汤", ("清汤", "清淡", "养生")),
    ):
        if any(marker in value for marker in markers):
            return canonical
    return ""


def _instruction_preferences(spec: TaskSpec) -> list[str]:
    """Translate visible functional intent into task-local soft preferences."""
    preference = _infer_caffeine_preference(spec.instruction or "")
    return [preference] if spec.facet == "beverage" and preference else []


def _infer_caffeine_preference(text: str) -> str:
    """Infer caffeine only from an explicit level or unambiguous time of day."""
    if any(marker in text for marker in ("低咖啡因", "脱因", "下午", "晚上", "晚间")):
        return "低咖啡因"
    if any(marker in text for marker in ("高咖啡因", "上午", "早上", "早晨")):
        return "高咖啡因"
    return ""


@dataclass
class Candidate:
    candidate_id: str
    entity_type: str
    name: str
    raw: str
    tool_name: str
    attributes: dict[str, str] = field(default_factory=dict)
    parent_ids: list[str] = field(default_factory=list)
    price: float | None = None
    inventory: int | None = None
    observed_turn: int = 0




def is_search_tool(name: str) -> bool:
    lowered = name.lower()
    return "search" in lowered or "recommend" in lowered or "recommand" in lowered


def is_commit_tool(name: str) -> bool:
    return name.startswith(("create_", "pay_")) or name in {
        "instore_book",
        "instore_reservation",
    }


def _is_commit_tool(name: str) -> bool:
    return is_commit_tool(name)


def _strip_address_particles(value: str) -> str:
    """Drop the sentence particles a captured address phrase picks up."""
    text = (value or "").strip()
    changed = True
    while changed:
        changed = False
        for suffix in _ADDRESS_SUFFIXES:
            if text.endswith(suffix) and len(text) > len(suffix):
                text = text[: -len(suffix)].strip()
                changed = True
        stripped = text.rstrip(_ADDRESS_PARTICLES).strip()
        if stripped != text:
            text = stripped
            changed = True
    return text


def _address_alias(value: str) -> str:
    """Resolve a captured address phrase to a profile alias when it is one.

    ``_ADDRESS_RE`` captures everything after 送到, so "送到单位来吧" arrives as
    "单位来". Keeping that as a literal address made the framework demand the
    model write "单位来" verbatim: the environment cannot geocode it, the write
    failed, the tool-failure guard blocked the retry and the subtask ended in
    the terminal refusal while the stock agent completed the order (E-051).
    """
    text = _strip_address_particles(value)
    if not text:
        return ""
    if text in _ADDRESS_ALIASES:
        return _ADDRESS_ALIASES[text]
    if any(marker in text for marker in _ADDRESS_STRUCTURE_MARKERS):
        return text
    for token, alias in _ADDRESS_ALIASES.items():
        if len(token) >= 2 and text.startswith(token) and len(text) - len(token) <= 3:
            return alias
    return text


def _required_slots(domain: str, facet: str, action: str, text: str) -> list[str]:
    # Some gaps change which candidate should be *recommended*, not merely which
    # one gets written. Those are required even when the action is only
    # "recommend", or the proactive engine loses a case that used to work.
    recommendation_slots: list[str] = []
    if (
        domain == "delivery"
        and facet == "beverage"
        and "咖啡" in text
        and any(marker in text for marker in ("提神", "犯困", "熬夜", "加班", "开会"))
    ):
        recommendation_slots.append("caffeine")
    if action != "commit":
        return recommendation_slots
    if domain == "delivery":
        slots = ["product", "address"]
        if "拖鞋" in text or "鞋" in text:
            slots.append("size")
        if facet == "restaurant":
            # A meal request that names no cuisine leaves a gap only the user can
            # close, and it changes which candidate is right. Declaring it here
            # replaces the proactive engine's vagueness heuristic.
            slots.append("taste")
        slots.extend(recommendation_slots)
        return slots
    if facet in {"train", "flight"}:
        return ["departure", "destination", "date", "quantity"]
    if facet == "travel":
        # A ticket request that names no mode ("帮我买张出行票"). Declaring the
        # gap here replaces the keyword probe that used to live in the proactive
        # engine: the compiler already knows the request is a travel purchase,
        # so the missing mode belongs in the spec, not in a topic word list.
        return ["transport", "destination", "date"]
    if facet == "hotel":
        return ["city", "date", "room_type"]
    if domain == "instore":
        return ["shop_or_service", "time"]
    if domain == "ota" and facet == "attraction":
        return ["city", "date", "quantity"]
    return []


def _slot_is_present(slot: str, text: str) -> bool:
    markers = {
        "product": _PRODUCT_CATEGORIES,
        "address": ("送到", "送去", "家", "公司", "单位", "学校", "店里"),
        "size": ("码", "尺码", "42-43", "44-45"),
        "departure": ("从", "出发", "离开"),
        "destination": ("去", "到"),
        "date": ("今天", "明天", "后天", "下周", "周", "号", "日"),
        "quantity": ("一张", "两张", "一人", "两人", "个人", "位", "我们"),
        "city": ("去", "在", "市", "区", "到"),
        # "酒店" and "房" are the *container*, not the room type. Listing them
        # here made "帮我订个酒店" report room_type as already stated, so the
        # gap that actually needs asking was marked filled (E-068).
        "room_type": ("大床", "双床", "标准间", "套房", "亲子房", "海景房", "城景房"),
        "transport": (
            "高铁",
            "飞机",
            "动车",
            "火车",
            "机票",
            "航班",
            "经济舱",
            "商务舱",
            "高铁票",
            "火车票",
            "动车票",
        ),
        "taste": (
            "辣",
            "清淡",
            "火锅",
            "烧烤",
            "川菜",
            "粤菜",
            "西餐",
            "日料",
            "面",
            "米粉",
            "烤",
            "炸",
            "甜",
            "酸",
            "汤",
            "口味",
            "菜系",
            # Named dishes and cuisines. A lexicon, and therefore a known
            # boundary: an unlisted dish ("帮我点个鲅鱼饺子") still reads as an
            # open taste gap. It is here rather than in the question policy
            # because it decides whether a *gap exists*, which is schema work,
            # not question selection (E-068 recurrence, caught by the smoke
            # target listing).
            "刺身",
            "海鲜",
            "自助",
            "蛋糕",
            "果酱",
            "巧克力",
            "甜品",
            "糖水",
            "小吃",
            "米线",
            "麻辣烫",
            "饺子",
            "包子",
            "粥",
            "饼",
            "粉",
            "菜",
            "云南菜",
            "贵州菜",
            "东北菜",
            "湘菜",
            "徽菜",
            "本帮菜",
        ),
        "shop_or_service": (
            "店",
            "餐厅",
            "足疗",
            "电玩",
            "理发",
            "套餐",
            "健身",
            "撸铁",
            "券",
        ),
        "time": ("今天", "明天", "后天", "周", "点", "上午", "下午", "晚上"),
        "caffeine": ("高咖啡因", "低咖啡因", "脱因", "上午", "早上", "早晨", "下午", "晚上", "晚间"),
    }
    return any(marker in text for marker in markers.get(slot, ()))


def _stated_slot_constraints(
    text: str, facet: str, existing: list[Constraint]
) -> list[Constraint]:
    """Typed constraints for slot values the instruction states but misses.

    The instruction's stated conditions are only captured when they appear in
    ``_ATTRIBUTE_TERMS`` verbatim, so "要大床的" produced no ``room_type``
    requirement at all and "机票" no ``transport`` one -- E-078 measured 24 terms
    of which only 6 ever fire. Meanwhile the memory layer already owns a
    marker->canonical map (``slots._CANONICAL_MARKERS``: 大床->大床房,
    动车->高铁, ...). Reusing it here raises recall without adding any new word
    list, which is the direction the user asked for.

    Scoped by facet, because a bare mention is often not a requirement: the
    instruction "机票已经买好了，你帮我订个酒店吧" names a *past* purchase, so a
    transport requirement there would be a false positive.

    The produced constraint carries ``attribute_key``, which is what lets a
    downstream comparison refute a candidate instead of only confirming it
    (E-067).
    """
    from agent.memory.slots import _CANONICAL_MARKERS

    relevant = {
        "transport": {"travel", "train", "flight"},
        "room_type": {"hotel"},
        "taste": {"restaurant", "beverage"},
        "caffeine": {"beverage"},
        "budget": {"hotel", "train", "flight", "attraction", "retail", "restaurant", "beverage"},
        "size": {"retail"},
    }
    captured_values = {c.value for c in existing}
    captured_keys = {
        getattr(c, "attribute_key", "") for c in existing if c.kind == "attribute"
    }
    produced: list[Constraint] = []
    for slot, markers in _CANONICAL_MARKERS.items():
        if facet and facet not in relevant.get(slot, set()):
            continue
        canonical = ""
        for candidate_value, keys in markers:
            if any(key in text for key in keys):
                canonical = candidate_value
                break
        if not canonical:
            continue
        # Suppress only when this slot already has a constraint, or the exact
        # value is present. A substring test is wrong in both directions here:
        # the category value 咖啡 is a substring of the canonical 高咖啡因, so it
        # silently killed a legitimate caffeine requirement.
        if slot in captured_keys or canonical in captured_values:
            continue
        produced.append(
            Constraint(
                "attribute",
                canonical,
                evidence_span=canonical,
                attribute_key=slot,
            )
        )
    return produced


def _validate_argument_constraint(
    constraint: Constraint,
    arguments: dict[str, Any],
    profile: dict[str, Any],
    *,
    selected_text: str = "",
) -> list[str]:
    aliases = {
        "date": (
            "date",
            "booking_date",
            "check_in_date",
            "departure_date",
            "dispatch_time",
            "time",
        ),
        "address": ("address", "location", "destination", "delivery_address"),
        "departure": ("departure", "departure_city", "from_city", "start_city"),
        "destination": ("destination", "arrival_city", "to_city", "end_city"),
    }
    values = [
        str(arguments.get(key, ""))
        for key in aliases.get(constraint.kind, (constraint.argument_name,))
        if key
    ]
    joined = " ".join(value for value in values if value)
    if constraint.operator == ConstraintOperator.RESOLVES_PROFILE:
        expected = _profile_address(profile, constraint.value)
        if not joined:
            return [
                f"missing {constraint.kind} argument for profile alias {constraint.value}"
            ]
        if expected and expected not in joined and joined not in expected:
            return [
                f"{constraint.kind} does not resolve to the user's {constraint.value} address"
            ]
        return []

    # Some tool schemas bind a task constraint to the selected candidate
    # rather than repeating it as a WRITE argument. VitaBench hotel CREATE,
    # for example, accepts hotel_id/room_id/user_id; the stay date is an
    # observed field of room_id. Keep this narrow and evidence-based: only an
    # explicit candidate ``date=...`` field may satisfy a missing date
    # argument, and an explicit WRITE date must agree with that candidate.
    candidate_values = _candidate_bound_values(constraint.kind, selected_text)
    if not joined:
        if candidate_values and _constraint_present(
            constraint.value, " ".join(candidate_values)
        ):
            return []
        if candidate_values:
            return [
                f"selected candidate does not satisfy required {constraint.kind}: "
                f"{constraint.value}"
            ]
        return [
            f"{constraint.kind} argument does not satisfy required value: {constraint.value}"
        ]
    if not _constraint_present(constraint.value, joined):
        return [
            f"{constraint.kind} argument does not satisfy required value: {constraint.value}"
        ]
    if candidate_values and not _constraint_present(
        constraint.value, " ".join(candidate_values)
    ):
        return [
            f"selected candidate conflicts with required {constraint.kind}: "
            f"{constraint.value}"
        ]
    return []


def _candidate_bound_values(kind: str, selected_text: str) -> list[str]:
    """Return explicit candidate fields allowed to satisfy argument constraints."""
    if kind != "date" or not selected_text:
        return []
    return _dedup(
        match.strip(" '\"")
        for match in re.findall(
            r"(?:^|[,\s(])date\s*[=:]\s*([^,)\]\s]+)", selected_text
        )
    )


def profile_address(profile: dict[str, Any], alias: str) -> str:
    """Public accessor for the user's registered address behind an alias."""
    return _profile_address(profile, alias)


def _profile_address(profile: dict[str, Any], alias: str) -> str:
    """Resolve the registered address behind a home/company alias.

    Street-level keys must win over place-level ones: a profile commonly holds
    both 常住地 (city) and 常住住址 (street address), and matching on the shared
    "常住" marker first returned the city. The environment cannot geocode a bare
    city, so a write filled with it fails, and the validator rejected correct
    street addresses for not containing the city (E-043).
    """
    markers = (
        ("家", "home", "住址", "地址", "常住住", "居住")
        if alias == "home"
        else ("公司", "单位", "工作", "company", "office")
    )
    place_only = ("常住地", "所在地", "城市", "city", "province", "省", "籍贯")
    street_markers = ("住址", "地址", "street", "detail")
    candidates: list[tuple[int, str]] = []
    for key, value in profile.items():
        text = str(key)
        lowered = text.lower()
        if not any(marker.lower() in lowered for marker in markers):
            continue
        if any(marker in text for marker in place_only):
            # 常住地/籍贯 describe a place, never a deliverable address.
            continue
        resolved = (
            str(value.get("address", "")) if isinstance(value, dict) else str(value)
        )
        if not resolved:
            continue
        score = 2 if any(marker in lowered for marker in street_markers) else 1
        candidates.append((score, resolved))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _normalize_search_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in arguments.items():
        if key in {"keywords", "key_words", "query"}:
            tokens = (
                value if isinstance(value, list) else re.split(r"[\s,，]+", str(value))
            )
            normalized[key] = sorted(
                {re.sub(r"\s+", "", str(token)).lower() for token in tokens if token}
            )[:4]
        else:
            normalized[key] = value
    return normalized


def _entity_type(candidate_id: str) -> str:
    if candidate_id.startswith("O"):
        return "order"
    match = re.search(r"_([A-Z])\d+$", candidate_id)
    code = match.group(1) if match else ""
    return {
        "P": "product",
        "S": "store",
        "I": "shop",
        "H": "hotel",
        "A": "attraction",
        "F": "flight",
        "T": "train",
    }.get(code, "unknown")


def _to_float(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _to_int(value: str | None) -> int | None:
    try:
        return int(float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _dedup(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _dedup_constraints(values: Iterable[Constraint]) -> list[Constraint]:
    seen: set[tuple] = set()
    result: list[Constraint] = []
    for value in values:
        key = (value.kind, value.value, value.target, value.operator)
        if value.value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _valid_fact_value(value: str) -> bool:
    text = (value or "").strip()
    if (
        not text
        or len(text) > 48
        or any(marker in text for marker in ("随便", "你看着办", "我不太清楚"))
    ):
        return False
    return not text.endswith(("真", "我在看", "还是", "帮我看看有没有"))


def _constraint_present(requirement: str, selected: str) -> bool:
    if requirement in {"汤锅", "火锅"}:
        return "汤锅" in selected or "火锅" in selected
    if requirement in selected:
        return True
    day_match = re.fullmatch(r"(\d{1,2})号", requirement)
    if day_match:
        day = int(day_match.group(1))
        return bool(re.search(rf"[-/.]0?{day}(?:\D|$)", selected))
    md_match = re.fullmatch(r"(\d{1,2})月(\d{1,2})日", requirement)
    if md_match:
        month, day = map(int, md_match.groups())
        return bool(re.search(rf"[-/.]0?{month}[-/.]0?{day}(?:\D|$)", selected))
    return False


def _contains_forbidden(selected: str, value: str) -> bool:
    """Match an exclusion without treating explicit negation as a violation."""
    if value not in selected:
        return False
    negated = (f"无{value}", f"不加{value}", f"不含{value}", f"去{value}")
    remainder = selected
    for phrase in negated:
        remainder = remainder.replace(phrase, "")
    return value in remainder


def _normalize_chinese_count(value: str) -> str:
    mapping = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    return str(mapping.get(value, value))


def _category_groundable_in_ledger(
    value: str, candidates: Iterable[Candidate]
) -> bool:
    from agent.runtime.ranking import category_is_groundable

    observable = [
        candidate
        for candidate in candidates
        if candidate.entity_type not in {"order", "unknown", "store"}
    ]
    return category_is_groundable(value, observable)


def _category_satisfied_by_tool(kind: str, value: str, tool_name: str) -> bool:
    """Use typed OTA create tools as evidence for their parent entity class."""
    if kind != "category":
        return False
    expected_marker = {
        "酒店": "hotel",
        "机票": "flight",
        "高铁票": "train",
        "火车票": "train",
        "景点": "attraction",
        "门票": "attraction",
    }.get(value)
    return bool(expected_marker and expected_marker in tool_name.lower())
