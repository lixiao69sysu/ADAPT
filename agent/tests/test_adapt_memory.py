"""Unit tests for ADAPT memory system using mock data (no API calls)."""

import os
import sys

# Ensure the project root is importable.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


from agent.memory.adapt_memory import ADAPTMemory
from agent.memory.drift import DriftDetector
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
        assert "ASK:" in out
        assert "飞机还是高铁" in out

    def test_ota_specified_transport_no_ask(self):
        """OTA trip WITH transport specified should NOT ask."""
        m = ADAPTMemory(language="chinese")
        out = m.read("帮我订下周一北京到上海的经济舱机票")
        assert "飞机还是高铁" not in out

    def test_vague_food_asks(self):
        """Vague food request should ask for taste."""
        m = ADAPTMemory(language="chinese")
        out = m.read("晚上给我点个双人餐外卖")
        assert "ASK:" in out

    def test_cold_start_instore_asks(self):
        """Cold-start vague instore request should ask."""
        m = ADAPTMemory(language="chinese")
        out = m.read("小美同学没去过梦幻城堡。帮我推荐家店")
        assert "ASK:" in out

    def test_specified_food_no_ask(self):
        """Specific food request should NOT ask."""
        m = ADAPTMemory(language="chinese")
        out = m.read("我要吃火锅，帮我找家店")
        assert "ASK:" not in out

    def test_boyfriend_slang_no_transport_ask(self):
        """男票 contains 票 but is romance slang — must NOT trigger the OTA
        transport question (飞机/高铁) on a delivery query."""
        m = ADAPTMemory(language="chinese")
        out = m.read("晚上给我点个双人餐外卖，在家和男票一起吃！")
        # No OTA transport question (男票 is slang, not a ticket).
        assert "飞机还是高铁" not in out
        assert "出行" not in out
        # A taste ask may still fire (vague + cold start) — that's fine.


# ---------------------------------------------------------------------------
# Test 2: Drift detection (商家名比较 + 矛盾偏好抑制)
# ---------------------------------------------------------------------------

class TestDriftDetection:
    def test_brands_are_multivalued_and_do_not_drift(self):
        """Stores in different consumption contexts may coexist."""
        d = DriftDetector(drift_threshold=2)
        d.observe(Signal("brand_loyalty", "川菜馆A", 0.8, "2023-01-01", "order"))
        d.observe(Signal("brand_loyalty", "轻食店B", 0.8, "2023-02-01", "order"))
        d.observe(Signal("brand_loyalty", "轻食店B", 0.8, "2023-03-01", "order"))
        assert not d.drift_summary()

    def test_same_scoped_single_value_dimension_can_drift(self):
        d = DriftDetector(drift_threshold=2)
        d.observe(Signal("taste_preference", "少糖", 0.8, "2023-01-01", "order"))
        d.observe(Signal("taste_preference", "无糖", 0.8, "2023-02-01", "order"))
        d.observe(Signal("taste_preference", "无糖", 0.8, "2023-03-01", "order"))
        assert len(d.drift_summary()) == 1
        assert d.drift_summary()[0]["value"] == "无糖"

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
# Test 3b: instore/delivery domain normalization (v22)
# ---------------------------------------------------------------------------

class TestInstoreGating:
    """instore and delivery are the same local consumption domain. Food signals
    only carry the "delivery" label from _event_domain, so an instore query
    (探店/到店/包间/预约) used to suppress every food fact to 0.02 — collapsing
    instore recommendation subtasks. _normalize_domain maps instore -> delivery
    so food facts survive, while OTA stays distinct (still suppressed)."""

    def test_instore_query_surfaces_food_facts(self):
        m = ADAPTMemory(language="chinese")
        m.update([
            mock_interaction("2023-01-01", "order", {"store_name": "川菜馆", "product_name": "水煮鱼"}),
            mock_interaction("2023-01-02", "conversation", {}, [
                {"role": "user", "content": "我喜欢吃火锅"},
            ]),
        ])
        out = m.read("小美同学第一次来。帮我预约个包间，明天去探店吃个团建餐")
        # Food facts must NOT be domain-gated away on an instore query.
        assert "川菜馆" in out or "水煮鱼" in out or "火锅" in out

    def test_instore_query_ranks_food_above_ota(self):
        """Normalization must rank food above OTA on an instore query, not make
        instore a free pass for hotel facts. 喜来登 (ota) stays at 0.02 relevance
        while 川菜馆 (delivery≈food) returns to the 0.25 floor, so food wins."""
        m = ADAPTMemory(language="chinese")
        m.update([
            mock_interaction("2023-01-01", "order", {"store_name": "川菜馆", "product_name": "水煮鱼"}),
            mock_interaction("2023-02-01", "order", {"store_name": "锦州喜来登", "product_name": "豪华大床房"}),
        ])
        out = m.read("帮我预约一家川菜馆的包间，明天中午到店吃饭")
        assert "川菜馆" in out
        assert "喜来登" not in out


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
        lm.record(Signal("prefers_product", "川菜", 0.8, "2026-01-01", "order"))
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

    def test_distinct_brand_preferences_coexist_in_read(self):
        """Different stores are alternatives, not one drift dimension."""
        m = ADAPTMemory(language="chinese")
        # User used to love store A, then consistently buys at store B.
        m.update([mock_interaction("2023-01-01", "order", {"store_name": "川菜馆A", "product_name": "回锅肉"})])
        m.update([mock_interaction("2023-02-01", "order", {"store_name": "轻食店B", "product_name": "沙拉"})])
        m.update([mock_interaction("2023-03-01", "order", {"store_name": "轻食店B", "product_name": "鸡胸肉"})])
        assert not m.drift.drift_summary()
        out = m.read("帮我点个外卖")
        assert "川菜馆A" in out
        assert "轻食店B" in out


