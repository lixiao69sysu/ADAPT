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

from agent.decision import (
    ConstraintOperator,
    TaskSpec,
    build_decision_card,
)
from agent.intent import is_transaction_request

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


