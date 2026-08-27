"""Three explicit authority centers for ADAPT's runtime architecture."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SemanticMemoryStore:
    """Typed access to the sole preference fact/evidence authority."""

    memory: Any

    def retrieve(
        self,
        *,
        task_scope: str = "",
        candidate_fields: tuple[str, ...] = (),
        unresolved_dimensions: tuple[str, ...] = (),
        evidence_required: bool = True,
        limit: int = 8,
    ):
        return self.memory.retrieve_facts(
            task_scope=task_scope,
            candidate_fields=candidate_fields,
            unresolved_dimensions=unresolved_dimensions,
            evidence_required=evidence_required,
            limit=limit,
        )


@dataclass(frozen=True)
class DecisionRuntime:
    """Current-task decision authority; all members are shared references."""

    task_spec: Any
    search_plan: Any
    runtime: Any
    ledger: Any
    responses: Any


@dataclass(frozen=True)
class ExecutionSafetyKernel:
    """Only authority allowed to approve and journal external side effects."""

    runtime: Any
    tool_registry: Any
    operations: Any
    lineage: Any
    tool_errors: Any

