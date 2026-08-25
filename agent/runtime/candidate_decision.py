"""Single, pure authority for candidate-to-action decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.runtime.bindings import CandidateBindingGraph
from agent.runtime.ranking import CandidateRanker
from agent.runtime.state import RuntimePhase


@dataclass(frozen=True)
class CandidateBinding:
    create_tool: str
    arguments: tuple[tuple[str, Any], ...]
    leaf_ids: tuple[str, ...]
    parent_ids: tuple[str, ...]
    hard_failures: tuple[str, ...]
    task_score: float
    preference_score: float
    provenance: tuple[str, ...]

    def as_arguments(self) -> dict[str, Any]:
        return dict(self.arguments)


@dataclass(frozen=True)
class EnrichmentRequest:
    tool_name: str
    arguments: tuple[tuple[str, Any], ...]

    def as_arguments(self) -> dict[str, Any]:
        return dict(self.arguments)


@dataclass(frozen=True)
class CandidateDecision:
    next_phase: RuntimePhase
    admissible: tuple[CandidateBinding, ...]
    ordered: tuple[CandidateBinding, ...]
    selected: CandidateBinding | None
    missing_arguments: tuple[str, ...]
    needs_enrichment: tuple[EnrichmentRequest, ...]
    selection_basis: str


class CandidateDecisionEngine:
    """Functional core shared by prompt, controller and preflight views."""

    def __init__(self, registry: Any) -> None:
        self.registry = registry

    def decide(
        self,
        ledger: Any,
        card: Any,
        *,
        runtime: Any | None = None,
        instruction_epoch: int = 0,
        fixed_arguments: dict[str, dict[str, Any]] | None = None,
        profile: dict[str, Any] | None = None,
    ) -> CandidateDecision:
        graph = CandidateBindingGraph.from_ledger(
            ledger, epoch=instruction_epoch
        )
        fixed_by_tool = fixed_arguments or {}
        profile_values = profile or {}
        raw_bindings: list[CandidateBinding] = []
        missing: set[str] = set()
        all_candidates = list(ledger.structural_leaf_candidates())
        ranker = CandidateRanker()
        ranked_candidates = ranker.rank(
            all_candidates, card, max(1, len(all_candidates))
        )
        rank_index = {
            candidate.candidate_id: index
            for index, candidate in enumerate(ranked_candidates)
        }
        score_components = ranker.score_candidates(all_candidates, card)

        for name, contract in sorted(self.registry.contracts.items()):
            if contract.role != "create":
                continue
            fixed = dict(fixed_by_tool.get(name, {}))
            from agent.runtime.information import (
                InformationSource,
                SchemaQuestionPlanner,
            )

            for argument in contract.arguments:
                source = SchemaQuestionPlanner.classify(contract, argument)
                if argument.name in fixed:
                    continue
                if source == InformationSource.PROFILE_RESOLVABLE:
                    user_value = profile_values.get("user_id")
                    if user_value not in (None, ""):
                        fixed[argument.name] = user_value
                elif source == InformationSource.USER_REQUIRED:
                    missing.add(argument.name)
            meta = self.registry.meta.get(name)
            for bound in graph.enumerate_bindings(
                contract, fixed_arguments=fixed
            ):
                arguments = bound.as_dict()
                failures = tuple(
                    failure
                    for failure in ledger.validate_write(
                        name,
                        arguments,
                        card,
                        profile=profile_values,
                        tool_meta=meta,
                    )
                    # Candidate admissibility is not final call validation.
                    # Non-ID action values may be bound when the proposal is
                    # prepared; _preflight still rejects an emitted WRITE
                    # when any required value is actually absent.
                    if not any(
                        failure == f"missing required argument: {argument.name}"
                        and argument.name not in contract.id_arguments
                        for argument in contract.arguments
                    )
                )
                leaf_rank = min(
                    (rank_index.get(candidate_id, 10**6) for candidate_id in bound.leaf_ids),
                    default=10**6,
                )
                component = max(
                    (
                        score_components[candidate_id]
                        for candidate_id in bound.leaf_ids
                        if candidate_id in score_components
                    ),
                    key=lambda score: (
                        score.task_relevance_band,
                        score.task_relevance,
                        score.session_correction,
                        score.historical_preference,
                    ),
                    default=None,
                )
                raw_bindings.append(
                    CandidateBinding(
                        create_tool=name,
                        arguments=bound.arguments,
                        leaf_ids=bound.leaf_ids,
                        parent_ids=bound.parent_ids,
                        hard_failures=failures,
                        task_score=(
                            float(
                                component.task_relevance_band * 1000
                                + component.task_relevance
                                - leaf_rank * 1e-6
                            )
                            if component is not None
                            else float(-leaf_rank)
                        ),
                        preference_score=(
                            float(
                                component.session_correction * 100
                                + component.historical_preference
                            )
                            if component is not None
                            else 0.0
                        ),
                        provenance=bound.provenance,
                    )
                )

        admissible = tuple(
            binding for binding in raw_bindings if not binding.hard_failures
        )
        ordered = tuple(
            sorted(
                admissible,
                key=lambda binding: (
                    binding.task_score,
                    binding.preference_score,
                    binding.create_tool,
                    binding.leaf_ids,
                ),
                reverse=True,
            )
        )
        selected: CandidateBinding | None = None
        basis = "no_admissible_binding"
        selected_id = str(getattr(runtime, "selected_candidate_id", "") or "")
        if selected_id:
            selected = next(
                (
                    binding
                    for binding in ordered
                    if selected_id in binding.leaf_ids
                ),
                None,
            )
            basis = "explicit_display_selection" if selected else "explicit_selection_not_bindable"
        elif ordered:
            selected = ordered[0]
            basis = "current_task_then_preference_order"

        enrichment = self._enrichment_requests(ledger, card)
        if not ledger.candidates:
            next_phase = RuntimePhase.SEARCH
        elif not ordered:
            next_phase = RuntimePhase.SELECT if enrichment else RuntimePhase.SELECT
        elif runtime is None:
            next_phase = RuntimePhase.SELECT
        else:
            authorization = runtime.authorization
            may_decide = bool(
                authorization.candidate_choice_authorized
                or authorization.choice_delegated
                or runtime.selection_made
                or runtime.force_decision_after_candidates
            )
            if authorization.create_authorized and may_decide and not missing:
                next_phase = RuntimePhase.READY_TO_CREATE
            else:
                next_phase = RuntimePhase.SELECT
        return CandidateDecision(
            next_phase=next_phase,
            admissible=admissible,
            ordered=ordered,
            selected=selected,
            missing_arguments=tuple(sorted(missing)),
            needs_enrichment=enrichment,
            selection_basis=basis,
        )

    def _enrichment_requests(self, ledger: Any, card: Any) -> tuple[EnrichmentRequest, ...]:
        candidates = list(ledger.candidates.values())
        ranker = CandidateRanker()
        requests: list[EnrichmentRequest] = []
        for name, contract in sorted(self.registry.contracts.items()):
            if contract.role != "enrich" or len(contract.required_id_variables) != 1:
                continue
            variable = contract.required_id_variables[0]
            parents = [
                candidate
                for candidate in candidates
                if candidate.entity_type == variable.entity_type
            ]
            for candidate in ranker.rank(parents, card, max(1, len(parents))):
                arguments: dict[str, Any] = {
                    variable.argument: (
                        [candidate.candidate_id]
                        if variable.many
                        else candidate.candidate_id
                    )
                }
                if ledger.enrichment_read_count(name, arguments):
                    continue
                requests.append(
                    EnrichmentRequest(name, tuple(sorted(arguments.items())))
                )
                if len(requests) >= min(6, len(parents)):
                    break
            if requests:
                break
        return tuple(requests)
