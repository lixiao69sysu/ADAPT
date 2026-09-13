"""Zero-model, observable-only audit for ADAPT's preference memory.

The audit deliberately evaluates memory mechanics rather than benchmark
success.  It consumes only task identity plus each subtask's public domain,
instruction, and interaction history.  It never reads evaluator criteria,
hidden user intention, rewards, targets, or environment internals.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from agent.vitabench_bootstrap import enable_vitabench_utf8

enable_vitabench_utf8()

from vita.domains.personalization.environment import get_tasks

from agent.decision import TaskSpec, _valid_fact_value
from agent.memory.adapt_memory import ADAPTMemory
from agent.memory.facts import PreferenceFact, SCALAR_DIMENSIONS, fact_from_signal
from agent.memory.signals import SignalParser
from agent.memory.slots import resolve_preference_slots
from agent.vitabench_runner import SPLIT_SEED, stable_user_split


# The single-valued boundary is declared once, in ``agent.memory.facts``; see
# the comment on ``SCALAR_DIMENSIONS`` there.
_SCALAR_DIMENSIONS = SCALAR_DIMENSIONS
_LOCAL_SCOPES = {"delivery", "instore", "local_commerce"}
_QUESTION_DIMENSIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("transport", ("飞机还是高铁", "出行方式", "倾向飞机", "倾向高铁")),
    ("room_type", ("住宿有特别要求", "大床房", "亲子房", "房型")),
    ("party_size", ("几个人", "人一起", "包间或其他要求")),
    ("caffeine", ("高咖啡因", "低咖啡因", "咖啡因")),
    ("time", ("什么时间", "几点", "按时间安排")),
    ("budget", ("预算范围", "大概的预算", "价格范围")),
    ("taste", ("什么口味", "口味要求", "更倾向什么口味", "想吃什么类型")),
)
_SCENARIO_CONTEXT = {
    "hotel": ("ota", "hotel"),
    "flight": ("ota", "flight"),
    "train": ("ota", "train"),
    "attraction": ("ota", "attraction"),
    "taxi": ("ota", "taxi"),
}


@dataclass
class FacetAudit:
    subtasks: int = 0
    facts_available: int = 0
    facts_recalled: int = 0
    questions_proposed: int = 0
    known_slot_question_conflicts: int = 0
    pool_values_dropped: int = 0


@dataclass
class MemoryAudit:
    cohort: str
    split_seed: str
    users: int = 0
    subtasks: int = 0
    parsed_signals: int = 0
    active_facts: int = 0
    superseded_facts: int = 0
    users_at_fact_capacity: int = 0
    users_at_stream_capacity: int = 0
    users_at_entity_capacity: int = 0
    preference_store_entries: int = 0
    entity_index_entries: int = 0
    structured_scope_checks: int = 0
    structured_scope_mismatches: int = 0
    questions_proposed: int = 0
    known_slot_question_conflicts: int = 0
    scalar_slot_conflicts: int = 0
    near_duplicate_pairs: int = 0
    relevant_facts_available: int = 0
    relevant_facts_recalled: int = 0
    pool_values_available: int = 0
    pool_values_dropped: int = 0
    pool_truncated_subtasks: int = 0
    active_facts_by_dimension: dict[str, int] = field(default_factory=dict)
    active_facts_by_source_type: dict[str, int] = field(default_factory=dict)
    questions_by_dimension: dict[str, int] = field(default_factory=dict)
    known_slot_conflicts_by_dimension: dict[str, int] = field(default_factory=dict)
    by_facet: dict[str, FacetAudit] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["observable_recall_rate"] = _rate(
            self.relevant_facts_recalled, self.relevant_facts_available
        )
        result["known_slot_question_conflict_rate"] = _rate(
            self.known_slot_question_conflicts, self.questions_proposed
        )
        result["structured_scope_mismatch_rate"] = _rate(
            self.structured_scope_mismatches, self.structured_scope_checks
        )
        for values in result["by_facet"].values():
            values["observable_recall_rate"] = _rate(
                values["facts_recalled"], values["facts_available"]
            )
        result["data_access"] = {
            "inputs": ["task.id", "subtask.subtask_id", "subtask.domain", "subtask.instruction", "subtask.interactions"],
            "forbidden": ["evaluation_criteria", "user_intention", "skill_tested", "reward", "target_product_ids", "target/distraction markers"],
            "model_calls": 0,
            "evaluator_calls": 0,
            "per_user_findings_emitted": False,
        }
        return result


def question_dimension(question: str | None) -> str:
    """Classify a proposed question by stable semantic field, not user/entity."""
    text = question or ""
    for dimension, markers in _QUESTION_DIMENSIONS:
        if any(marker in text for marker in markers):
            return dimension
    return ""


def _fact_relevant(fact: PreferenceFact, spec: TaskSpec) -> bool:
    if fact.status != "active":
        return False
    scope_match = fact.scope == spec.domain or (
        spec.domain in _LOCAL_SCOPES and fact.scope in _LOCAL_SCOPES
    )
    if scope_match and fact.facet in {spec.facet, "general", "travel", "service"}:
        return True
    if fact.scope == "general" and fact.facet == "general":
        return fact.dimension in {
            "safety", "budget", "room_type", "transport", "seat", "size",
            "color", "attribute", "time", "caffeine",
        }
    return fact.dimension == "safety" and spec.facet in {"restaurant", "beverage"}


def _normalized_value(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", (value or "").lower())


def _near_duplicate_pairs(facts: Iterable[PreferenceFact]) -> int:
    groups: dict[tuple[str, str, str, str], list[str]] = defaultdict(list)
    for fact in facts:
        if fact.status == "active":
            groups[(fact.scope, fact.facet, fact.dimension, fact.polarity)].append(
                _normalized_value(fact.value)
            )
    pairs = 0
    for values in groups.values():
        unique = sorted({value for value in values if len(value) >= 3})
        for index, left in enumerate(unique):
            for right in unique[index + 1:]:
                shorter, longer = sorted((left, right), key=len)
                if shorter in longer and len(shorter) / len(longer) >= 0.5:
                    pairs += 1
    return pairs


def _scalar_conflicts(facts: Iterable[PreferenceFact]) -> int:
    slots: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    for fact in facts:
        if (
            fact.status == "active"
            and fact.polarity == "positive"
            and fact.dimension in _SCALAR_DIMENSIONS
        ):
            slots[(fact.scope, fact.facet, fact.dimension, fact.category)].add(
                _normalized_value(fact.value)
            )
    return sum(1 for values in slots.values() if len(values) > 1)


def _scenario_expectation(raw: str) -> tuple[str, str] | None:
    try:
        payload = json.loads(raw) if raw and raw.lstrip().startswith("{") else None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    scenario = str(payload.get("scenario", "")).strip().lower()
    if scenario in _SCENARIO_CONTEXT:
        return _SCENARIO_CONTEXT[scenario]
    if scenario in {"delivery", "instore"}:
        return scenario, ""
    return None


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def audit_tasks(tasks: Iterable[Any], selected_ids: Iterable[str], cohort: str = "dev") -> MemoryAudit:
    """Replay visible histories and return aggregate-only memory diagnostics."""
    selected = set(selected_ids)
    report = MemoryAudit(cohort=cohort, split_seed=SPLIT_SEED)
    parser = SignalParser()
    dimension_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    question_counts: Counter[str] = Counter()
    question_conflict_counts: Counter[str] = Counter()
    for task in tasks:
        task_id = task.id  # allowed field; used only for selection and isolation
        if task_id not in selected:
            continue
        report.users += 1
        memory = ADAPTMemory(language="chinese", user_id=task_id)
        audited_interactions: set[str] = set()
        for subtask in task.subtasks:
            # These are the complete and exclusive subtask fields read here.
            _subtask_id = subtask.subtask_id
            domain = subtask.domain
            instruction = subtask.instruction
            interactions = subtask.interactions
            del _subtask_id  # deliberately excluded from aggregate output

            report.subtasks += 1
            memory.begin_subtask(instruction)
            fresh_interactions = []
            for interaction in interactions:
                encoded = json.dumps(
                    interaction, ensure_ascii=False, sort_keys=True, default=str
                )
                fingerprint = hashlib.sha1(encoded.encode("utf-8")).hexdigest()
                if fingerprint not in audited_interactions:
                    audited_interactions.add(fingerprint)
                    fresh_interactions.append(interaction)
            fresh_signals = parser.parse(fresh_interactions)
            report.parsed_signals += len(fresh_signals)
            for index, signal in enumerate(fresh_signals):
                expected = _scenario_expectation(signal.raw)
                if not expected:
                    continue
                report.structured_scope_checks += 1
                fact = fact_from_signal(signal, f"audit-{index}")
                expected_scope, expected_facet = expected
                mismatch = fact.scope != expected_scope or (
                    bool(expected_facet) and fact.facet != expected_facet
                )
                report.structured_scope_mismatches += int(mismatch)

            memory.update(interactions)  # no llm: zero model calls
            spec = TaskSpec.compile(instruction)
            # Respect the task's visible domain when terse wording defeats the
            # lexical compiler.  This changes audit routing, not runtime memory.
            spec.domain = domain
            card = memory.compile_task(instruction)
            facts = list(memory.facts)
            facet = spec.facet
            facet_row = report.by_facet.setdefault(facet, FacetAudit())
            facet_row.subtasks += 1

            relevant = [fact for fact in facts if _fact_relevant(fact, spec)]
            recalled_values = set(card.alignment_preferences()) | set(card.avoid)
            recalled = sum(1 for fact in relevant if fact.value in recalled_values)
            report.relevant_facts_available += len(relevant)
            report.relevant_facts_recalled += recalled
            facet_row.facts_available += len(relevant)
            facet_row.facts_recalled += recalled

            available_pool = {
                fact.value for fact in facts
                if fact.status == "active"
                and fact.polarity != "negative"
                and _valid_fact_value(fact.value)
            }
            dropped = len(available_pool - set(card.preference_pool))
            report.pool_values_available += len(available_pool)
            report.pool_values_dropped += dropped
            report.pool_truncated_subtasks += int(dropped > 0)
            facet_row.pool_values_dropped += dropped

            question = memory.propose_question(instruction, domain)
            if question:
                report.questions_proposed += 1
                facet_row.questions_proposed += 1
                dimension = question_dimension(question)
                question_counts[dimension or "unclassified"] += 1
                conflict = bool(dimension) and dimension in resolve_preference_slots(
                    spec, facts
                )
                report.known_slot_question_conflicts += int(conflict)
                facet_row.known_slot_question_conflicts += int(conflict)
                if conflict:
                    question_conflict_counts[dimension] += 1

        for fact in memory.facts:
            if fact.status == "active":
                dimension_counts[fact.dimension] += 1
                source_counts[fact.source_type or "unknown"] += 1
        report.active_facts += sum(f.status == "active" for f in memory.facts)
        report.superseded_facts += sum(f.status == "superseded" for f in memory.facts)
        report.users_at_fact_capacity += int(
            len(memory.preference_store.facts) >= memory.preference_store.max_facts
        )
        report.users_at_stream_capacity += int(len(memory.stream) >= 500)
        report.users_at_entity_capacity += int(
            len(memory.entity_index) >= memory.entity_index.max_entries
        )
        report.preference_store_entries += len(memory.preference_store.facts)
        report.entity_index_entries += len(memory.entity_index)
        report.scalar_slot_conflicts += _scalar_conflicts(memory.facts)
        report.near_duplicate_pairs += _near_duplicate_pairs(memory.facts)
    report.active_facts_by_dimension = dict(dimension_counts.most_common())
    report.active_facts_by_source_type = dict(source_counts.most_common())
    report.questions_by_dimension = dict(question_counts.most_common())
    report.known_slot_conflicts_by_dimension = dict(question_conflict_counts.most_common())
    return report


def render_markdown(report: MemoryAudit) -> str:
    data = report.to_dict()
    lines = [
        "# ADAPT Memory Audit",
        "",
        f"Cohort: `{report.cohort}`; users: {report.users}; subtasks: {report.subtasks}; model/evaluator calls: 0.",
        "",
        "> This is an observable-mechanics audit, not a benchmark score. It uses no rubric, reward, hidden intention, target ID, or target/distraction marker.",
        "",
        "## Aggregate",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Parsed signals | {report.parsed_signals} |",
        f"| Active / superseded facts | {report.active_facts} / {report.superseded_facts} |",
        f"| Users at fact / stream capacity | {report.users_at_fact_capacity} / {report.users_at_stream_capacity} |",
        f"| Users at entity-index capacity | {report.users_at_entity_capacity} |",
        f"| Preference / entity entries | {report.preference_store_entries} / {report.entity_index_entries} |",
        f"| Observable structural recall | {report.relevant_facts_recalled}/{report.relevant_facts_available} ({data['observable_recall_rate']}) |",
        f"| Proposed questions | {report.questions_proposed} |",
        f"| Known-slot question conflicts | {report.known_slot_question_conflicts} ({data['known_slot_question_conflict_rate']}) |",
        f"| Structured scope mismatches | {report.structured_scope_mismatches}/{report.structured_scope_checks} ({data['structured_scope_mismatch_rate']}) |",
        f"| Scalar slots with conflicting active values | {report.scalar_slot_conflicts} |",
        f"| Near-duplicate active fact pairs | {report.near_duplicate_pairs} |",
        f"| Subtasks with a truncated preference pool | {report.pool_truncated_subtasks}/{report.subtasks} |",
        f"| Pool-value exposures dropped by bounded card | {report.pool_values_dropped}/{report.pool_values_available} |",
        "",
        "## Composition",
        "",
        f"- Active facts by dimension: `{json.dumps(report.active_facts_by_dimension, ensure_ascii=False)}`",
        f"- Active facts by source: `{json.dumps(report.active_facts_by_source_type, ensure_ascii=False)}`",
        f"- Questions by dimension: `{json.dumps(report.questions_by_dimension, ensure_ascii=False)}`",
        f"- Known-slot conflicts by dimension: `{json.dumps(report.known_slot_conflicts_by_dimension, ensure_ascii=False)}`",
        "",
        "## By facet",
        "",
        "| Facet | Tasks | Recall | Questions | Known-slot conflicts | Pool drops |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for facet, row in sorted(report.by_facet.items()):
        lines.append(
            f"| {facet} | {row.subtasks} | {row.facts_recalled}/{row.facts_available} "
            f"({_rate(row.facts_recalled, row.facts_available)}) | {row.questions_proposed} | "
            f"{row.known_slot_question_conflicts} | {row.pool_values_dropped} |"
        )
    lines.extend([
        "",
        "## Interpretation limits",
        "",
        "- Recall is structural and observable: it asks whether relevant stored facts reach the card/alignment pool, not whether an evaluator target was selected.",
        "- Fact, recall, and pool counts across subtasks are exposure counts; parsed signals are deduplicated per user before counting.",
        "- A conflict means a semantic field already has visible positive evidence when the generic question policy asks it again; confirmation may still be appropriate in some tasks.",
        "- Counts are aggregate only. The audit intentionally emits no user-, task-, product-, or candidate-specific prescription.",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", choices=("dev", "blind", "final", "all"), default="dev")
    parser.add_argument("--json", type=Path, default=Path("data/analysis/memory_audit_dev.json"))
    parser.add_argument("--markdown", type=Path, default=Path("docs/MEMORY_AUDIT_DEV.md"))
    args = parser.parse_args()
    tasks = get_tasks("chinese")
    split = stable_user_split(tasks)
    report = audit_tasks(tasks, split[args.cohort], args.cohort)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
