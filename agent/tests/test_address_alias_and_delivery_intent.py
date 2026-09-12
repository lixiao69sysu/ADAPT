"""E-051: the delivery verb and the address alias that made the framework lie.

Two subtasks in the R9 traces failed for reasons the framework itself created:

- "今天中午还是吃粉，给我点个粉送到单位来吧。" -> `_ADDRESS_RE` captured
  "单位来" after 送到 and kept it as a literal address, so the Decision Card said
  ``MUST: address=单位来``. The model wrote exactly that, the environment could
  not geocode it, the tool-failure guard blocked the retry and the subtask ended
  in the terminal refusal (the stock agent solves this unit in 3/4 trials).
- "好热，给我送个奶茶的到家来。" -> 送 was not a transaction verb, so the
  instruction compiled as a *recommendation*: the runtime never authorized the
  write, and the model that announced "我直接帮你下单了" was rejected by our own
  gate (the stock agent solves this unit in 4/4 trials).
"""

from __future__ import annotations

from agent.adapt_agent import ADAPTAgent
from agent.decision import (
    Constraint,
    ConstraintOperator,
    ConstraintTarget,
    DecisionCard,
    TaskSpec,
    build_decision_card,
)
from agent.intent import is_transaction_request
from agent.runtime import RuntimePhase, TaskRuntime
from agent.runtime.tool_errors import ToolErrorLedger
from agent.runtime.tools import ToolRegistry
from vita.data_model.message import ToolCall

COMPANY_INSTRUCTION = "今天中午还是吃粉，给我点个粉送到单位来吧。"
DELIVERY_INSTRUCTION = "好热，给我送个奶茶的到家来。"
COMPANY_ADDRESS = "郑州市东三街岗杜街255号河南省儿童医院(东三街院区)呼吸科护士站"
HOME_ADDRESS = "河南省郑州市金水区沙门安置小区2栋302"


class _Debug:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, event: str, **payload) -> None:
        self.events.append({"event": event, **payload})


class _DeliveryParams:
    @classmethod
    def model_json_schema(cls):
        return {
            "required": ["store_id", "product_ids", "user_id", "address"],
            "properties": {
                "store_id": {},
                "product_ids": {},
                "user_id": {},
                "address": {},
            },
        }


class _DeliveryTool:
    name = "create_delivery_order"
    params = _DeliveryParams


def _agent(profile: dict, card: DecisionCard) -> ADAPTAgent:
    agent = object.__new__(ADAPTAgent)
    agent.user_profile = profile
    agent.decision_card = card
    agent.tool_errors = ToolErrorLedger()
    agent.tool_registry = ToolRegistry()
    agent.tool_registry.rebuild([_DeliveryTool])
    agent.debug = _Debug()
    agent.enable_lessons = False
    agent._record_lesson = lambda *args, **kwargs: None
    return agent


def test_delivery_request_authorizes_the_write():
    spec = TaskSpec.compile(DELIVERY_INSTRUCTION)
    assert is_transaction_request(DELIVERY_INSTRUCTION)
    assert spec.action == "commit"
    runtime = TaskRuntime.begin(spec)
    assert runtime.authorization.create_authorized

    runtime.observe_user("随便，你看着办吧")
    runtime.observe_candidates(4, execution_ready=True)
    assert runtime.choice_settled()[0]
    assert runtime.phase == RuntimePhase.READY_TO_CREATE


def test_information_request_about_delivery_is_not_a_transaction():
    for instruction in ("帮我看看有没有送货服务", "帮我送到家就行"):
        assert not is_transaction_request(instruction), instruction


def test_company_alias_with_a_particle_is_not_a_literal_address():
    spec = TaskSpec.compile(COMPANY_INSTRUCTION)
    card = build_decision_card(spec, [])
    constraints = [c for c in card.constraints if c.kind == "address"]
    assert constraints, "the instruction names a delivery address"
    constraint = constraints[0]
    assert constraint.value == "company"
    assert constraint.operator == ConstraintOperator.RESOLVES_PROFILE
    assert spec.resolved_slots.get("address") == "company"
    assert "单位来" not in card.render()


def test_the_write_argument_is_repaired_to_the_registered_address():
    card = build_decision_card(TaskSpec.compile(COMPANY_INSTRUCTION), [])
    agent = _agent({"常住住址": HOME_ADDRESS, "单位地址": COMPANY_ADDRESS}, card)
    call = ToolCall(
        id="c1",
        name="create_delivery_order",
        arguments={"address": "单位来", "product_ids": ["P1"], "user_id": "U1"},
    )
    agent._normalize_address_call(call)
    assert call.arguments["address"] == COMPANY_ADDRESS
    assert any(
        event["event"] == "address_argument_repaired" for event in agent.debug.events
    )


def test_home_alias_without_the_send_prefix_is_resolved():
    card = build_decision_card(TaskSpec.compile(DELIVERY_INSTRUCTION), [])
    constraints = [c for c in card.constraints if c.kind == "address"]
    assert constraints and constraints[0].value == "home"
    agent = _agent({"常住住址": HOME_ADDRESS}, card)
    call = ToolCall(
        id="c2",
        name="create_delivery_order",
        arguments={"address": "家", "product_ids": ["P1"], "user_id": "U1"},
    )
    agent._normalize_address_call(call)
    assert call.arguments["address"] == HOME_ADDRESS


def test_a_real_address_is_never_hijacked_by_the_home_token():
    spec = TaskSpec.compile("帮我送到家乐福超市门口")
    card = build_decision_card(spec, [])
    constraints = [c for c in card.constraints if c.kind == "address"]
    assert constraints and constraints[0].value == "家乐福超市门口"

    literal = build_decision_card(
        TaskSpec.compile("送到郑州市金水区国基路166号"), []
    )
    values = [c.value for c in literal.constraints if c.kind == "address"]
    assert values == ["郑州市金水区国基路166号"]


def test_an_alias_in_a_contains_constraint_still_resolves():
    """Belt and braces: a card built with the old operator must still resolve."""
    card = DecisionCard(
        constraints=[
            Constraint(
                "address",
                "单位",
                ConstraintTarget.ARGUMENT,
                ConstraintOperator.CONTAINS,
            )
        ]
    )
    agent = _agent({"单位地址": COMPANY_ADDRESS}, card)
    call = ToolCall(
        id="c3",
        name="create_delivery_order",
        arguments={"address": "单位来", "product_ids": ["P1"], "user_id": "U1"},
    )
    agent._normalize_address_call(call)
    assert call.arguments["address"] == COMPANY_ADDRESS
