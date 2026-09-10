"""Typed task compilation, candidate observations, ranking, and validation."""

from __future__ import annotations

import json
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
_EXACT_ENTITY_RE = re.compile(
    r"(?:就选|指定|要的是|就)([\u4e00-\u9fffA-Za-z0-9··・（）()_-]{2,24}?)(?:吧|[,，。!！?？]|$)"
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
    "汤",
    "饭",
    "外卖",
    "酒店",
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
        for date in _DATE_RE.findall(text):
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
        address_match = _ADDRESS_RE.search(text)
        if address_match:
            raw_address = address_match.group(1).strip().rstrip("吧呀啊")
            alias = (
                "home"
                if raw_address in {"家", "家里", "家中"}
                else (
                    "company"
                    if raw_address in {"公司", "单位", "店里", "公司前台"}
                    else raw_address
                )
            )
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
        must = _dedup(self.must)[:3]
        avoid = _dedup(self.avoid)[:2]
        remaining = max(0, max_facts - len(must) - len(avoid))
        ask_reserve = 1 if self.ask and remaining else 0
        prefer = _dedup(self.prefer)[: max(0, remaining - ask_reserve)]
        remaining -= len(prefer)
        ask = _dedup(self.ask)[: min(1, remaining)]
        remaining -= len(ask)
        evidence = _dedup(self.evidence)[: min(1, remaining)]
        selected_by_title = {
            "MUST": must,
            "AVOID": avoid,
            "PREFER": prefer,
            "ASK": ask,
            "EVIDENCE": evidence,
        }
        sections: list[str] = []
        for title in ("AVOID", "MUST", "PREFER", "ASK", "EVIDENCE"):
            selected = selected_by_title[title]
            if selected:
                sections.append(f"{title}: " + " | ".join(selected))
        return ("\n".join(sections) or "MUST: follow the current instruction")[
            :max_chars
        ]


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


class CandidateLedger:
    """Typed, per-subtask observations and semantic search budgets."""

    def __init__(
        self,
        max_searches_per_family: int = 3,
        max_family_searches: int = 6,
    ) -> None:
        self.candidates: dict[str, Candidate] = {}
        self.search_counts: dict[str, int] = {}
        self.search_family_counts: dict[str, int] = {}
        # Distinct queries a family may spend before the sufficiency stop takes
        # over. Exploration is a capability, not waste: the stock agent averages
        # 3.1 searches per subtask, and units it solves use several keyword
        # families before choosing (E-042).
        self.exploration_allowance = 3
        # Observable query terms of the most recent search per family. The
        # framework reuses these when it must fill an unobserved entity kind
        # (E-035) instead of inventing new keywords.
        self.last_search_arguments: dict[str, dict[str, Any]] = {}
        # Administrative units of the user's own registered address, supplied by
        # the owning agent. Used only as an observable proximity tie-break.
        self.home_tokens: list[str] = []
        self.enrichment_read_counts: dict[str, int] = {}
        self.pending_payment_ids: set[str] = set()
        self.max_searches_per_family = max_searches_per_family
        # Per-subtask cap across *distinct* queries inside one tool family.
        # Identical signatures are already capped by max_searches_per_family;
        # this bounds keyword-variant thrashing (E-031: never-solved subtasks
        # issue 2.8x the searches of always-solved ones).
        self.max_family_searches = max_family_searches
        self.require_max_preference_coverage = False
        self._turn = 0

    def reset(self) -> None:
        self.candidates.clear()
        self.search_counts.clear()
        self.search_family_counts.clear()
        self.last_search_arguments.clear()
        self.enrichment_read_counts.clear()
        self.pending_payment_ids.clear()
        self.require_max_preference_coverage = False
        self._turn = 0

    def observe(self, tool_name: str, content: str | None) -> None:
        text = content or ""
        if not text:
            return
        self._turn += 1
        context_parent_ids: list[str] = []
        for chunk in (line.strip() for line in text.splitlines() if line.strip()):
            ids = _ID_RE.findall(chunk)
            fields = {key: value.strip() for key, value in _FIELD_RE.findall(chunk)}
            explicit_parents = [
                item
                for item in ids
                if _entity_type(item) in {"hotel", "attraction", "flight", "train"}
            ]
            if explicit_parents:
                context_parent_ids = explicit_parents
            name = next(
                (
                    fields[k]
                    for k in (
                        "product_name",
                        "hotel_name",
                        "attraction_name",
                        "shop_name",
                        "store_name",
                        "train_number",
                        "flight_number",
                        "room_type",
                        "seat_type",
                        "name",
                    )
                    if k in fields
                ),
                "",
            )
            price = _to_float(fields.get("price"))
            inventory = _to_int(fields.get("quantity"))
            for candidate_id in ids:
                parent_ids = [item for item in ids if item != candidate_id]
                if _entity_type(candidate_id) == "product":
                    parent_ids = _dedup([*parent_ids, *context_parent_ids])
                enriched_raw = chunk
                if parent_ids and _entity_type(candidate_id) == "product":
                    enriched_raw += ", parent_ids=" + "|".join(parent_ids)
                self.candidates[candidate_id] = Candidate(
                    candidate_id,
                    _entity_type(candidate_id),
                    name,
                    enriched_raw,
                    tool_name,
                    fields,
                    parent_ids,
                    price,
                    inventory,
                    self._turn,
                )
        if "status:unpaid" in text or "status=unpaid" in text:
            self.pending_payment_ids.update(
                item for item in _ID_RE.findall(text) if item.startswith("O")
            )
        if "Payment successful" in text or "支付成功" in text:
            self.pending_payment_ids.clear()
        if "cancel" in tool_name.lower() and not any(
            marker in text.lower() for marker in ("fail", "error", "失败")
        ):
            returned_ids = set(_ID_RE.findall(text))
            if returned_ids:
                self.pending_payment_ids.difference_update(returned_ids)
            else:
                self.pending_payment_ids.clear()

    @staticmethod
    def search_family(tool_name: str) -> str:
        lowered = tool_name.lower()
        if "product" in lowered:
            return "product_search"
        if "hotel" in lowered:
            return "hotel_search"
        if "attraction" in lowered:
            return "attraction_search"
        if "flight" in lowered:
            return "flight_search"
        if "train" in lowered:
            return "train_search"
        if "shop" in lowered or "store" in lowered:
            return "merchant_search"
        return tool_name

    def signature(self, tool_name: str, arguments: dict[str, Any]) -> str:
        return f"{self.search_family(tool_name)}:{json.dumps(_normalize_search_arguments(arguments), sort_keys=True, ensure_ascii=False)}"

    def register_search(self, tool_name: str, arguments: dict[str, Any]) -> int:
        signature = self.signature(tool_name, arguments)
        family = self.search_family(tool_name)
        self.search_counts[signature] = self.search_counts.get(signature, 0) + 1
        self.search_family_counts[family] = self.search_family_counts.get(family, 0) + 1
        self.last_search_arguments[family] = dict(arguments)
        return self.search_counts[signature]

    def register_enrichment_read(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> int:
        signature = f"{tool_name}:{json.dumps(arguments, sort_keys=True, ensure_ascii=False)}"
        self.enrichment_read_counts[signature] = (
            self.enrichment_read_counts.get(signature, 0) + 1
        )
        return self.enrichment_read_counts[signature]

    def search_allowed(self, tool_name: str) -> bool:
        # Arguments are not known while building the tool list. Keep the search
        # tool available and enforce the normalized signature in preflight.
        return True

    def search_budget_rejection(
        self, tool_name: str, *, execution_ready: bool
    ) -> str | None:
        """Deterministic gate for a proposed search call in this subtask.

        Two general rules, both structural (no user, task or candidate
        specifics):

        1. Distinct-query budget per tool family - stops keyword-variant
           thrashing inside one family (identical signatures are already
           capped by ``max_searches_per_family``).
        2. Sufficiency stop - once the ledger already holds a compliant
           candidate that CREATE could use *and* the agent has already explored
           the family, searching again cannot improve the outcome of this
           subtask; the agent must select and act instead.

        The sufficiency stop deliberately does **not** fire on the first
        candidate set. Trace comparison against the stock agent showed that
        firing it immediately cost the model the multi-keyword exploration it
        uses to ground a choice (ADAPT searched 1.0 times per subtask against
        stock's 3.1, and lost units where stock searched several keyword
        families before choosing) (E-042).

        Returns a rejection reason, or ``None`` when the search is allowed.
        Callers must register the search attempt before calling this so that
        counts include the current proposal, matching the signature budget.
        """
        family = self.search_family(tool_name)
        family_count = self.search_family_counts.get(family, 0)
        if family_count > self.max_family_searches:
            return (
                f"{family} search budget exhausted after "
                f"{family_count - 1} distinct attempts in this subtask; "
                "work with the candidates already in the ledger"
            )
        if (
            execution_ready
            and self.candidates
            and family_count > self.exploration_allowance
        ):
            return (
                "the candidate ledger already contains a compliant candidate "
                "for this subtask; do not search again - select it and proceed "
                "to the create/pay action"
            )
        return None

    def selected_candidates(self, arguments: dict[str, Any]) -> list[Candidate]:
        selected: list[Candidate] = []
        for key, value in arguments.items():
            if key == "user_id" or not key.endswith(("_id", "_ids")):
                continue
            for item in value if isinstance(value, list) else [value]:
                if str(item) in self.candidates:
                    selected.append(self.candidates[str(item)])
        return selected

    def constraint_candidates(self, arguments: dict[str, Any]) -> list[Candidate]:
        """Return the purchased/booked entity, excluding relational parent IDs.

        Product search rows repeat a store ID for every product.  The ledger's
        store entry therefore represents only the last observed row, not the
        product being purchased.  Candidate constraints must apply to the
        product ID (or hotel/flight/etc. ID), while the store ID is validated
        only for provenance and type.
        """
        selected = self.selected_candidates(arguments)
        for entity_type in (
            "product",
            "hotel",
            "attraction",
            "flight",
            "train",
            "shop",
            "store",
        ):
            typed = [candidate for candidate in selected if candidate.entity_type == entity_type]
            if typed:
                return typed
        return selected

    def shortlist(self, card: DecisionCard, limit: int = 5) -> list[Candidate]:
        candidates = [
            c
            for c in self.candidates.values()
            if c.entity_type not in {"order", "unknown"}
        ]
        for entity_type in (
            "product",
            "hotel",
            "attraction",
            "flight",
            "train",
            "shop",
        ):
            typed = [
                candidate
                for candidate in candidates
                if candidate.entity_type == entity_type
            ]
            if typed:
                candidates = typed
                break
        from agent.runtime.location import location_rank
        from agent.runtime.ranking import CandidateRanker

        parent_ranks = {
            candidate.candidate_id: location_rank(candidate, self.home_tokens)
            for candidate in self.candidates.values()
            if candidate.entity_type in {"shop", "store", "hotel"}
        }
        return CandidateRanker().rank(
            candidates,
            card,
            limit,
            location_tokens=self.home_tokens,
            parent_ranks=parent_ranks,
        )

    def unique_evidence_leader(self, card: DecisionCard) -> Candidate | None:
        """Return a candidate only when observable preference evidence is decisive.

        The scalar ranker is useful for ordering, but price and recency must not
        silently become execution policy.  A framework lock is therefore
        allowed only when the best compliant candidate matches strictly more
        current-card preferences than every runner-up and matches at least one
        preference.  Ties and evidence-free shortlists remain model decisions.
        """
        from agent.runtime.ranking import CandidateRanker

        ranked = self.shortlist(card, limit=8)
        if not ranked:
            return None
        ranker = CandidateRanker()
        score_by_id = ranker.decisive_preference_scores(ranked, card)
        scores = [score_by_id.get(candidate.candidate_id, 0.0) for candidate in ranked]
        if scores[0] <= 0:
            return None
        runner_up = max(scores[1:], default=-1.0)
        return ranked[0] if scores[0] > runner_up else None

    def preference_coverage_gap(
        self, arguments: dict[str, Any], card: DecisionCard
    ) -> str:
        """Describe an observable lower-evidence choice without hidden labels."""
        chosen = self.constraint_candidates(arguments)
        ranked = self.shortlist(card, limit=8)
        if not chosen or not ranked or not card.alignment_preferences():
            return ""
        from agent.runtime.ranking import CandidateRanker

        alignment = CandidateRanker.preference_alignment(ranked, card)
        scores = {
            candidate.candidate_id: alignment.decisive_score(candidate)
            for candidate in ranked
        }
        chosen_score = scores.get(chosen[0].candidate_id, 0.0)
        best_score = max(scores.values(), default=0.0)
        if best_score <= chosen_score:
            return ""
        best_ids = [
            candidate.candidate_id
            for candidate in ranked
            if scores.get(candidate.candidate_id, 0.0) == best_score
        ]
        return (
            f"selected candidate has observable preference score {chosen_score:.2f}, "
            f"while observed candidates {best_ids} score {best_score:.2f}; "
            "choose from the maximum observable preference-coverage set"
        )

    def validate_ranked_choice(
        self,
        arguments: dict[str, Any],
        card: DecisionCard,
        selected_candidate_id: str = "",
    ) -> list[str]:
        """Keep WRITE inside the compliant shortlist without taking over choice.

        Ranking is normally a retrieval aid, not an oracle. The policy model
        may choose any compliant shortlisted candidate when evidence ties. An
        explicit user selection always locks execution; otherwise a unique,
        strictly preference-evidence-leading candidate is locked as well.
        """
        chosen = self.constraint_candidates(arguments)
        if not chosen:
            return []
        chosen_id = chosen[0].candidate_id
        if selected_candidate_id and chosen_id != selected_candidate_id:
            expected = self.candidates.get(selected_candidate_id)
            expected_name = expected.name if expected else selected_candidate_id
            return [
                f"selected {chosen_id}, but the user explicitly selected "
                f"{selected_candidate_id} ({expected_name}); use that exact ID"
            ]
        if selected_candidate_id:
            return []
        evidence_leader = self.unique_evidence_leader(card)
        if evidence_leader and chosen_id != evidence_leader.candidate_id:
            return [
                f"selected {chosen_id}, but current observable preference evidence "
                f"uniquely leads to {evidence_leader.candidate_id} "
                f"({evidence_leader.name}); use that exact ID"
            ]
        compliant_ids = {
            candidate.candidate_id for candidate in self.shortlist(card, limit=8)
        }
        if compliant_ids and chosen_id not in compliant_ids:
            return [
                f"selected {chosen_id}, but it is outside the rendered compliant "
                "Candidate shortlist; choose one of the visible shortlisted IDs"
            ]
        return []

    def render(
        self, card: DecisionCard | None = None, limit: int = 8, max_chars: int = 4200
    ) -> str:
        selected = self.shortlist(card or DecisionCard(), limit)
        if not selected:
            return ""
        lines = ["## Candidate shortlist (only these observed IDs may be selected)"]
        from agent.runtime.ranking import CandidateRanker

        alignment = CandidateRanker.preference_alignment(selected, card or DecisionCard())
        for index, candidate in enumerate(selected, 1):
            matched = [atom.value for atom in alignment.matches(candidate)]
            evidence = (
                f"; preference_evidence={matched}"
                if matched
                else "; preference_evidence=[]"
            )
            lines.append(
                f"{index}. {candidate.candidate_id} [{candidate.entity_type}] "
                f"{candidate.name}{evidence}: {candidate.raw[:420]}"
            )
        if selected and selected[0].entity_type == "product":
            expanded_parents = {
                parent_id
                for candidate in self.candidates.values()
                if candidate.entity_type == "product"
                for parent_id in candidate.parent_ids
            }
            parent_candidates = [
                candidate
                for candidate in self.candidates.values()
                if candidate.entity_type
                in {"hotel", "attraction", "flight", "train"}
                and candidate.candidate_id not in expanded_parents
            ]
            if parent_candidates:
                from agent.runtime.ranking import CandidateRanker

                unexpanded = CandidateRanker().rank(parent_candidates, card or DecisionCard(), 8)
                lines.append("## Unexpanded parent candidates (READ details before CREATE)")
                for candidate in unexpanded:
                    lines.append(
                        f"- {candidate.candidate_id} [{candidate.entity_type}] "
                        f"{candidate.name}: {candidate.raw[:320]}"
                    )
        if self.pending_payment_ids:
            lines.append(
                "PENDING_PAYMENT: " + ", ".join(sorted(self.pending_payment_ids))
            )
        return "\n".join(lines)[:max_chars]

    def validate_write(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        card: DecisionCard,
        profile: dict[str, Any] | None = None,
    ) -> list[str]:
        if not _is_commit_tool(tool_name):
            return []
        errors: list[str] = []
        for key, value in arguments.items():
            if key == "user_id" or not key.endswith(("_id", "_ids")):
                continue
            for item in value if isinstance(value, list) else [value]:
                item_text = str(item)
                if (
                    item_text not in self.candidates
                    and item_text not in self.pending_payment_ids
                ):
                    errors.append(
                        f"{key}={item_text} was not returned by a tool in this subtask"
                    )
                candidate = self.candidates.get(item_text)
                expected_type = key.removesuffix("_ids").removesuffix("_id")
                compatible = {
                    "shop": {"shop"},
                    "store": {"store"},
                    "product": {"product"},
                    "hotel": {"hotel"},
                    "attraction": {"attraction"},
                    "flight": {"flight"},
                    "train": {"train"},
                    "order": {"order"},
                    "room": {"product"},
                    "ticket": {"product"},
                    "seat": {"product"},
                }
                if (
                    candidate
                    and expected_type in compatible
                    and candidate.entity_type not in compatible[expected_type]
                ):
                    errors.append(
                        f"{key} expects {expected_type} ID but {item_text} is {candidate.entity_type}"
                    )
        errors.extend(self._validate_parent_relationships(arguments))
        # Payment consumes an already-created order. Product, room, date and
        # address constraints were validated before CREATE and are not fields
        # of PAY tools; reapplying them here produces impossible requirements.
        if tool_name.startswith("pay_"):
            return _dedup(errors)
        constraint_candidates = self.constraint_candidates(arguments)
        selected_text = "\n".join(c.raw for c in constraint_candidates)
        constraints = card.constraints or [
            *[Constraint("legacy", value) for value in card.must],
            *[
                Constraint("legacy", value, operator=ConstraintOperator.EXCLUDES)
                for value in card.avoid
            ],
        ]
        for constraint in constraints:
            if not constraint.hard or constraint.target == ConstraintTarget.WORKFLOW:
                continue
            if constraint.target == ConstraintTarget.CANDIDATE:
                if constraint.operator == ConstraintOperator.EXCLUDES:
                    from agent.runtime.ranking import violates_exclusion

                    if constraint.value and violates_exclusion(
                        selected_text, constraint.value, card.prefer
                    ):
                        errors.append(
                            f"selected candidate contains forbidden value: {constraint.value}"
                        )
                elif (
                    constraint.value
                    and not _category_satisfied_by_tool(
                        constraint.kind, constraint.value, tool_name
                    )
                    and not (
                        constraint.kind == "category"
                        and not _category_groundable_in_ledger(
                            constraint.value, self.candidates.values()
                        )
                    )
                    and not _constraint_present(constraint.value, selected_text)
                ):
                    errors.append(
                        f"selected candidate does not show required value: {constraint.value}"
                    )
            else:
                errors.extend(
                    _validate_argument_constraint(
                        constraint,
                        arguments,
                        profile or {},
                        selected_text=selected_text,
                    )
                )
        if any(c.inventory == 0 for c in constraint_candidates):
            errors.append("selected candidate has zero inventory")
        selected_ids = {
            str(item)
            for key, value in arguments.items()
            if key.endswith(("_id", "_ids")) and key != "user_id"
            for item in (value if isinstance(value, list) else [value])
        }
        selected = [
            self.candidates[item] for item in selected_ids if item in self.candidates
        ]
        products = [
            candidate for candidate in selected if candidate.entity_type == "product"
        ]
        stores = {
            candidate.candidate_id
            for candidate in selected
            if candidate.entity_type == "store"
        }
        for product in products:
            parent_stores = {
                item for item in product.parent_ids if _entity_type(item) == "store"
            }
            if stores and parent_stores and stores.isdisjoint(parent_stores):
                errors.append(
                    f"product {product.candidate_id} does not belong to selected store"
                )
        return _dedup(errors)

    def _validate_parent_relationships(self, arguments: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        relationships = (
            ("room_id", "hotel_id"),
            ("ticket_id", "attraction_id"),
            ("product_id", "shop_id"),
        )
        for child_key, parent_key in relationships:
            child_id = arguments.get(child_key)
            parent_id = arguments.get(parent_key)
            child = self.candidates.get(str(child_id)) if child_id else None
            if (
                child
                and parent_id
                and child.parent_ids
                and str(parent_id) not in child.parent_ids
            ):
                errors.append(
                    f"{child_key}={child_id} was not observed under {parent_key}={parent_id}"
                )
        for child_key, possible_parents in (
            ("seat_id", ("flight_id", "train_id")),
            ("product_ids", ("store_id", "shop_id")),
        ):
            child_values = arguments.get(child_key, [])
            if not isinstance(child_values, list):
                child_values = [child_values]
            parent_id = next(
                (arguments.get(key) for key in possible_parents if arguments.get(key)),
                None,
            )
            for child_id in child_values:
                child = self.candidates.get(str(child_id)) if child_id else None
                if (
                    child
                    and parent_id
                    and child.parent_ids
                    and str(parent_id) not in child.parent_ids
                ):
                    errors.append(
                        f"{child_key}={child_id} was not observed under parent={parent_id}"
                    )
        return errors


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


def _required_slots(domain: str, facet: str, action: str, text: str) -> list[str]:
    if action != "commit":
        return []
    if domain == "delivery":
        slots = ["product", "address"]
        if "拖鞋" in text or "鞋" in text:
            slots.append("size")
        if (
            facet == "beverage"
            and "咖啡" in text
            and any(marker in text for marker in ("提神", "犯困", "熬夜", "加班", "开会"))
        ):
            slots.append("caffeine")
        return slots
    if facet in {"train", "flight"}:
        return ["departure", "destination", "date", "quantity"]
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
        "city": ("去", "在", "市", "区"),
        "room_type": ("大床", "双床", "房", "酒店"),
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
