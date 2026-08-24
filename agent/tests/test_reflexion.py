"""Unit tests for the Reflexion self-evolution flywheel skeleton (no API calls)."""

import os
import sys

# Ensure the project root is importable.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from agent.memory.reflexion import (
    FAILURE_MODE_DATE,
    FAILURE_MODE_DRIFT,
    FAILURE_MODE_GENERIC,
    FAILURE_MODE_MISMATCH,
    FAILURE_MODE_PROACTIVE,
    FAILURE_MODE_TOOL,
    ReflexionEngine,
    ReflexionLesson,
    classify_failure,
    distill_lesson_heuristic,
    distill_lesson_prompt,
)


@pytest.fixture
def engine():
    return ReflexionEngine(max_lessons=5, top_k=3)


def _lesson(**kw):
    defaults = dict(
        id=0, domain="delivery", subtask_id="sub_T_1", instruction="帮我点个外卖",
        lesson="少糖要求没执行到位",
    )
    defaults.update(kw)
    return ReflexionLesson(**defaults)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

class TestStorage:
    def test_add_and_len(self, engine):
        engine.add("delivery", "sub_T_1", "帮我点外卖", "少糖要求没执行到位")
        assert len(engine) == 1
        assert engine.all()[0].lesson == "少糖要求没执行到位"
        assert engine.all()[0].domain == "delivery"

    def test_add_rejects_empty(self, engine):
        with pytest.raises(ValueError):
            engine.add("delivery", "sub_T_1", "帮我点外卖", "   ")

    def test_add_dedupes_exact_text(self, engine):
        engine.add("delivery", "sub_T_1", "帮我点外卖", "少糖要求没执行到位")
        engine.add("delivery", "sub_T_2", "再点一次", "少糖要求没执行到位")
        assert len(engine) == 1

    def test_prune_keeps_most_important(self, engine):
        for i in range(8):
            engine.add("delivery", f"sub_T_{i}", f"指令{i}", f"教训{i}", importance=float(i))
        assert len(engine) == 5
        imps = sorted(l.importance for l in engine.all())
        assert imps == [3.0, 4.0, 5.0, 6.0, 7.0]

    def test_reset(self, engine):
        engine.add("delivery", "sub_T_1", "x", "y")
        engine.reset()
        assert len(engine) == 0


# ---------------------------------------------------------------------------
# Retrieval: domain gating + keyword relevance + importance tiebreak
# ---------------------------------------------------------------------------

class TestRetrieval:
    def test_cross_domain_suppressed(self, engine):
        engine.add("ota", "sub_O_1", "帮我订个酒店", "大床房要求", importance=10.0)
        engine.add("delivery", "sub_D_1", "点杯少糖奶茶", "少糖要求没执行到位", importance=5.0)
        got = engine.retrieve("帮我点一杯少糖的奶茶", domain="delivery")
        assert len(got) == 1
        assert got[0].subtask_id == "sub_D_1"  # OTA lesson fully suppressed

    def test_keyword_overlap_ranks_above_same_domain_generic(self, engine):
        engine.add("delivery", "sub_D_1", "点杯少糖奶茶", "少糖要求没执行到位")
        engine.add("delivery", "sub_D_2", "随便点个吃的", "点餐前要确认忌口")
        got = engine.retrieve("帮我点一杯少糖的奶茶")
        assert got[0].subtask_id == "sub_D_1"   # overlaps 少糖/奶茶 tokens

    def test_importance_breaks_overlap_tie(self, engine):
        engine.add("delivery", "sub_D_1", "点杯少糖奶茶", "少糖要求没执行到位", importance=3.0)
        engine.add("delivery", "sub_D_2", "点杯少糖奶茶", "少糖要求要写进备注", importance=8.0)
        got = engine.retrieve("点少糖奶茶")
        assert got[0].subtask_id == "sub_D_2"   # same overlap, higher importance first

    def test_empty_query_returns_nothing(self, engine):
        engine.add("delivery", "sub_D_1", "x", "y")
        assert engine.retrieve("") == []

    def test_top_k_respected(self, engine):
        for i in range(10):
            engine.add("delivery", f"sub_D_{i}", "点杯少糖奶茶", f"教训{i}少糖", importance=5.0)
        got = engine.retrieve("点少糖奶茶")
        assert len(got) == 3


