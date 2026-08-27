"""Gates for the three authority centers and observable harness boundary."""

from pathlib import Path

from agent.adapt_agent import ADAPTAgent
from agent.candidate_attribution_report import aggregate
from agent.lessons import ExecutionLessonStore
from agent.memory.adapt_memory import ADAPTMemory
from agent.memory.facts import PreferenceFact


def test_agent_centers_are_views_of_single_live_authorities():
    agent = ADAPTAgent(
        tools=[],
        domain_policy="time={time}",
        memory=ADAPTMemory(language="chinese"),
        user_profile={"user_id": "center-user"},
        time="2026-08-27 12:00:00",
        language="chinese",
    )

    assert agent.semantic_memory.memory is agent.memory
    assert agent.decision_runtime.runtime is agent.runtime
    assert agent.decision_runtime.ledger is agent.ledger
    assert agent.safety_kernel.operations is agent.operations
    assert agent.safety_kernel.lineage is agent.lineage


def test_typed_memory_retrieval_is_evidence_bound_and_side_effect_free():
    memory = ADAPTMemory(language="chinese")
    fact = PreferenceFact(
        fact_id="fact-1",
        scope="delivery",
        facet="retail",
        dimension="material",
        value="azure",
        polarity="positive",
        confidence=0.8,
        observed_at="2026-08-27",
        source_type="user_explicit",
        evidence_ids=["event-1"],
    )
    memory.preference_store.facts.append(fact)
    before = memory.storage_stats()

    first = memory.retrieve_facts(
        task_scope="delivery", candidate_fields=("material=azure",)
    )
    second = memory.retrieve_facts(
        task_scope="delivery", candidate_fields=("material=azure",)
    )

    assert first == second == (fact,)
    assert memory.storage_stats() == before
    assert memory.retrieve_facts(
        task_scope="ota", candidate_fields=("material=azure",)
    ) == ()


def test_lesson_store_rejects_framework_self_opinion():
    store = ExecutionLessonStore("user")
    assert store.add(
        "general", "general", "preference_undercoverage",
        "framework ranking", "force the first candidate",
    ) is None
    assert store.all() == []
    assert store.add(
        "general", "general", "tool_error", "visible error", "correct args"
    ) is not None


def test_report_always_exposes_all_twelve_causal_layers():
    report = aggregate(
        [
            {"event": "subtask_begin"},
            {"event": "preflight_rejected"},
            {"event": "failure_attributed", "owner": "environment", "stage": "tool_result"},
            {"event": "lesson_suppressed"},
        ]
    )
    assert tuple(report["causal_layers"]) == (
        "TaskSpec", "Search", "Grounding", "Admissibility", "Ranking",
        "Presentation", "Selection", "ExecutionPlan", "Preflight", "Model",
        "Environment", "Harness",
    )
    assert report["causal_layers"]["Preflight"]["rejected"] == 1
    assert report["causal_layers"]["Environment"]["tool_result"] == 1


def test_production_code_does_not_import_isolated_legacy_policies():
    root = Path(__file__).resolve().parents[1]
    forbidden = (
        "agent.memory.reflexion",
        "agent.framework.search",
        "agent.framework.tool_resolution",
    )
    offenders = []
    for path in root.rglob("*.py"):
        if "tests" in path.parts or path.name in {
            "reflexion.py", "search.py", "tool_resolution.py"
        }:
            continue
        text = path.read_text(encoding="utf-8")
        if any(module in text for module in forbidden):
            offenders.append(str(path))
    assert offenders == []
