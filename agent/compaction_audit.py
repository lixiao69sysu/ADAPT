"""Counterfactual, zero-model audit of long-horizon memory compaction.

Three policies consume the same unbounded observable fact universe:

1. current: one mixed store, current 500-entry pruning order;
2. protected_aggregate: protected preference store plus an exact-normalized
   entity evidence index;
3. protected_aggregate_fair: the same split with a 500-entry entity index
   selected fairly across observed scope/facet/dimension/category buckets.

Only aggregate metrics are emitted. No evaluator or hidden task fields are read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from agent.vitabench_bootstrap import enable_vitabench_utf8

enable_vitabench_utf8()

from vita.domains.personalization.environment import get_tasks

from agent.decision import Candidate, TaskSpec
from agent.memory.drift import DriftDetector
from agent.memory.entity_index import (
    ENTITY_DIMENSIONS,
    aggregate_entity_facts,
    entity_fact_key,
    fair_entity_selection,
    normalize_entity,
)
from agent.memory.fact_store import FactStore
from agent.memory.facts import PreferenceFact, fact_from_signal
from agent.memory.grounding import ground_facts_to_candidates
from agent.memory.proactive import ProactiveEngine
from agent.memory.signals import SignalParser
from agent.memory.slots import resolve_preference_slots
from agent.memory_audit import question_dimension
from agent.vitabench_runner import SPLIT_SEED, stable_user_split


_PROTECTED_DIMENSIONS = {
    "safety", "avoid", "explicit", "conditional", "temperature",
    "sweetness", "taste", "topping", "room_type", "transport", "budget",
    "time", "caffeine", "attribute", "location", "size", "color", "seat",
}


@dataclass
class PolicyMetrics:
    preference_store_entries: int = 0
    entity_index_entries: int = 0
    users_preference_store_at_500: int = 0
    users_entity_index_at_500: int = 0
    protected_available: int = 0
    protected_retained: int = 0
    safety_available: int = 0
    safety_retained: int = 0
    explicit_available: int = 0
    explicit_retained: int = 0
    conditional_available: int = 0
    conditional_retained: int = 0
    groundable_available: int = 0
    groundable_retained: int = 0
    questions_proposed: int = 0
    known_slot_question_conflicts: int = 0
    known_slot_resolutions: int = 0
    max_live_entries_per_user: int = 0

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["protected_retention_rate"] = _rate(
            self.protected_retained, self.protected_available
        )
        result["safety_retention_rate"] = _rate(
            self.safety_retained, self.safety_available
        )
        result["explicit_retention_rate"] = _rate(
            self.explicit_retained, self.explicit_available
        )
        result["conditional_retention_rate"] = _rate(
            self.conditional_retained, self.conditional_available
        )
        result["candidate_groundable_recall"] = _rate(
            self.groundable_retained, self.groundable_available
        )
        return result


@dataclass
class CompactionAudit:
    cohort: str
    users: int
    subtasks: int
    split_seed: str = SPLIT_SEED
    policies: dict[str, PolicyMetrics] = field(default_factory=dict)
    universe_active_facts: int = 0
    universe_entity_entries: int = 0

    def to_dict(self) -> dict[str, Any]:
        policies = {name: metrics.to_dict() for name, metrics in self.policies.items()}
        current_recall = policies["current"]["candidate_groundable_recall"]
        eligible = []
        for name, metrics in policies.items():
            if name == "current":
                continue
            if (
                metrics["users_preference_store_at_500"] < policies["current"]["users_preference_store_at_500"]
                and metrics["safety_retention_rate"] == 1.0
                and metrics["explicit_retention_rate"] == 1.0
                and metrics["conditional_retention_rate"] == 1.0
                and (metrics["candidate_groundable_recall"] or 0.0) >= (current_recall or 0.0)
                and metrics["known_slot_question_conflicts"] == 0
            ):
                eligible.append(name)
        return {
            "cohort": self.cohort,
            "split_seed": self.split_seed,
            "users": self.users,
            "subtasks": self.subtasks,
            "universe_active_facts": self.universe_active_facts,
            "universe_entity_entries": self.universe_entity_entries,
            "policies": policies,
            "eligible_policies": eligible,
            "recommended_policy": (
                "protected_aggregate_fair"
                if "protected_aggregate_fair" in eligible
                else (eligible[0] if eligible else None)
            ),
            "data_access": {
                "inputs": ["task.id", "subtask.subtask_id", "subtask.domain", "subtask.instruction", "subtask.interactions"],
                "forbidden": ["evaluation_criteria", "user_intention", "skill_tested", "reward", "target_product_ids", "target/distraction markers"],
                "model_calls": 0,
                "evaluator_calls": 0,
                "per_user_findings_emitted": False,
                "grounding_proxy": "observable entity fields from interaction history replayed as candidate names",
            },
        }


def current_selection(facts: Iterable[PreferenceFact], limit: int = 500) -> list[PreferenceFact]:
    return sorted(
        facts,
        key=lambda fact: (
            fact.status == "active", fact.confidence, fact.observed_at
        ),
        reverse=True,
    )[:limit]


def _protected(facts: Iterable[PreferenceFact]) -> list[PreferenceFact]:
    return [
        fact for fact in facts
        if fact.status == "active"
        and (
            fact.polarity == "negative"
            or fact.dimension in _PROTECTED_DIMENSIONS
            or fact.source_type == "conversation"
        )
        and fact.dimension not in ENTITY_DIMENSIONS
    ]


def _preference_store_facts(
    facts: Iterable[PreferenceFact],
) -> list[PreferenceFact]:
    """Keep every non-entity dimension, including future open-world fields."""
    return [
        fact for fact in facts
        if fact.status == "active" and fact.dimension not in ENTITY_DIMENSIONS
    ]


def _policy_views(
    universe: list[PreferenceFact], policy: str
) -> tuple[list[PreferenceFact], list[PreferenceFact]]:
    if policy == "current":
        return current_selection(universe), []
    protected = _preference_store_facts(universe)
    entities = aggregate_entity_facts(universe)
    if policy == "protected_aggregate_fair":
        entities = fair_entity_selection(entities, 500)
    return protected, entities


def _fresh_interactions(interactions: Iterable[Any], seen: set[str]) -> list[Any]:
    fresh = []
    for interaction in interactions:
        encoded = json.dumps(interaction, ensure_ascii=False, sort_keys=True, default=str)
        fingerprint = hashlib.sha1(encoded.encode("utf-8")).hexdigest()
        if fingerprint not in seen:
            seen.add(fingerprint)
            fresh.append(interaction)
    return fresh


def _ingest_unbounded(
    store: FactStore, drift: DriftDetector, signals: Iterable[Any], start_index: int
) -> int:
    index = start_index
    for signal in signals:
        if signal.predicate == "raw_observation":
            continue
        fact = fact_from_signal(signal, f"counterfactual-{index}")
        store.ingest(fact, confirmed_drift=bool(drift.observe(signal)))
        index += 1
    return index


def _pseudo_candidates(entity_facts: Iterable[PreferenceFact]) -> list[Candidate]:
    values = []
    seen = set()
    for fact in entity_facts:
        normalized = normalize_entity(fact.value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        values.append(fact.value)
    return [
        Candidate(
            candidate_id=f"OBS{index}",
            entity_type="product",
            name=value,
            raw=f"name={value}",
            tool_name="observable_history_replay",
        )
        for index, value in enumerate(values)
    ]


def _fact_keys(facts: Iterable[PreferenceFact]) -> set[tuple[str, str, str, str, str, str]]:
    return {entity_fact_key(fact) for fact in facts if fact.status == "active"}


def _update_retention(
    metrics: PolicyMetrics,
    universe: list[PreferenceFact],
    retained: list[PreferenceFact],
    pseudo_candidates: list[Candidate],
) -> None:
    retained_keys = _fact_keys(retained)
    protected = _protected(universe)
    metrics.protected_available += len(_fact_keys(protected))
    metrics.protected_retained += len(_fact_keys(protected) & retained_keys)
    for dimension in ("safety", "explicit", "conditional"):
        available = {
            entity_fact_key(fact) for fact in universe
            if fact.status == "active" and fact.dimension == dimension
        }
        kept = len(available & retained_keys)
        setattr(metrics, f"{dimension}_available", getattr(metrics, f"{dimension}_available") + len(available))
        setattr(metrics, f"{dimension}_retained", getattr(metrics, f"{dimension}_retained") + kept)

    groundable_universe = ground_facts_to_candidates(universe, pseudo_candidates)
    groundable_retained = ground_facts_to_candidates(retained, pseudo_candidates)
    metrics.groundable_available += len(_fact_keys(groundable_universe))
    metrics.groundable_retained += len(_fact_keys(groundable_retained))


def audit_compaction(
    tasks: Iterable[Any], selected_ids: Iterable[str], cohort: str = "dev"
) -> CompactionAudit:
    policies = {
        name: PolicyMetrics()
        for name in ("current", "protected_aggregate", "protected_aggregate_fair")
    }
    selected = set(selected_ids)
    parser = SignalParser()
    users = 0
    subtasks = 0
    universe_active = 0
    universe_entities = 0

    for task in tasks:
        task_id = task.id
        if task_id not in selected:
            continue
        users += 1
        seen: set[str] = set()
        store = FactStore(max_facts=100_000)
        drift = DriftDetector()
        evidence_index = 0
        for subtask in task.subtasks:
            subtasks += 1
            instruction = subtask.instruction
            domain = subtask.domain
            interactions = subtask.interactions
            fresh = _fresh_interactions(interactions, seen)
            evidence_index = _ingest_unbounded(
                store, drift, parser.parse(fresh), evidence_index
            )
            universe_snapshot = list(store.facts)
            spec = TaskSpec.compile(instruction)
            spec.domain = domain
            for policy_name, metrics in policies.items():
                preference_store, entity_index = _policy_views(
                    universe_snapshot, policy_name
                )
                live = [*preference_store, *entity_index]
                known_slots = resolve_preference_slots(spec, live)
                metrics.known_slot_resolutions += len(known_slots)
                question = ProactiveEngine().propose_question(
                    instruction,
                    "No user preference information available yet.",
                    domain,
                    known_slots=known_slots,
                )
                if question:
                    metrics.questions_proposed += 1
                    dimension = question_dimension(question)
                    metrics.known_slot_question_conflicts += int(
                        bool(dimension) and dimension in known_slots
                    )

        universe = list(store.facts)
        active_universe = [fact for fact in universe if fact.status == "active"]
        universe_active += len(active_universe)
        aggregated_entities = aggregate_entity_facts(active_universe)
        universe_entities += len(aggregated_entities)
        pseudo_candidates = _pseudo_candidates(aggregated_entities)
        for policy_name, metrics in policies.items():
            preference_store, entity_index = _policy_views(universe, policy_name)
            live = [*preference_store, *entity_index]
            metrics.preference_store_entries += len(preference_store)
            metrics.entity_index_entries += len(entity_index)
            metrics.max_live_entries_per_user = max(
                metrics.max_live_entries_per_user,
                len(preference_store) + len(entity_index),
            )
            metrics.users_preference_store_at_500 += int(len(preference_store) >= 500)
            metrics.users_entity_index_at_500 += int(len(entity_index) >= 500)
            _update_retention(metrics, universe, live, pseudo_candidates)

    return CompactionAudit(
        cohort=cohort,
        users=users,
        subtasks=subtasks,
        policies=policies,
        universe_active_facts=universe_active,
        universe_entity_entries=universe_entities,
    )


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def render_markdown(report: CompactionAudit) -> str:
    data = report.to_dict()
    lines = [
        "# ADAPT Counterfactual Memory Compaction Audit",
        "",
        f"Cohort: `{report.cohort}`; users: {report.users}; subtasks: {report.subtasks}; model/evaluator calls: 0.",
        "",
        "> This is an observable counterfactual replay, not a benchmark score. Historical structured entity fields are replayed as candidate vocabulary; no hidden fields are accessed.",
        "",
        "| Policy | Preference entries | Entity entries | Max live/user | Users pref>=500 | Protected | Safety | Explicit | Conditional | Groundable recall | Known-slot conflicts |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, metrics in data["policies"].items():
        lines.append(
            f"| {name} | {metrics['preference_store_entries']} | {metrics['entity_index_entries']} | "
            f"{metrics['max_live_entries_per_user']} | {metrics['users_preference_store_at_500']} | {metrics['protected_retention_rate']} | "
            f"{metrics['safety_retention_rate']} | {metrics['explicit_retention_rate']} | "
            f"{metrics['conditional_retention_rate']} | {metrics['candidate_groundable_recall']} | "
            f"{metrics['known_slot_question_conflicts']} |"
        )
    lines.extend([
        "",
        f"Eligible policies: `{', '.join(data['eligible_policies']) or 'none'}`",
        f"Recommended policy: `{data['recommended_policy'] or 'none'}`",
        "",
        "## Limits",
        "",
        "- Groundable recall is a zero-model proxy over entity fields actually visible in interaction history; it does not claim recall over unseen catalog synonyms.",
        "- `protected_aggregate` leaves the entity index unbounded. `protected_aggregate_fair` caps it at 500 entries per active user using semantic-bucket water filling.",
        "- Every non-entity or future unknown dimension remains in PreferenceStore; compaction never silently drops it merely because its schema is new.",
        "- No policy is installed into runtime by this audit.",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", choices=("dev", "blind", "final", "all"), default="dev")
    parser.add_argument("--json", type=Path, default=Path("data/analysis/memory_compaction_audit_dev.json"))
    parser.add_argument("--markdown", type=Path, default=Path("docs/MEMORY_COMPACTION_AUDIT_DEV.md"))
    args = parser.parse_args()
    tasks = get_tasks("chinese")
    split = stable_user_split(tasks)
    report = audit_compaction(tasks, split[args.cohort], args.cohort)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