# ---------------------------------------------------------------------------
# Injection rendering
# ---------------------------------------------------------------------------

class TestInjection:
    def test_empty_renders_empty(self):
        assert ReflexionEngine().format_injection([]) == ""

    def test_renders_block_with_escape_hatch(self, engine):
        engine.add("delivery", "sub_D_1", "点杯少糖奶茶", "少糖要求没执行到位")
        out = engine.format_injection(engine.all())
        assert out.startswith("【经验教训】")
        assert "少糖要求没执行到位" in out
        assert "sub_D_1" in out
        assert "以本次指令为准" in out   # soft framing, not a hard gate

    def test_injection_text_is_prependable(self, engine):
        engine.add("delivery", "sub_D_1", "点杯少糖奶茶", "少糖要求没执行到位")
        base = "【用户偏好画像】\n...\n【本任务执行提示】..."
        combined = engine.format_injection(engine.retrieve("点少糖")) + "\n\n" + base
        assert combined.startswith("【经验教训】")


# ---------------------------------------------------------------------------
# Failure-mode classification + heuristic lessons
# ---------------------------------------------------------------------------

class TestClassification:
    def test_tool_termination_wins(self):
        assert classify_failure("订大床房", last_action="search(hotel)", termination="too_many_errors") == FAILURE_MODE_TOOL

    def test_proactive_skill(self):
        assert classify_failure("帮我推荐下", skill=["proactive"]) == FAILURE_MODE_PROACTIVE

    def test_date_markers(self):
        assert classify_failure("帮我订下周6去上海的高铁票") == FAILURE_MODE_DATE

    def test_drift_markers(self):
        assert classify_failure("要杯少糖多冰的奶茶") == FAILURE_MODE_DRIFT

    def test_last_action_mismatch(self):
        assert classify_failure("帮我点个外卖", last_action="search('汉堡')") == FAILURE_MODE_MISMATCH

    def test_generic(self):
        assert classify_failure("帮我点个外卖") == FAILURE_MODE_GENERIC

    def test_heuristic_returns_per_mode(self):
        assert "工具" in distill_lesson_heuristic("x", "delivery", termination="too_many_errors")
        assert "日期" in distill_lesson_heuristic("订下周6的票", "ota")
        assert "甜度" in distill_lesson_heuristic("要杯少糖多冰", "delivery")  # DRIFT-mode lesson
        assert distill_lesson_heuristic("帮我推荐下", "instore", skill=["proactive"]) != distill_lesson_heuristic("随便", "instore")

    def test_llm_prompt_mentions_scene(self):
        p = distill_lesson_prompt("帮我点杯少糖奶茶", "delivery", "search('奶茶')", "【用户偏好画像】")
        assert "失败指令" in p and "最后动作" in p and "注入的记忆" in p


# ---------------------------------------------------------------------------
# Persistence roundtrip
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_to_from_dict_roundtrip(self, engine):
        engine.add("delivery", "sub_D_1", "点杯少糖奶茶", "少糖要求没执行到位",
                   failure_mode=FAILURE_MODE_DRIFT, timestamp="2026-08-20 10:00:00")
        blob = engine.to_dict()
        assert isinstance(blob, list) and len(blob) == 1
        e2 = ReflexionEngine()
        e2.from_dict(blob)
        assert len(e2) == 1
        assert e2.all()[0].lesson == "少糖要求没执行到位"
        assert e2.all()[0].failure_mode == FAILURE_MODE_DRIFT
        assert e2.all()[0].timestamp == "2026-08-20 10:00:00"

    def test_from_dict_skips_malformed(self):
        e = ReflexionEngine()
        e.from_dict([{"lesson": "  ", "domain": "delivery"}])   # empty lesson skipped
        e.from_dict([{"lesson": "ok", "importance": "not-a-number"}])  # bad float skipped
        assert len(e) == 0
