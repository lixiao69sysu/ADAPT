"""Retired-controller candidate ledger, split out of agent/decision.py.

**No production call site.** Every reference to ``CandidateLedger`` lives in
``agent/tests``. It is retained because those tests encode behavioural
knowledge about candidate observation, search budgeting and constraint
binding, but it is *not* an ADAPT capability and nothing on the measured path
constructs one. ``decision.py`` deliberately does not import this module: the
data layer's import closure reaches ``decision.py`` for the shared data
structures only, and the eager re-export that once dragged the retired
controller into that closure must not come back.

It was split out for this reason (E-062 / cleanup pass): keeping the forced
ordering, leader and gate logic inside the module that holds TaskSpec and
DecisionCard made the old controller easy to reintroduce. Anything revived
from here needs a zero-model reproduction and a paired measurement first.
"""

from __future__ import annotations

import json
from typing import Any

from agent.decision import (
    Candidate,
    Constraint,
    ConstraintOperator,
    ConstraintTarget,
    DecisionCard,
    _FIELD_RE,
    _ID_RE,
    _category_groundable_in_ledger,
    _category_satisfied_by_tool,
    _constraint_present,
    _dedup,
    _entity_type,
    _is_commit_tool,
    _normalize_search_arguments,
    _to_float,
    _to_int,
    _validate_argument_constraint,
)


