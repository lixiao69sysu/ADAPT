"""Unit tests for ADAPT memory system using mock data (no API calls)."""

import os
import sys

# Ensure the project root is importable.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from agent.memory.adapt_memory import ADAPTMemory
from agent.memory.drift import DriftDetector
from agent.memory.proactive import ProactiveEngine
from agent.memory.signals import Signal


# ---------------------------------------------------------------------------
# Fixtures: mock interactions in VitaBench init_gen format
# ---------------------------------------------------------------------------

def mock_interaction(order_date, behavior_type, content, dialogue=None):
    """Build one init_gen-format interaction."""
    inter = {"date": order_date, "behavior": []}
    if behavior_type:
        inter["behavior"].append({"behavior_type": behavior_type, "content": content})
    if dialogue:
        inter["dialogue"] = dialogue
    return inter


# ---------------------------------------------------------------------------
# Test 1: Proactive asking (时序修复后，指令注入正确)
# ---------------------------------------------------------------------------

class TestProactiveAsking:
    def test_ota_missing_transport_asks(self):
        """OTA trip without transport mode should trigger asking."""
        m = ADAPTMemory(language="chinese")
        out = m.read("下周6号要去逛迪斯尼了，帮我买去迪的票")
        assert "建议先向用户询问" in out
        assert "飞机还是高铁" in out

    def test_ota_specified_transport_no_ask(self):
        """OTA trip WITH transport specified should NOT ask."""
        m = ADAPTMemory(language="chinese")
        out = m.read("帮我订下周一北京到上海的经济舱机票")
        assert "建议先向用户询问" not in out

    def test_vague_food_asks(self):
        """Vague food request should ask for taste."""
        m = ADAPTMemory(language="chinese")
        out = m.read("晚上给我点个双人餐外卖")
        assert "建议先向用户询问" in out

    def test_cold_start_instore_asks(self):
        """Cold-start vague instore request should ask."""
        m = ADAPTMemory(language="chinese")
        out = m.read("小美同学没去过梦幻城堡。帮我推荐家店")
        assert "建议先向用户询问" in out

    def test_specified_food_no_ask(self):
        """Specific food request should NOT ask."""
        m = ADAPTMemory(language="chinese")
        out = m.read("我要吃火锅，帮我找家店")
        assert "建议先向用户询问" not in out

    def test_repeated_reads_do_not_consume_question_budget(self):
        m = ADAPTMemory(language="chinese", max_questions=2)

        first = m.read("下周去上海，帮我买张票")
        second = m.read("下周去上海，帮我买张票")

        assert "建议先向用户询问" in first
        assert "建议先向用户询问" in second
        assert m.proactive.asked_this_subtask == 0

    def test_question_tool_consumes_once_and_never_returns_empty(self):
        m = ADAPTMemory(language="chinese", max_questions=1)

        question = m.suggest_question_tool("下周去上海，帮我买张票")
        exhausted = m.suggest_question_tool("下周去上海，帮我买张票")

        assert "飞机还是高铁" in question
        assert m.proactive.asked_this_subtask == 1
        assert exhausted
        assert "无需继续询问" in exhausted

    def test_question_tool_returns_direct_question_verbatim(self):
        m = ADAPTMemory(language="chinese", max_questions=1)
        direct = "请问您的头发长度是长发、中长发还是短发？"

        result = m.suggest_question_tool(direct)

        assert result == direct
        assert m.proactive.asked_this_subtask == 1


# ---------------------------------------------------------------------------
# Test 2: Drift detection (商家名比较 + 矛盾偏好抑制)
# ---------------------------------------------------------------------------

class TestDriftDetection:
    def test_drift_on_conflicting_same_predicate(self):
        """Two conflicting values on the same predicate should drift."""
        d = DriftDetector(drift_threshold=2)
        # Two signals preferring different stores.
        d.observe(Signal("brand_loyalty", "川菜馆A", 0.8, "2023-01-01", "order"))
        d.observe(Signal("brand_loyalty", "轻食店B", 0.8, "2023-02-01", "order"))
        # Conflict 1: new value differs -> conflict_count=1, no drift yet.
        assert not d.drift_summary()
        d.observe(Signal("brand_loyalty", "轻食店B", 0.8, "2023-03-01", "order"))
        # Conflict 2: drifted.
        assert len(d.drift_summary()) == 1
        assert d.drift_summary()[0]["value"] == "轻食店B"

    def test_same_value_reinforces_no_drift(self):
        """Repeated same values should reinforce, not drift."""
        d = DriftDetector(drift_threshold=2)
        d.observe(Signal("brand_loyalty", "川菜馆A", 0.8, "2023-01-01", "order"))
        d.observe(Signal("brand_loyalty", "川菜馆A", 0.8, "2023-02-01", "order"))
        assert not d.drift_summary()

    def test_opinion_signal_uses_store_name(self):
        """Opinion signal object should be store name, not full JSON."""
        from agent.memory.signals import SignalParser
        p = SignalParser()
        inter = mock_interaction(
            "2023-01-01",
            "comment",
            {"target_name": "重庆鸡公煲", "comment_text": "很好吃"},
        )
        signals = p.parse([inter])
        opinion = [s for s in signals if s.predicate == "likes_food"]
        assert len(opinion) == 1
        assert "重庆鸡公煲" in opinion[0].object
        # Should NOT be the raw JSON blob.
        assert opinion[0].object != '{"target_name": "重庆鸡公煲"...}'


