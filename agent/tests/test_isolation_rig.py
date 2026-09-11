"""Isolation-rig switches: vary one factor at a time (E-046).

Comparing the whole agent against the stock baseline conflates three very
different costs: the memory representation, ADAPT's extra prompt blocks, and the
per-phase tool crop. These switches let a run vary exactly one of them.
"""

from __future__ import annotations

from agent.decision import CandidateLedger, TaskSpec
from agent.runtime.state import RuntimePhase, TaskRuntime
from agent.runtime.tools import ToolMeta, ToolRegistry, ToolRole


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    names = (
        ("delivery_product_search_recommand", ToolRole.SEARCH),
        ("get_delivery_product_info", ToolRole.READ),
        ("create_delivery_order", ToolRole.CREATE),
        ("pay_delivery_order", ToolRole.PAY),
        ("query_preference_memory", ToolRole.READ),
        ("record_preference_answer", ToolRole.MEMORY),
    )
    registry.meta = {name: ToolMeta(name, role) for name, role in names}
    registry.tools = [type("T", (), {"name": name})() for name, _ in names]
    return registry


def test_phase_gating_crops_tools_by_default():
    registry = build_registry()
    runtime = TaskRuntime.begin(TaskSpec.compile("帮我买个垃圾桶送到家"))
    runtime.phase = RuntimePhase.SELECT
    exposed = {tool.name for tool in registry.allowed_tools(runtime, CandidateLedger())}
    assert "create_delivery_order" not in exposed
    assert "delivery_product_search_recommand" in exposed


def test_no_phase_gating_exposes_every_environment_tool():
    registry = build_registry()
    runtime = TaskRuntime.begin(TaskSpec.compile("帮我买个垃圾桶送到家"))
    for phase in (RuntimePhase.SEARCH, RuntimePhase.SELECT, RuntimePhase.READY_TO_CREATE):
        runtime.phase = phase
        exposed = {
            tool.name
            for tool in registry.allowed_tools(
                runtime, CandidateLedger(), gate_phases=False
            )
        }
        assert "create_delivery_order" in exposed
        assert "pay_delivery_order" in exposed
        assert "delivery_product_search_recommand" in exposed
        # Framework-internal memory writes stay hidden even without gating.
        assert "record_preference_answer" not in exposed


def test_adapt_prompt_blocks_are_optional(monkeypatch):
    from vita.agent.personalization_agent import PersonalizationAgent

    from agent.adapt_agent import ADAPTAgent

    monkeypatch.setattr(
        PersonalizationAgent, "system_prompt", property(lambda self: "BASE-PROMPT")
    )
    agent = ADAPTAgent.__new__(ADAPTAgent)
    agent.enable_adapt_prompt = False
    assert ADAPTAgent.system_prompt.fget(agent) == "BASE-PROMPT"