class CandidateLedger:
    """Typed, per-subtask observations and semantic search budgets."""

    def __init__(
        self,
        max_searches_per_family: int = 3,
        max_family_searches: int = 6,
    ) -> None:
        self.candidates: dict[str, Candidate] = {}
        self.search_counts: dict[str, int] = {}
        self.search_family_counts: dict[str, int] = {}
        # Distinct queries a family may spend before the sufficiency stop takes
        # over. Exploration is a capability, not waste: the stock agent averages
        # 3.1 searches per subtask, and units it solves use several keyword
        # families before choosing (E-042).
        self.exploration_allowance = 3
        # Observable query terms of the most recent search per family. The
        # framework reuses these when it must fill an unobserved entity kind
        # (E-035) instead of inventing new keywords.
        self.last_search_arguments: dict[str, dict[str, Any]] = {}
        # Administrative units of the user's own registered address, supplied by
        # the owning agent. Used only as an observable proximity tie-break.
        self.home_tokens: list[str] = []
        self.enrichment_read_counts: dict[str, int] = {}
        self.pending_payment_ids: set[str] = set()
        self.max_searches_per_family = max_searches_per_family
        # Per-subtask cap across *distinct* queries inside one tool family.
        # Identical signatures are already capped by max_searches_per_family;
        # this bounds keyword-variant thrashing (E-031: never-solved subtasks
        # issue 2.8x the searches of always-solved ones).
        self.max_family_searches = max_family_searches
        self.require_max_preference_coverage = False
        self._turn = 0

    def reset(self) -> None:
        self.candidates.clear()
        self.search_counts.clear()
        self.search_family_counts.clear()
        self.last_search_arguments.clear()
        self.enrichment_read_counts.clear()
        self.pending_payment_ids.clear()
        self.require_max_preference_coverage = False
        self._turn = 0

    def observe(self, tool_name: str, content: str | None) -> None:
        text = content or ""
        if not text:
            return
        self._turn += 1
        context_parent_ids: list[str] = []
        for chunk in (line.strip() for line in text.splitlines() if line.strip()):
            ids = _ID_RE.findall(chunk)
            fields = {key: value.strip() for key, value in _FIELD_RE.findall(chunk)}
            explicit_parents = [
                item
                for item in ids
                if _entity_type(item) in {"hotel", "attraction", "flight", "train"}
            ]
            if explicit_parents:
                context_parent_ids = explicit_parents
            name = next(
                (
                    fields[k]
                    for k in (
                        "product_name",
                        "hotel_name",
                        "attraction_name",
                        "shop_name",
                        "store_name",
                        "train_number",
                        "flight_number",
                        "room_type",
                        "seat_type",
                        "name",
                    )
                    if k in fields
                ),
                "",
            )
            price = _to_float(fields.get("price"))
            inventory = _to_int(fields.get("quantity"))
            for candidate_id in ids:
                parent_ids = [item for item in ids if item != candidate_id]
                if _entity_type(candidate_id) == "product":
                    parent_ids = _dedup([*parent_ids, *context_parent_ids])
                enriched_raw = chunk
                if parent_ids and _entity_type(candidate_id) == "product":
                    enriched_raw += ", parent_ids=" + "|".join(parent_ids)
                self.candidates[candidate_id] = Candidate(
                    candidate_id,
                    _entity_type(candidate_id),
                    name,
                    enriched_raw,
                    tool_name,
                    fields,
                    parent_ids,
                    price,
                    inventory,
                    self._turn,
                )
        if "status:unpaid" in text or "status=unpaid" in text:
            self.pending_payment_ids.update(
                item for item in _ID_RE.findall(text) if item.startswith("O")
            )
        if "Payment successful" in text or "支付成功" in text:
            self.pending_payment_ids.clear()
        if "cancel" in tool_name.lower() and not any(
            marker in text.lower() for marker in ("fail", "error", "失败")
        ):
            returned_ids = set(_ID_RE.findall(text))
            if returned_ids:
                self.pending_payment_ids.difference_update(returned_ids)
            else:
                self.pending_payment_ids.clear()

    @staticmethod
    def search_family(tool_name: str) -> str:
        lowered = tool_name.lower()
        if "product" in lowered:
            return "product_search"
        if "hotel" in lowered:
            return "hotel_search"
        if "attraction" in lowered:
            return "attraction_search"
        if "flight" in lowered:
            return "flight_search"
        if "train" in lowered:
            return "train_search"
        if "shop" in lowered or "store" in lowered:
            return "merchant_search"
        return tool_name

    def signature(self, tool_name: str, arguments: dict[str, Any]) -> str:
        return f"{self.search_family(tool_name)}:{json.dumps(_normalize_search_arguments(arguments), sort_keys=True, ensure_ascii=False)}"

    def register_search(self, tool_name: str, arguments: dict[str, Any]) -> int:
        signature = self.signature(tool_name, arguments)
        family = self.search_family(tool_name)
        self.search_counts[signature] = self.search_counts.get(signature, 0) + 1
        self.search_family_counts[family] = self.search_family_counts.get(family, 0) + 1
        self.last_search_arguments[family] = dict(arguments)
        return self.search_counts[signature]

    def register_enrichment_read(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> int:
        signature = f"{tool_name}:{json.dumps(arguments, sort_keys=True, ensure_ascii=False)}"
        self.enrichment_read_counts[signature] = (
            self.enrichment_read_counts.get(signature, 0) + 1
        )
        return self.enrichment_read_counts[signature]

    def search_allowed(self, tool_name: str) -> bool:
        # Arguments are not known while building the tool list. Keep the search
        # tool available and enforce the normalized signature in preflight.
        return True

    def search_budget_rejection(
        self, tool_name: str, *, execution_ready: bool
    ) -> str | None:
        """Deterministic gate for a proposed search call in this subtask.

        Two general rules, both structural (no user, task or candidate
        specifics):

        1. Distinct-query budget per tool family - stops keyword-variant
           thrashing inside one family (identical signatures are already
           capped by ``max_searches_per_family``).
        2. Sufficiency stop - once the ledger already holds a compliant
           candidate that CREATE could use *and* the agent has already explored
           the family, searching again cannot improve the outcome of this
           subtask; the agent must select and act instead.

        The sufficiency stop deliberately does **not** fire on the first
        candidate set. Trace comparison against the stock agent showed that
        firing it immediately cost the model the multi-keyword exploration it
        uses to ground a choice (ADAPT searched 1.0 times per subtask against
        stock's 3.1, and lost units where stock searched several keyword
        families before choosing) (E-042).

        Returns a rejection reason, or ``None`` when the search is allowed.
        Callers must register the search attempt before calling this so that
        counts include the current proposal, matching the signature budget.
        """
        family = self.search_family(tool_name)
        family_count = self.search_family_counts.get(family, 0)
        if family_count > self.max_family_searches:
            return (
                f"{family} search budget exhausted after "
                f"{family_count - 1} distinct attempts in this subtask; "
                "work with the candidates already in the ledger"
            )
        if (
            execution_ready
            and self.candidates
            and family_count > self.exploration_allowance
        ):
            return (
                "the candidate ledger already contains a compliant candidate "
                "for this subtask; do not search again - select it and proceed "
                "to the create/pay action"
            )
        return None

    def selected_candidates(self, arguments: dict[str, Any]) -> list[Candidate]:
        selected: list[Candidate] = []
        for key, value in arguments.items():
            if key == "user_id" or not key.endswith(("_id", "_ids")):
                continue
            for item in value if isinstance(value, list) else [value]:
                if str(item) in self.candidates:
                    selected.append(self.candidates[str(item)])
        return selected

    def constraint_candidates(self, arguments: dict[str, Any]) -> list[Candidate]:
        """Return the purchased/booked entity, excluding relational parent IDs.

        Product search rows repeat a store ID for every product.  The ledger's
        store entry therefore represents only the last observed row, not the
        product being purchased.  Candidate constraints must apply to the
        product ID (or hotel/flight/etc. ID), while the store ID is validated
        only for provenance and type.
        """
        selected = self.selected_candidates(arguments)
        for entity_type in (
            "product",
            "hotel",
            "attraction",
            "flight",
            "train",
            "shop",
            "store",
        ):
            typed = [candidate for candidate in selected if candidate.entity_type == entity_type]
            if typed:
                return typed
        return selected

    def shortlist(self, card: DecisionCard, limit: int = 5) -> list[Candidate]:
        candidates = [
            c
            for c in self.candidates.values()
            if c.entity_type not in {"order", "unknown"}
        ]
        for entity_type in (
            "product",
            "hotel",
            "attraction",
            "flight",
            "train",
            "shop",
        ):
            typed = [
                candidate
                for candidate in candidates
                if candidate.entity_type == entity_type
            ]
            if typed:
                candidates = typed
                break
        from agent.runtime.location import location_rank
        from agent.runtime.ranking import CandidateRanker

        parent_ranks = {
            candidate.candidate_id: location_rank(candidate, self.home_tokens)
            for candidate in self.candidates.values()
            if candidate.entity_type in {"shop", "store", "hotel"}
        }
        return CandidateRanker().rank(
            candidates,
            card,
            limit,
            location_tokens=self.home_tokens,
            parent_ranks=parent_ranks,
        )

    def unique_evidence_leader(self, card: DecisionCard) -> Candidate | None:
        """Return a candidate only when observable preference evidence is decisive.

        The scalar ranker is useful for ordering, but price and recency must not
        silently become execution policy.  A framework lock is therefore
        allowed only when the best compliant candidate matches strictly more
        current-card preferences than every runner-up and matches at least one
        preference.  Ties and evidence-free shortlists remain model decisions.
        """
        from agent.runtime.ranking import CandidateRanker

        ranked = self.shortlist(card, limit=8)
        if not ranked:
            return None
        ranker = CandidateRanker()
        score_by_id = ranker.decisive_preference_scores(ranked, card)
        scores = [score_by_id.get(candidate.candidate_id, 0.0) for candidate in ranked]
        if scores[0] <= 0:
            return None
        runner_up = max(scores[1:], default=-1.0)
        return ranked[0] if scores[0] > runner_up else None

    def preference_coverage_gap(
        self, arguments: dict[str, Any], card: DecisionCard
    ) -> str:
        """Describe an observable lower-evidence choice without hidden labels."""
        chosen = self.constraint_candidates(arguments)
        ranked = self.shortlist(card, limit=8)
        if not chosen or not ranked or not card.alignment_preferences():
            return ""
        from agent.runtime.ranking import CandidateRanker

        alignment = CandidateRanker.preference_alignment(ranked, card)
        scores = {
            candidate.candidate_id: alignment.decisive_score(candidate)
            for candidate in ranked
        }
        chosen_score = scores.get(chosen[0].candidate_id, 0.0)
        best_score = max(scores.values(), default=0.0)
        if best_score <= chosen_score:
            return ""
        best_ids = [
            candidate.candidate_id
            for candidate in ranked
            if scores.get(candidate.candidate_id, 0.0) == best_score
        ]
        return (
            f"selected candidate has observable preference score {chosen_score:.2f}, "
            f"while observed candidates {best_ids} score {best_score:.2f}; "
            "choose from the maximum observable preference-coverage set"
        )

    def evidence_leaders(self, card: DecisionCard, limit: int = 8) -> list[Candidate]:
        """Candidates tied at the maximum *decisive* preference coverage.

        Used by the learned preference-grounding policy to name the best
        evidence-supported candidate; it is a control on which candidate the
        framework points at, never a veto on writing (E-049).
        """
        ranked = self.shortlist(card, limit=limit)
        if not ranked:
            return []
        from agent.runtime.ranking import CandidateRanker

        scores = CandidateRanker.decisive_preference_scores(ranked, card)
        best = max(scores.values(), default=0.0)
        if best <= 0:
            return []
        return [
            candidate
            for candidate in ranked
            if scores.get(candidate.candidate_id, 0.0) == best
        ]

    def validate_ranked_choice(
        self,
        arguments: dict[str, Any],
        card: DecisionCard,
        selected_candidate_id: str = "",
    ) -> list[str]:
        """Keep WRITE inside the observed set without taking the choice over.

        Ranking is a retrieval aid, not an oracle. Only an explicit user
        selection is a real constraint and is enforced; a merely leading
        preference score and a position inside the rendered top-8 are not.

        The positional form was a veto in disguise: a candidate the model had
        genuinely observed, but which ranked ninth, produced a preflight
        rejection, three replans and sometimes a terminal refusal. Divergence is
        still observable through ``preference_leader_diverged`` and
        ``shortlist_position_diverged``; the model chooses among compliant
        observed candidates (E-045, E-049).
        """
        chosen = self.constraint_candidates(arguments)
        if not chosen:
            return []
        chosen_id = chosen[0].candidate_id
        if selected_candidate_id and chosen_id != selected_candidate_id:
            expected = self.candidates.get(selected_candidate_id)
            expected_name = expected.name if expected else selected_candidate_id
            return [
                f"selected {chosen_id}, but the user explicitly selected "
                f"{selected_candidate_id} ({expected_name}); use that exact ID"
            ]
        return []

    def shortlist_position(
        self, candidate_id: str, card: DecisionCard, limit: int = 8
    ) -> int:
        """1-based position of an observed candidate in the rendered shortlist."""
        for index, candidate in enumerate(self.shortlist(card, limit=limit), 1):
            if candidate.candidate_id == candidate_id:
                return index
        return 0

    def render(
        self, card: DecisionCard | None = None, limit: int = 8, max_chars: int = 4200
    ) -> str:
        selected = self.shortlist(card or DecisionCard(), limit)
        if not selected:
            return ""
        lines = ["## Candidate shortlist (only these observed IDs may be selected)"]
        from agent.runtime.ranking import CandidateRanker

        alignment = CandidateRanker.preference_alignment(selected, card or DecisionCard())
        for index, candidate in enumerate(selected, 1):
            matched = [atom.value for atom in alignment.matches(candidate)]
            evidence = (
                f"; preference_evidence={matched}"
                if matched
                else "; preference_evidence=[]"
            )
            lines.append(
                f"{index}. {candidate.candidate_id} [{candidate.entity_type}] "
                f"{candidate.name}{evidence}: {candidate.raw[:420]}"
            )
        if selected and selected[0].entity_type == "product":
            expanded_parents = {
                parent_id
                for candidate in self.candidates.values()
                if candidate.entity_type == "product"
                for parent_id in candidate.parent_ids
            }
            parent_candidates = [
                candidate
                for candidate in self.candidates.values()
                if candidate.entity_type
                in {"hotel", "attraction", "flight", "train"}
                and candidate.candidate_id not in expanded_parents
            ]
            if parent_candidates:
                from agent.runtime.ranking import CandidateRanker

                unexpanded = CandidateRanker().rank(parent_candidates, card or DecisionCard(), 8)
                lines.append("## Unexpanded parent candidates (READ details before CREATE)")
                for candidate in unexpanded:
                    lines.append(
                        f"- {candidate.candidate_id} [{candidate.entity_type}] "
                        f"{candidate.name}: {candidate.raw[:320]}"
                    )
        if self.pending_payment_ids:
            lines.append(
                "PENDING_PAYMENT: " + ", ".join(sorted(self.pending_payment_ids))
            )
        return "\n".join(lines)[:max_chars]

    def validate_write(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        card: DecisionCard,
        profile: dict[str, Any] | None = None,
    ) -> list[str]:
        if not _is_commit_tool(tool_name):
            return []
        errors: list[str] = []
        for key, value in arguments.items():
            if key == "user_id" or not key.endswith(("_id", "_ids")):
                continue
            for item in value if isinstance(value, list) else [value]:
                item_text = str(item)
                if (
                    item_text not in self.candidates
                    and item_text not in self.pending_payment_ids
                ):
                    errors.append(
                        f"{key}={item_text} was not returned by a tool in this subtask"
                    )
                candidate = self.candidates.get(item_text)
                expected_type = key.removesuffix("_ids").removesuffix("_id")
                compatible = {
                    "shop": {"shop"},
                    "store": {"store"},
                    "product": {"product"},
                    "hotel": {"hotel"},
                    "attraction": {"attraction"},
                    "flight": {"flight"},
                    "train": {"train"},
                    "order": {"order"},
                    "room": {"product"},
                    "ticket": {"product"},
                    "seat": {"product"},
                }
                if (
                    candidate
                    and expected_type in compatible
                    and candidate.entity_type not in compatible[expected_type]
                ):
                    errors.append(
                        f"{key} expects {expected_type} ID but {item_text} is {candidate.entity_type}"
                    )
        errors.extend(self._validate_parent_relationships(arguments))
        # Payment consumes an already-created order. Product, room, date and
        # address constraints were validated before CREATE and are not fields
        # of PAY tools; reapplying them here produces impossible requirements.
        if tool_name.startswith("pay_"):
            return _dedup(errors)
        constraint_candidates = self.constraint_candidates(arguments)
        selected_text = "\n".join(c.raw for c in constraint_candidates)
        constraints = card.constraints or [
            *[Constraint("legacy", value) for value in card.must],
            *[
                Constraint("legacy", value, operator=ConstraintOperator.EXCLUDES)
                for value in card.avoid
            ],
        ]
        for constraint in constraints:
            if not constraint.hard or constraint.target == ConstraintTarget.WORKFLOW:
                continue
            if constraint.target == ConstraintTarget.CANDIDATE:
                if constraint.operator == ConstraintOperator.EXCLUDES:
                    from agent.runtime.ranking import violates_exclusion

                    if constraint.value and violates_exclusion(
                        selected_text, constraint.value, card.prefer
                    ):
                        errors.append(
                            f"selected candidate contains forbidden value: {constraint.value}"
                        )
                elif (
                    constraint.value
                    and not _category_satisfied_by_tool(
                        constraint.kind, constraint.value, tool_name
                    )
                    and not (
                        constraint.kind == "category"
                        and not _category_groundable_in_ledger(
                            constraint.value, self.candidates.values()
                        )
                    )
                    and not _constraint_present(constraint.value, selected_text)
                ):
                    errors.append(
                        f"selected candidate does not show required value: {constraint.value}"
                    )
            else:
                errors.extend(
                    _validate_argument_constraint(
                        constraint,
                        arguments,
                        profile or {},
                        selected_text=selected_text,
                    )
                )
        if any(c.inventory == 0 for c in constraint_candidates):
            errors.append("selected candidate has zero inventory")
        selected_ids = {
            str(item)
            for key, value in arguments.items()
            if key.endswith(("_id", "_ids")) and key != "user_id"
            for item in (value if isinstance(value, list) else [value])
        }
        selected = [
            self.candidates[item] for item in selected_ids if item in self.candidates
        ]
        products = [
            candidate for candidate in selected if candidate.entity_type == "product"
        ]
        stores = {
            candidate.candidate_id
            for candidate in selected
            if candidate.entity_type == "store"
        }
        for product in products:
            parent_stores = {
                item for item in product.parent_ids if _entity_type(item) == "store"
            }
            if stores and parent_stores and stores.isdisjoint(parent_stores):
                errors.append(
                    f"product {product.candidate_id} does not belong to selected store"
                )
        return _dedup(errors)

    def _validate_parent_relationships(self, arguments: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        relationships = (
            ("room_id", "hotel_id"),
            ("ticket_id", "attraction_id"),
            ("product_id", "shop_id"),
        )
        for child_key, parent_key in relationships:
            child_id = arguments.get(child_key)
            parent_id = arguments.get(parent_key)
            child = self.candidates.get(str(child_id)) if child_id else None
            if (
                child
                and parent_id
                and child.parent_ids
                and str(parent_id) not in child.parent_ids
            ):
                errors.append(
                    f"{child_key}={child_id} was not observed under {parent_key}={parent_id}"
                )
        for child_key, possible_parents in (
            ("seat_id", ("flight_id", "train_id")),
            ("product_ids", ("store_id", "shop_id")),
        ):
            child_values = arguments.get(child_key, [])
            if not isinstance(child_values, list):
                child_values = [child_values]
            parent_id = next(
                (arguments.get(key) for key in possible_parents if arguments.get(key)),
                None,
            )
            for child_id in child_values:
                child = self.candidates.get(str(child_id)) if child_id else None
                if (
                    child
                    and parent_id
                    and child.parent_ids
                    and str(parent_id) not in child.parent_ids
                ):
                    errors.append(
                        f"{child_key}={child_id} was not observed under parent={parent_id}"
                    )
        return errors