# ---------------------------------------------------------------------------
# Test: signal generalization (specs -> general taste dimensions)
# ---------------------------------------------------------------------------

class TestTasteGeneralization:
    def test_order_specs_lift_to_taste_dimensions(self):
        """Product specs like '少糖多冰' should surface as general taste prefs."""
        m = ADAPTMemory(language="chinese")
        m.update([mock_interaction("2023-01-01", "order", {
            "tags": ["少糖"],
            "items": [{"product_name": "豆乳黑麒麟（少糖多冰）", "price": 18, "quantity": 1}],
        })])
        out = m.read("帮我点杯喝的")
        assert "少糖" in out
        assert "多冰" in out

    def test_plain_order_no_taste_dimensions(self):
        """An order without specs/tags should not fabricate taste dimensions."""
        m = ADAPTMemory(language="chinese")
        m.update([mock_interaction("2023-01-01", "order", {
            "product_name": "可乐",
        })])
        out = m.read("帮我点个外卖")
        assert "口味/规格偏好" not in out


class TestDimensionExtraction:
    """v16a: temperature + topping dimensions must lift to taste_preference."""

    def test_iced_dimension_from_product_name(self):
        """Chilled fruit names should project a reusable temperature dimension."""
        m = ADAPTMemory(language="chinese")
        m.update([mock_interaction("2026-02-07", "order", {
            "tags": ["冰镇水果"],
            "items": [
                {"product_name": "冰镇小番茄 1盒", "price": 18.8, "quantity": 1},
                {"product_name": "冰镇荔枝 1斤", "price": 35, "quantity": 1},
            ],
        })])
        out = m.read("点一份果切送公司来，要有四种不同水果")
        assert "冰镇" in out

    def test_topping_and_temp_from_tags_and_name(self):
        """Drink tags and variants should project topping and temperature."""
        m = ADAPTMemory(language="chinese")
        m.update([mock_interaction("2026-05-22", "order", {
            "merchant_name": "古茗（射洪经开区店）",
            "tags": ["奶茶", "布蕾", "热"],
            "items": [{"product_name": "黑糖布蕾奶茶（热/三分糖）", "price": 16, "quantity": 1}],
        })])
        out = m.read("帮我再点一杯奶茶送到家")
        assert "布蕾" in out
        assert "热饮" in out

    def test_heitao_not_extracted(self):
        """A flavor word in a product name must not become a global dimension.
        The product name (常点商品) may still mention 黑糖, but the 口味/规格偏好
        dimension line must not list it."""
        m = ADAPTMemory(language="chinese")
        m.update([mock_interaction("2026-05-22", "order", {
            "merchant_name": "古茗（射洪经开区店）",
            "tags": ["奶茶", "布蕾", "热"],
            "items": [{"product_name": "黑糖布蕾奶茶（热/三分糖）", "price": 16, "quantity": 1}],
        })])
        assert not any(
            fact.value == "黑糖" and fact.dimension in {"taste", "sweetness", "attribute"}
            for fact in m.facts
        )

    def test_iced_drink_dimension_from_paren(self):
        """A cold drink variant should project a temperature dimension."""
        m = ADAPTMemory(language="chinese")
        m.update([mock_interaction("2026-06-01", "order", {
            "tags": ["奶茶", "布蕾", "冰"],
            "items": [{"product_name": "布蕾奶茶（冰）", "price": 15, "quantity": 1}],
        })])
        out = m.read("帮我点个奶茶送到公司")
        assert "冰饮" in out

    def test_garbage_jike_fragment_filtered_from_hint(self):
        """Multi-clause 忌口 fragments ('不加小料，觉得多余' -> '小料，觉得多余')
        must not crowd the hint — they are unreliable dialogue regex parses."""
        m = ADAPTMemory(language="chinese")
        m.update([mock_interaction("2026-05-20", "conversation", {}, [{
            "role": "user",
            "content": "以前我点奶茶都不加小料，觉得多余。现在发现布蕾是真不一样，这个口感太绝了",
        }])])
        out = m.read("帮我再点一杯奶茶送到家")
        assert "忌口小料" not in out


