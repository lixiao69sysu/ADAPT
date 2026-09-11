"""Bounded LLM profile summary, injected like the baseline's memory (E-046).

The stock baseline's advantage on the same model comes from its memory being an
LLM-written *generalized* preference summary ("喜欢冷色调", "对哈密瓜过敏") that
the policy model applies directly. ADAPT kept such a summary but never showed it
to the model, and asked for item-level detail instead of reusable dimensions.
"""

from __future__ import annotations

import pytest

from agent.memory.adapt_memory import ADAPTMemory

INTERACTIONS = [
    {
        "date": "2026-08-11",
        "dialogue": [
            {
                "role": "user",
                "content": "才知道自己哈密瓜过敏，以后别给我推荐含哈密瓜的东西。",
            }
        ],
    },
    {
        "date": "2026-08-20",
        "behavior": [
            {
                "behavior_type": "order",
                "content": {
                    "scenario": "delivery",
                    "merchant_name": "某水果店",
                    "tags": ["水果"],
                    "items": [{"product_name": "静谧蓝收纳盒", "price": 29}],
                },
            }
        ],
    },
]


def test_summary_is_absent_by_default():
    memory = ADAPTMemory()
    memory.update(INTERACTIONS)
    assert "用户偏好归纳" not in memory.read("买个垃圾桶")


def test_summary_block_is_prepended_when_enabled():
    memory = ADAPTMemory(enable_summary_rewrite=True)
    memory._summary_text = "审美：偏好冷色调。忌口：对哈密瓜过敏。"
    rendered = memory.read("买个垃圾桶")
    assert rendered.startswith("## 用户偏好归纳")
    assert "冷色调" in rendered
    assert "哈密瓜" in rendered
    # The card still follows the summary block.
    assert "MUST" in rendered or "PREFER" in rendered or "authorization" in rendered


def test_summary_is_bounded():
    memory = ADAPTMemory(enable_summary_rewrite=True, summary_max_chars=40)
    memory._summary_text = "冷色调。" * 100
    rendered = memory.read("买个垃圾桶")
    block = rendered.split("\n\n")[0]
    assert len(block) < 200
    assert len(memory._render_summary_block()) < 40 + 60


def test_summary_read_without_query_also_carries_the_block():
    memory = ADAPTMemory(enable_summary_rewrite=True)
    memory._summary_text = "偏好冷色调"
    assert "用户偏好归纳" in memory.read()
    assert "用户偏好归纳" not in ADAPTMemory().read()


def test_summary_rewrite_is_only_attempted_when_enabled(monkeypatch):
    calls = {"n": 0}

    def fake_update(self, interactions, llm, llm_args):  # noqa: ANN001
        calls["n"] += 1
        return "summary"

    monkeypatch.setattr(ADAPTMemory, "_llm_update_summary", fake_update)
    off = ADAPTMemory()
    off.update(INTERACTIONS, llm="some-model")
    assert calls["n"] == 0
    on = ADAPTMemory(enable_summary_rewrite=True)
    on.update(INTERACTIONS, llm="some-model")
    assert calls["n"] == 1


def test_a_failed_summary_never_breaks_the_update(monkeypatch):
    """The summary is best-effort: an endpoint failure must not lose the update."""

    def boom(**kwargs):  # noqa: ANN003
        raise RuntimeError("endpoint down")

    import vita.utils.llm_utils as llm_utils

    monkeypatch.setattr(llm_utils, "generate", boom)
    memory = ADAPTMemory(enable_summary_rewrite=True)
    memory._summary_text = "既有画像"
    detail = memory.update(INTERACTIONS, llm="some-model")
    assert "ADAPT memory updated" in detail
    assert memory._summary_text == "既有画像"
