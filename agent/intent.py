"""Observable order-intent classification shared by spec and runtime.

The compiled :class:`~agent.decision.TaskSpec` action and the runtime
authorization state must agree on what counts as a request to *perform* a
transaction. Two drifting literal lists previously caused an order request such
as "帮我定张车票" to compile as a recommendation task while the runtime never
authorized a write (E-034): the agent searched, could not legally act, and the
subtask ended without the requested order.

The vocabulary here is phrase-level and generic:

* :data:`TRANSACTION_MARKERS` name the transaction itself ("下单", "预订").
* :data:`COMPLETION_INTENT_RE` is a verb plus a quantity/unit or item noun
  ("团一张", "定张车票", "来杯咖啡"). The tail is required so that a bare verb
  never authorizes a write: "帮我看看有没有团购券" asks for information, not a
  purchase.

Neither list may contain a user, task, candidate or product identifier.
"""

from __future__ import annotations

import re

# Explicit transaction phrases: the user names the transaction they want.
TRANSACTION_MARKERS = (
    "下单",
    "下个单",
    "帮我下单",
    "买单",
    "结账",
    "预订",
    "预定",
    "预约",
    "挂号",
    "报名",
    "包场",
    "直接买",
    "直接订",
    "直接定",
    "直接下单",
    "帮我买",
    "给我买",
    "替我买",
    "帮我订",
    "给我订",
    "帮我定",
    "给我定",
    "帮我点",
    "给我点",
    "帮我团",
    "给我团",
    "帮我搞",
    "给我搞",
    "帮我弄",
    "再买",
    "再订",
    "再点",
    "买一下",
    "买下来",
    "再来杯",
    "再来一杯",
    "再来一份",
)

_ORDER_VERB = "团|买|订|定|点|来|要|搞|弄|抢|捎|带|加|下|送"
_QUANTITY = r"一|两|二|三|四|五|六|七|八|九|十|几|半|\d+"
_UNIT = (
    "张|个|份|单|杯|碗|套|双|只|瓶|盒|袋|包|件|位|间|台|次|支|束|枚|条|块|斤|人|款|种"
)
# Units that may carry a request on their own ("订个"，"来杯"). 单 is excluded:
# it is the tail of the compound noun 订单 and would make "帮我查一下订单状态"
# look like an order.
_UNIT_STANDALONE = (
    "张|个|份|杯|碗|套|双|只|瓶|盒|袋|包|件|位|间|台|次|支|束|枚|条|块|斤|人|款|种"
)
_ITEM = (
    "票|券|房|房间|餐|饭|菜|面|咖啡|奶茶|饮品|饮料|蛋糕|甜点|水果|鲜花|花束|药|"
    "门票|车票|机票|火车票|高铁票|动车票|电影票|团购券|优惠券|代金券|套餐|酒店|民宿|"
    "外卖|商品|东西|服务|座位|位子|位|桌|包间|按摩|保洁"
)

# verb (+ optional quantity) + unit-or-item noun, in three shapes:
#   quantity + unit/item    ("团一张"，"买一份沙拉")
#   unit + item             ("定张车票"，"来杯冰咖啡"，"订个位子")
#   item                    ("订酒店"，"点外卖")
# A bare unit is not enough, so the compound noun "订单" never authorizes a
# write.  "就选第一个" and "推荐一个适合的采摘园" intentionally do not match.
#
# 送 is a transaction verb when it takes an object ("给我送个奶茶到家来"):
# with it missing, that instruction compiled as a *recommendation* task, the
# runtime never authorized the delivery, and the model that tried to order was
# rejected by our own gate while the stock agent simply delivered (E-051).
# The object requirement keeps "帮我送到家" and "有没有送货服务" out.
COMPLETION_INTENT_RE = re.compile(
    rf"(?:帮我|给我|替我|麻烦|直接|顺便|再|想)?(?:{_ORDER_VERB})"
    rf"(?:(?:{_QUANTITY})(?:{_UNIT}|{_ITEM})"
    rf"|(?:{_UNIT})[\u4e00-\u9fff]{{0,3}}?(?:{_ITEM})"
    rf"|(?:{_ITEM})"
    rf"|(?:{_UNIT_STANDALONE}))"
)


def is_transaction_request(text: str) -> bool:
    """Whether the user asked ADAPT to perform a transaction."""
    content = text or ""
    if any(marker in content for marker in TRANSACTION_MARKERS):
        return True
    return COMPLETION_INTENT_RE.search(content) is not None


def is_completion_style_request(text: str) -> bool:
    """Whether the user asked for the outcome instead of a specific option.

    Completion-style phrasing ("帮我团一张") also delegates the concrete
    candidate choice: the user asked to be served, not to pick an option.
    """
    return COMPLETION_INTENT_RE.search(text or "") is not None