# ---------------------------------------------------------------------------
# Test: soft execution hint (v12)
# ---------------------------------------------------------------------------

class TestExecutionHint:
    def test_hint_prioritizes_avoid_first(self):
        """The execution hint must surface 忌口 before brands/tastes."""
        m = ADAPTMemory(language="chinese")
        m.update([mock_interaction("2023-01-01", "order", {"store_name": "吉野家", "product_name": "照烧鸡"})])
        m.update([mock_interaction("2023-01-02", "conversation", {}, [{
            "role": "user", "content": "我不吃鱼虾",
        }])])
        out = m.read("帮我点个外卖")
        assert out.startswith("AVOID:")
        assert "鱼虾" in out
        assert out.index("鱼虾") < out.index("吉野家")

    def test_hint_is_directive_framed(self):
        """The hint is directive but keeps a conflict escape hatch."""
        m = ADAPTMemory(language="chinese")
        m.update([mock_interaction("2023-01-01", "order", {"store_name": "吉野家", "product_name": "照烧鸡"})])
        out = m.read("帮我点个外卖")
        assert "PREFER:" in out
        assert "吉野家" in out

    def test_no_hint_on_cold_start(self):
        """Empty memory should not emit an execution hint."""
        m = ADAPTMemory(language="chinese")
        out = m.read("帮我点个外卖")
        assert "PREFER:" not in out


# ---------------------------------------------------------------------------
# Test: domain detection (v13) — short/idiomatic queries must gate properly
# ---------------------------------------------------------------------------

class TestDomainDetection:
    """v13 generalization fix: short OTA queries like '帮我订个酒店' previously
    returned domain=None (1 keyword < 3-count threshold) -> no domain gating ->
    cross-domain noise (狗咖/锦州喜来登) leaked into hotel-task hints, collapsing
    Strong-marker detection must recover these without broad false positives."""

    def _dom(self, q):
        from agent.memory.retrieval import RetrievalScorer, RetrievalConfig
        return RetrievalScorer(RetrievalConfig()).domain(q)

    def test_short_ota_queries_detected(self):
        for q in [
            "下个周末打算去长春玩，帮我订两晚房，要大床的，周五下了班就出发",
            "帮我订个酒店",
            "下个月7号要去趟哈尔滨，帮我把去程的票订上",
            "帮我买一下去桂林的票",
            "帮我推荐一些好玩的景点呢",
            "帮我下单一张回程的机票吧",
            "帮我们买两张门票吧",
            "帮我订3号到5号晚上的房间",
        ]:
            assert self._dom(q) == "ota", f"OTA misdetected as {self._dom(q)!r}: {q}"

    def test_boyfriend_slang_ticket_guard(self):
        """男票/女票 contain 票 but are NOT OTA tickets."""
        q = "晚上给我点个双人餐外卖，在家和男票一起吃！边吃边看电影，想想都幸福"
        assert self._dom(q) == "delivery"

    def test_delivery_strong_markers(self):
        assert self._dom("帮我点个外卖送到公司") == "delivery"
        assert self._dom("帮我下单一些薇诺娜面膜送到家里来") == "delivery"


# ---------------------------------------------------------------------------
# Test: v15 — profile dump restored (v14 removal collapsed mean to 0.0507);
# taste ask NOT gated by #5 Decision-Flip
# ---------------------------------------------------------------------------

class TestV15ProfileRestored:
    def test_profile_is_not_dumped_when_summary_populated(self):
        """Narrative summary is private fallback, not an unconditional dump."""
        m = ADAPTMemory(language="chinese")
        m._summary_text = "用户偏好画像内容：常选川菜馆、喜欢重口味。"
        m.update([mock_interaction("2023-01-01", "order", {"store_name": "川菜馆", "product_name": "水煮鱼"})])
        out = m.read("下周去长春，帮我订个酒店")
        assert "用户偏好画像" not in out
        assert len(out) <= 1200

    def test_no_profile_on_cold_start(self):
        """Empty _summary_text -> no profile block."""
        m = ADAPTMemory(language="chinese")
        m.update([mock_interaction("2023-01-01", "order", {"store_name": "川菜馆", "product_name": "水煮鱼"})])
        out = m.read("帮我点个外卖")
        assert "用户偏好画像" not in out

    def test_taste_ask_fires_even_with_memory(self):
        """#5 gate removed: taste ask still fires when instruction is vague,
        regardless of whether memory covers the taste."""
        m = ADAPTMemory(language="chinese")
        m.update([mock_interaction("2023-01-01", "order", {"store_name": "川菜馆", "product_name": "水煮鱼"})])
        out = m.read("帮我点个外卖")
        assert "ASK:" in out


# ---------------------------------------------------------------------------
# Run with:  python -m pytest agent/tests/test_adapt_memory.py -v
# ---------------------------------------------------------------------------
