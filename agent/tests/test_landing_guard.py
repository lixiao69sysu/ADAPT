"""Zero-model tests for the landing guard (execution-timing layer).

Evidence context: an offline audit of `data/simulations/stock_dev.json`
(a dev-cohort checkpoint at 4 trials) found that 52 commit-type units ended the
subtask on a confirmation request and never wrote anything; they pass at 0.058
against 0.427 for the commit units that write and then report completion.

Every assertion here runs without an API call: generation is stubbed.
"""

from __future__ import annotations

from vita.data_model.message import (
    AssistantMessage,
    MultiToolMessage,
    ToolCall,
    ToolMessage,
)

from agent.decision import TaskSpec

# A commit-type instruction and a recommendation-type instruction. The
# assertions below re-check the compile result so heuristic drift is caught.
COMMIT = "帮我下单一杯喝的，就糯糯青山吧，送到公司"
RECOMMEND = "推荐几家电玩城吧，周末想去玩"

CANDIDATE_RESULT = (
    "StoreProduct(store_name=霸王茶姬, store_id=S17790992158045728_S00002, "
    "product_name=糯糯青山, product_id=S17790992158045728_P00002, price=16.0)"
)
CONFIRM_TEXT = "我帮你选好了：霸王茶姬的糯糯青山，少糖多冰，16元。确认的话我马上帮你下单？"
DONE_TEXT = "已为你下单。订单号：OT123，共16元。"




def _assistant(content=None, calls=None):
    return AssistantMessage(role="assistant", content=content, tool_calls=calls or [])


def _write_call(idx=1):
    return ToolCall(
        id=f"w{idx}",
        name="create_delivery_order",
        arguments={"store_id": "S17790992158045728_S00002"},
        requestor="assistant",
    )


def _search_call(idx=1, name="delivery_product_search_recommand"):
    return ToolCall(id=f"s{idx}", name=name, arguments={"keywords": ["奶茶"]}, requestor="assistant")


def _tool_message(content):
    return MultiToolMessage(
        role="tool",
        tool_messages=[
            ToolMessage(
                id="t1",
                name="delivery_product_search_recommand",
                role="tool",
                content=content,
            )
        ],
    )


def _stub(monkeypatch, replies):
    """Stub generation in both binding sites; return captured call kwargs.

    ``LandingGuardAgent`` calls through ``vita.utils.llm_utils.generate`` (so the
    runner's context guard stays in effect), while the vendored ``LLMAgent`` holds
    its own module-level binding. Both must be patched to keep the tests offline.
    """
    captured: list[dict] = []

    def fake_generate(**kwargs):
        captured.append(kwargs)
        index = min(len(captured) - 1, len(replies) - 1)
        return replies[index]

    monkeypatch.setattr("vita.utils.llm_utils.generate", fake_generate)
    monkeypatch.setattr("vita.agent.llm_agent.generate", fake_generate)
    return captured


def _summary(event: dict) -> dict:
    """Drop the attribution fields so tests can assert the payload itself."""
    return {k: v for k, v in event.items() if k not in {"subtask", "instruction"}}


# ── the heuristics themselves ────────────────────────────────────────────


def test_the_two_probe_instructions_compile_as_assumed():
    assert TaskSpec.compile(COMMIT).action == "commit"
    assert TaskSpec.compile(RECOMMEND).action == "recommend"




# ── guards on the directive decision ─────────────────────────────────────












# ── the relaunch path ────────────────────────────────────────────────────
















# ── lifecycle and configuration ──────────────────────────────────────────