# ---------------------------------------------------------------------------
# Test 3: Retrieval domain gating (OTA 查询不混入外卖)
# ---------------------------------------------------------------------------

class TestRetrievalGating:
    def _build_memory_with_mixed_data(self):
        m = ADAPTMemory(language="chinese")
        # Feed food interactions.
        m.update([
            mock_interaction("2023-01-01", "order", {"store_name": "川菜馆", "product_name": "水煮鱼"}),
            mock_interaction("2023-01-15", "order", {"store_name": "面馆", "product_name": "牛肉面"}),
        ])
        # Feed OTA interactions.
        m.update([
            mock_interaction("2023-02-01", "order", {"store_name": "亚朵酒店", "product_name": "大床房"}),
            mock_interaction("2023-02-10", "order", {"store_name": "锦州喜来登", "product_name": "豪华大床房"}),
        ])
        return m

    def test_ota_query_prefers_hotel_facts(self):
        m = self._build_memory_with_mixed_data()
        out = m.read("下周要去三亚，帮我订个酒店")
        # Should surface hotel-related facts.
        assert "亚朵酒店" in out or "喜来登" in out or "酒店" in out

    def test_delivery_query_prefers_food_facts(self):
        m = self._build_memory_with_mixed_data()
        out = m.read("晚上想吃水煮鱼，帮我点个外卖")
        # Should surface food facts, hotel facts should be suppressed or low.
        assert "水煮鱼" in out or "川菜馆" in out

    def test_retrieval_returns_something(self):
        """Memory should always return something for a known query."""
        m = self._build_memory_with_mixed_data()
        out = m.read("帮我订个去北京的机票")
        assert len(out) > 0


# ---------------------------------------------------------------------------
# Test 4: Lifecycle / selective forgetting
# ---------------------------------------------------------------------------

class TestLifecycle:
    def test_durable_facts_not_forgotten(self):
        """Complaints (durable) should survive even long gaps."""
        from agent.memory.lifecycle import LifecycleManager
        lm = LifecycleManager()
        lm.record(Signal("avoids_food", "香菜", 0.95, "2020-01-01", "complaint"))
        assert lm.facts[0].lifetime_type == "durable"
        # 5 years later: still alive (confidence floor is 0.2).
        assert lm.facts[0].is_alive(1825)
        # durable: 5y later confidence should still be meaningfully high.
        assert lm.facts[0].decay(1825) > 0.5

    def test_ephemeral_facts_forgotten(self):
        """Ephemeral facts (browse/search) should decay fast."""
        from agent.memory.lifecycle import LifecycleManager
        lm = LifecycleManager()
        lm.record(Signal("searches", "网红奶茶", 0.4, "2026-01-01", "search"))
        assert lm.facts[0].lifetime_type == "ephemeral"
        # 60 days later: mostly decayed.
        assert lm.facts[0].decay(60) < 0.2

    def test_normal_facts_mid_decay(self):
        """Normal taste facts decay with ~180d half-life."""
        from agent.memory.lifecycle import LifecycleManager
        lm = LifecycleManager()
        lm.record(Signal("likes_food", "川菜", 0.8, "2026-01-01", "conversation"))
        assert lm.facts[0].lifetime_type == "normal"
        # 180 days: ~half confidence.
        assert 0.3 < lm.facts[0].decay(180) < 0.6


# ---------------------------------------------------------------------------
# Test 5: 时序修复 — read 用正确指令（模拟 orchestrator 修复后的行为）
# ---------------------------------------------------------------------------

class TestInstructionTiming:
    def test_read_uses_correct_instruction(self):
        """After the timing fix, memory.read(query=instruction) should use the
        CURRENT subtask instruction, not a stale/None one."""
        m = ADAPTMemory(language="chinese")
        # Feed some food + travel data.
        m.update([
            mock_interaction("2023-01-01", "order", {"store_name": "川菜馆", "product_name": "水煮鱼"}),
            mock_interaction("2023-02-01", "order", {"store_name": "亚朵酒店", "product_name": "大床房"}),
        ])
        # Simulate orchestrator: set_current_instruction then system_prompt read.
        ota_out = m.read("下周去三亚，帮我订酒店")
        food_out = m.read("晚上想吃水煮鱼外卖")
        # Different instructions should retrieve different, relevant facts.
        assert "亚朵酒店" in ota_out
        assert "水煮鱼" in food_out

    def test_drifted_preference_suppressed_in_read(self):
        """A drifted-away preference should be suppressed from read output."""
        m = ADAPTMemory(language="chinese")
        # User used to love store A, then consistently buys at store B.
        m.update([mock_interaction("2023-01-01", "order", {"store_name": "川菜馆A", "product_name": "回锅肉"})])
        m.update([mock_interaction("2023-02-01", "order", {"store_name": "轻食店B", "product_name": "沙拉"})])
        m.update([mock_interaction("2023-03-01", "order", {"store_name": "轻食店B", "product_name": "鸡胸肉"})])
        # Drift should have fired (A -> B).
        assert len(m.drift.drift_summary()) >= 1
        # Reading food task should not surface the old store A.
        out = m.read("帮我点个外卖")
        assert "川菜馆A" not in out


# ---------------------------------------------------------------------------
# Run with:  python -m pytest agent/tests/test_adapt_memory.py -v
# ---------------------------------------------------------------------------
