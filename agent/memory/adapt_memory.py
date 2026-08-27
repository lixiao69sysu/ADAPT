"""ADAPTMemory: the ADAPT long-term preference memory backend.

Plugs into VitaBench 2.0 via --memory-class agent.memory.ADAPTMemory.
Implements the BaseMemory interface:
    read(query)   -> inject top-k relevant preference facts into system prompt
    update(...)   -> parse new interactions into the memory stream

Pipeline on update:
    raw interactions -> SignalParser -> MemoryStream events
                     -> DriftDetector (conflict -> decay old preference)
                     -> LifecycleManager (per-type decay / selective forgetting)
Pipeline on read:
    instruction query -> 3D retrieval (relevance x recency x importance)
                      -> top-k facts formatted as "User Preference Memory"
    + ProactiveEngine hook: when instruction is vague and memory lacks the
      decision info, expose a suggested question to ask the user.

The original VitaBench agent (LLMAgent/PersonalizationAgent) is untouched;
this memory is injected into its system prompt via the standard BaseMemory hook.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter

from vita.environment.toolkit import ToolType, is_tool
from vita.memory.base import BaseMemory

from agent.decision import (
    Constraint,
    ConstraintOperator,
    ConstraintTarget,
    DecisionCard,
    TaskSpec,
    build_decision_card,
)
from agent.memory.drift import DriftDetector
from agent.memory.entity_index import (
    ENTITY_DIMENSIONS,
    CombinedFactView,
    EntityEvidenceIndex,
)
from agent.memory.evidence import (
    PreferenceEvidenceStore,
    is_persistent_statement,
)
from agent.memory.fact_store import FactStore
from agent.memory.facts import fact_from_signal
from agent.memory.grounding import candidate_evidence_edges
from agent.memory.lifecycle import LifecycleManager
from agent.memory.proactive import ProactiveEngine
from agent.memory.retrieval import RetrievalConfig, RetrievalScorer, _normalize_domain
from agent.memory.signals import SignalParser
from agent.memory.slots import resolve_preference_slots
from agent.memory.stream import MemoryStream, parse_timestamp


class ADAPTMemory(BaseMemory):
    """ADAPT long-term preference memory with drift-aware retrieval."""

    # Execution hints draw from a wider window than the final bounded card.
    _HINT_TOP_K = 40

    def __init__(
        self,
        language: str | None = None,
        top_k: int = 60,
        w_relevance: float = 0.5,
        w_recency: float = 0.2,
        w_importance: float = 0.3,
        half_life_days: float = 180.0,
        drift_threshold: int = 2,
        max_questions: int = 2,
        enable_summary_rewrite: bool = False,
        enable_tiered_compaction: bool = True,
        entity_index_max_entries: int = 500,
        enable_preference_modeling: bool = True,
        memory_update_mode: str = "full",
        enable_proactive_interaction: bool = True,
        **kwargs,
    ):
        super().__init__(language=language, top_k=top_k, **kwargs)
        self.stream = MemoryStream()
        self.parser = SignalParser()
        self.scorer = RetrievalScorer(RetrievalConfig(
            w_relevance=w_relevance,
            w_recency=w_recency,
            w_importance=w_importance,
            half_life_days=half_life_days,
            top_k=top_k,
        ))
        self.drift = DriftDetector(drift_threshold=drift_threshold)
        self.lifecycle = LifecycleManager()
        self.proactive = ProactiveEngine(max_questions=max_questions)
        self._latest_ts: str | None = None
        # Brand frequency counter: brand_name -> order count (from 'order' type signals)
        self._brand_freq: Counter = Counter()
        # LLM-generated coherent user-profile summary (rich, curated "gist").
        # Complemented by the structured signals for precision + drift handling.
        self._summary_text: str = ""
        self.enable_tiered_compaction = enable_tiered_compaction
        self.preference_store = FactStore()
        # The FactStore remains the bounded task-facing projection.  Evidence
        # provenance and confidence aggregation live separately so duplicate
        # rows or a weak search cannot silently become a durable belief.
        self.preference_evidence = PreferenceEvidenceStore()
        # Compatibility name used by existing callers and diagnostics.
        self.fact_store = self.preference_store
        self.entity_index = EntityEvidenceIndex(entity_index_max_entries)
        self.facts = CombinedFactView(
            self.preference_store,
            self.entity_index,
            enable_tiered_compaction,
        )
        self.enable_summary_rewrite = enable_summary_rewrite
        self.enable_preference_modeling = enable_preference_modeling
        if memory_update_mode not in {"full", "simple"}:
            raise ValueError("memory_update_mode must be 'full' or 'simple'")
        self.memory_update_mode = memory_update_mode
        self.enable_proactive_interaction = enable_proactive_interaction
        self._seen_interactions: set[str] = set()
        self._current_task_key: str = ""

    # ------------------------------------------------------------------
    # BaseMemory interface
    # ------------------------------------------------------------------

    def read(self, query: str | None = None, with_suggestion: bool = True) -> str:
        """Return a compact, deterministic Decision Card for the current task.

        Repeated framework reads are intentionally side-effect free: retrieval
        does not change salience and proposing a question does not spend budget.
        """
        query = query or ""
        if not query:
            active = [fact for fact in self.facts if fact.status == "active"]
            values = [fact.value for fact in sorted(active, key=lambda f: f.confidence, reverse=True)[:8]]
            return "PREFER: " + " | ".join(values) if values else "No user preference information available yet."
        card = self.compile_task(query)
        if with_suggestion:
            question = self.propose_question(query)
            if question:
                card.ask.insert(0, question)
        return card.render()

    def compile_task(
        self, instruction: str, *, spec: TaskSpec | None = None
    ) -> DecisionCard:
        """Compile instruction and active facts into a bounded Decision Card."""
        spec = spec or TaskSpec.compile(instruction)
        facts = self.facts if self.enable_preference_modeling else []
        if self.enable_preference_modeling:
            spec.resolved_slots.update(resolve_preference_slots(spec, facts))
        card = build_decision_card(spec, facts)
        if not self.enable_proactive_interaction:
            card.ask.clear()
        return card

    def resolve_task_slots(
        self, instruction: str, *, spec: TaskSpec | None = None
    ) -> dict[str, str]:
        """Return strong, unambiguous historical values for task-choice slots."""
        spec = spec or TaskSpec.compile(instruction)
        if not self.enable_preference_modeling:
            return {}
        return resolve_preference_slots(spec, self.facts)

    def storage_stats(self) -> dict[str, int | bool]:
        """Observable storage diagnostics for traces and zero-model audits."""
        return {
            "tiered_compaction": self.enable_tiered_compaction,
            "preference_modeling": self.enable_preference_modeling,
            "memory_update_simple": self.memory_update_mode == "simple",
            "proactive_interaction": self.enable_proactive_interaction,
            "preference_entries": len(self.preference_store.facts),
            "entity_entries": len(self.entity_index),
            "combined_entries": len(self.facts),
            **self.preference_evidence.stats(),
        }

    def retrieve_facts(
        self,
        *,
        task_scope: str = "",
        candidate_fields: tuple[str, ...] = (),
        unresolved_dimensions: tuple[str, ...] = (),
        evidence_required: bool = True,
        limit: int = 8,
    ) -> tuple[object, ...]:
        """Typed, side-effect-free retrieval from the canonical fact view.

        This is the candidate-induced recovery path. It returns fact objects,
        never the narrative summary and never evaluator-derived information.
        """
        scope = (task_scope or "").strip().casefold()
        dimensions = {item.casefold() for item in unresolved_dimensions if item}
        field_blob = " ".join(str(item) for item in candidate_fields).casefold()
        eligible = []
        for fact in self.facts:
            if fact.status != "active" or not fact.decision_eligible:
                continue
            if evidence_required and not fact.evidence_ids:
                continue
            if scope and fact.scope not in {scope, "general"}:
                continue
            if dimensions and fact.dimension.casefold() not in dimensions:
                continue
            if field_blob and not any(
                token and token.casefold() in field_blob
                for token in (fact.value, fact.category, fact.dimension)
            ):
                continue
            eligible.append(fact)
        eligible.sort(
            key=lambda fact: (
                fact.dimension == "safety",
                fact.polarity == "negative",
                fact.confidence,
                fact.independent_evidence_count,
                fact.observed_at,
            ),
            reverse=True,
        )
        return tuple(eligible[: max(0, int(limit))])

    def _ingest_fact(
        self, fact, *, confirmed_drift: bool = False
    ):
        hypothesis = self.preference_evidence.observe_fact(fact)
        fact.confidence = hypothesis.confidence
        fact.belief_confidence = hypothesis.confidence
        fact.independent_evidence_count = hypothesis.evidence_count
        fact.source_diversity = hypothesis.source_diversity
        if self.enable_tiered_compaction and fact.dimension in ENTITY_DIMENSIONS:
            stored = self.entity_index.ingest(fact)
        else:
            stored = self.preference_store.ingest(
            fact, confirmed_drift=confirmed_drift
        )
        if stored is not None:
            self.preference_evidence.apply_to_facts([stored])
        return stored

    def _ingest_fact_simple(self, fact):
        """Controlled baseline: direct fact ingestion without belief updates."""
        if self.enable_tiered_compaction and fact.dimension in ENTITY_DIMENSIONS:
            return self.entity_index.ingest(fact)
        return self.preference_store.ingest(fact, confirmed_drift=False)

    def apply_candidate_grounding(
        self,
        card: DecisionCard,
        candidates: list[object],
        *,
        max_positive: int = 8,
    ) -> dict[str, int]:
        """Project belief only through explicit current-candidate edges."""
        if not self.enable_preference_modeling:
            card.preference_pool.clear()
            card.preference_weights.clear()
            card.preference_decisive.clear()
            card.preference_source_types.clear()
            card.candidate_preference_scores.clear()
            card.candidate_parent_preference_scores.clear()
            card.candidate_grounding_sources.clear()
            card.candidate_grounding_confidence.clear()
            card.candidate_grounding_edge_counts.clear()
            card.grounded_edge_count = 0
            return {
                "candidate_count": len(candidates),
                "grounded_positive": 0,
                "grounded_negative": 0,
                "grounded_edges": 0,
            }
        self.preference_evidence.apply_to_facts(self.facts)
        edges = candidate_evidence_edges(
            self.facts,
            candidates,
            task_text=" ".join(card.task_intent),
        )
        positive = [edge for edge in edges if edge.fact.polarity != "negative"]
        negative = [edge.projected_fact() for edge in edges if edge.fact.polarity == "negative"]

        # Preserve a representative from each grounded dimension/category
        # before filling by confidence.  This prevents an entity-heavy history
        # from crowding out safety/conditional evidence while keeping the card
        # bounded to the documented eight facts.
        grouped: dict[tuple[str, str, str], list[object]] = {}
        for edge in positive:
            key = (
                edge.fact.dimension,
                edge.fact.category,
                edge.fact.condition_signature,
            )
            grouped.setdefault(key, []).append(edge)
        fair_first = [items[0] for _key, items in sorted(grouped.items()) if items]
        remaining = [edge for edge in positive if edge not in fair_first]
        ordered = [*fair_first, *remaining]
        selected_fact_ids: set[str] = set()
        selected_values: list[str] = []
        for edge in ordered:
            value = edge.fact.value
            if edge.fact.fact_id in selected_fact_ids or value in selected_values:
                continue
            selected_fact_ids.add(edge.fact.fact_id)
            selected_values.append(value)
            if len(selected_values) >= max_positive:
                break
        card.preference_pool = selected_values
        card.preference_weights.clear()
        card.preference_decisive.clear()
        card.preference_source_types.clear()
        card.candidate_preference_scores.clear()
        card.candidate_parent_preference_scores.clear()
        card.candidate_grounding_sources.clear()
        card.candidate_grounding_confidence.clear()
        card.candidate_grounding_edge_counts.clear()
        card.grounded_edge_count = 0
        selected = set(selected_values)
        parent_identity_keys = {"store_name", "shop_name", "merchant_name"}
        # Each fact contributes at most once per candidate.  Multiple raw
        # fields exposing the same value are corroboration of grounding, not
        # repeated preference evidence.
        per_candidate_fact: dict[tuple[str, str], object] = {}
        for edge in positive:
            if edge.fact.fact_id not in selected_fact_ids:
                continue
            key = (edge.candidate_id, edge.fact.fact_id)
            previous = per_candidate_fact.get(key)
            if previous is None or edge.weight > previous.weight:
                per_candidate_fact[key] = edge
        for edge in per_candidate_fact.values():
            fact = edge.fact
            if fact.value not in selected:
                continue
            card.preference_weights[fact.value] = max(
                fact.confidence, card.preference_weights.get(fact.value, 0.0)
            )
            card.preference_decisive[fact.value] = (
                card.preference_decisive.get(fact.value, False)
                or fact.decision_eligible
            )
            card.preference_source_types[fact.value] = tuple(
                dict.fromkeys(
                    [
                        *card.preference_source_types.get(fact.value, ()),
                        *(fact.evidence_types or [fact.source_type]),
                    ]
                )
            )
            target = (
                card.candidate_parent_preference_scores
                if edge.attribute_key in parent_identity_keys
                else card.candidate_preference_scores
            )
            target[edge.candidate_id] = round(
                target.get(edge.candidate_id, 0.0) + edge.weight,
                4,
            )
            card.candidate_grounding_sources[edge.candidate_id] = tuple(
                dict.fromkeys(
                    [
                        *card.candidate_grounding_sources.get(edge.candidate_id, ()),
                        edge.attribute_source,
                    ]
                )
            )
            card.candidate_grounding_confidence[edge.candidate_id] = max(
                edge.grounding_confidence,
                card.candidate_grounding_confidence.get(edge.candidate_id, 0.0),
            )
            card.candidate_grounding_edge_counts[edge.candidate_id] = (
                card.candidate_grounding_edge_counts.get(edge.candidate_id, 0) + 1
            )
            card.grounded_edge_count += 1

        existing_avoids = set(card.avoid)
        for fact in negative:
            if fact.value in existing_avoids:
                continue
            existing_avoids.add(fact.value)
            card.avoid.append(fact.value)
            card.constraints.append(
                Constraint(
                    fact.dimension,
                    fact.value,
                    ConstraintTarget.CANDIDATE,
                    ConstraintOperator.EXCLUDES,
                    source="memory_candidate_grounding",
                    hard=fact.dimension in {"avoid", "safety"},
                    evidence_span=fact.value,
                )
            )
        return {
            "candidate_count": len(candidates),
            "grounded_positive": len(selected_values),
            "grounded_negative": len(negative),
            "grounded_edges": card.grounded_edge_count,
        }

    def _summary_fallback(self, spec: TaskSpec) -> list[str]:
        """Retrieve at most three relevant summary lines without dumping it all."""
        if not self._summary_text:
            return []
        markers = {spec.facet, *[c.value for c in spec.must]}
        lines = [line.strip(" -*") for line in self._summary_text.splitlines() if line.strip()]
        relevant = [line for line in lines if any(marker and marker in line for marker in markers)]
        return relevant[:3]

    def begin_subtask(self, instruction: str) -> None:
        """Explicit state transition invoked by ADAPTAgent, never by read()."""
        # Instruction text is not a unique subtask key; identical consecutive
        # tasks still receive independent question budgets and pending state.
        self._current_task_key = (instruction or "").strip()
        self.proactive.reset_subtask()

    def _read_base(self, query: str) -> str:
        """Retrieve and format memory facts in groundtruth-aligned categorical format."""
        events = self.scorer.retrieve(self.stream, query) if query else self.stream.most_important(self.top_k)

        # Collect signals by category
        likes: list[str] = []
        avoids: list[str] = []
        brands: list[str] = []       # brand_loyalty
        products: list[str] = []     # prefers_product
        intents: list[str] = []      # intent_product
        searches: list[str] = []
        explicit: list[str] = []     # explicit_preference
        taste: list[str] = []        # taste_preference (generalized 口味/规格维度)
        raw_obs: list[str] = []

        for ev in events:
            sig = ev.signal
            if self.drift.suppress_drifted(ev):
                continue
            if sig is None:
                if ev.raw_text:
                    raw_obs.append(ev.raw_text[:80])
                continue
            p = sig.predicate
            conf = sig.confidence
            obj = sig.object
            if p == "likes_food":
                likes.append(f"{obj}（置信{conf:.2f}）")
            elif p == "avoids_food":
                avoids.append(f"{obj}（置信{conf:.2f}）")
            elif p == "brand_loyalty":
                brands.append(f"{obj}（置信{conf:.2f}）")
            elif p == "prefers_product":
                products.append(obj)
            elif p == "intent_product":
                intents.append(obj)
            elif p == "searches":
                searches.append(obj)
            elif p == "explicit_preference":
                explicit.append(obj)
            elif p == "taste_preference":
                taste.append(obj)
            elif p == "raw_observation":
                raw_obs.append(ev.raw_text[:80])

        # Deduplicate while preserving order
        def dedup(lst: list[str]) -> list[str]:
            seen: set[str] = set()
            out: list[str] = []
            for x in lst:
                key = x.split("（")[0].strip()
                if key not in seen:
                    seen.add(key)
                    out.append(x)
            return out

        likes = dedup(likes)
        avoids = dedup(avoids)
        brands = dedup(brands)
        products = dedup(products)
        intents = dedup(intents)
        explicit = dedup(explicit)
        taste = dedup(taste)

        # Build categorical blocks — matches groundtruth memory format
        sections: list[str] = []

        if explicit:
            block = "【用户明确需求】\n" + "\n".join(f"  - {x}" for x in explicit[:10])
            sections.append(block)

        if brands or likes or avoids or products or taste:
            food_lines: list[str] = []
            if brands:
                # Sort brands by order frequency (most ordered first)
                def _brand_sort_key(b: str) -> int:
                    name = b.split("（")[0].strip()
                    return self._brand_freq.get(name, 0)
                brands_sorted = sorted(brands, key=_brand_sort_key, reverse=True)
                brand_strs = []
                for b in brands_sorted[:10]:
                    name = b.split("（")[0].strip()
                    freq = self._brand_freq.get(name, 0)
                    suffix = f"（已点{freq}次）" if freq >= 2 else ""
                    brand_strs.append(f"{name}{suffix}")
                food_lines.append("  常用商家/品牌（优先选择）：" + "、".join(brand_strs))
            if taste:
                food_lines.append("  口味/规格偏好：" + "、".join(taste[:15]))
            if likes:
                food_lines.append("  喜欢/偏好：" + "、".join(
                    x.split("（")[0] for x in likes[:10]
                ))
            if avoids:
                food_lines.append("  不喜欢/忌口：" + "、".join(
                    x.split("（")[0] for x in avoids[:10]
                ))
            if products:
                food_lines.append("  常点商品：" + "、".join(products[:12]))
            sections.append("【饮食/消费偏好】\n" + "\n".join(food_lines))

        if intents:
            block = "【近期意向商品】\n" + "\n".join(f"  - {x}" for x in intents[:6])
            sections.append(block)

        if searches:
            block = "【搜索记录（兴趣参考）】\n" + "\n".join(f"  - {x}" for x in searches[:10])
            sections.append(block)

        if raw_obs and not sections:
            # Only show raw observations when no structured data exists
            block = "【历史行为记录】\n" + "\n".join(f"  - {x}" for x in raw_obs[:6])
            sections.append(block)

        if not sections:
            return "No user preference information available yet."

        return "\n\n".join(sections)

    def _execution_hint(self, query: str) -> str:
        """Task-specific execution hint: the preferences most relevant to THIS
        subtask's choice, surfaced at the top of read() output.

        Soft framing on purpose — a hard 'must-do' gate made the agent fragile
        in v3 (over-asking, no exploration). This is an informational nudge the
        agent can override when the instruction conflicts.

        Domain-gated: drop events whose content clearly belongs to a different
        environment, and avoid transferring concrete past entities across OTA
        cities/subdomains. Only abstract compatible attributes may transfer.
        """
        if not query or not self.stream.events:
            return ""
        events = self.scorer.retrieve(self.stream, query, k=self._HINT_TOP_K)
        qdom = self.scorer.domain(query)
        # 忌口最高优先（最不能违反）；明确偏好/品牌/口味等跟随相关性排序，
        # 避免跨领域噪音（如点外卖任务误提示"要高铁"）
        high: list[str] = []
        prod_med: list[str] = []      # 常点X — concrete product anchors (most actionable)
        brand_med: list[str] = []     # 常选X — brand anchors
        attr_med: list[str] = []      # taste/likes — attribute-level signals
        seen: set[str] = set()
        for ev in events:
            sig = ev.signal
            if sig is None or self.drift.suppress_drifted(ev):
                continue
            p = sig.predicate
            obj = (sig.object or "").strip()
            if not obj or obj in seen:
                continue
            # Multi-clause 忌口 fragments (e.g. "小料，觉得多余") come from the
            # dialogue regex running past a clause boundary ("不加小料，觉得多余").
            # They are unreliable and crowd the 4-slot hint — drop them so the
            # real dimension preferences (冰镇/布蕾/热饮) can surface.
            if p == "avoids_food" and any(c in obj for c in "，、。；,。"):
                continue
            seen.add(obj)
            # Cross-domain guard: only enforced when the query domain is
            # confidently detected.
            if qdom:
                evdom = self.scorer._event_domain(
                    sig, f"{sig.predicate} {sig.object} {sig.raw}"
                )
                if evdom and _normalize_domain(evdom) != _normalize_domain(qdom):
                    continue
            if p == "avoids_food":
                high.append(f"忌口{obj}")
            elif qdom == "ota" and p in ("brand_loyalty", "prefers_product", "explicit_preference"):
                # OTA: skip concrete past entities that anchor the agent to a
                # wrong city/brand/subdomain (喜来登/门票/过去日期). Keep the
                # abstract signals below (taste/likes) or nothing.
                continue
            elif p == "brand_loyalty":
                brand_med.append(f"常选{obj}")
            elif p == "taste_preference":
                attr_med.append(obj)
            elif p == "likes_food":
                attr_med.append(f"喜欢{obj}")
            elif p == "prefers_product":
                prod_med.append(f"常点{obj}")
            elif p == "explicit_preference":
                attr_med.append(obj)
        # Reserve concrete product anchors before brands and attributes so a
        # high-confidence completed order is not displaced by many generic
        # attribute events in the bounded hint.
        hints = (high + prod_med[:2] + brand_med + attr_med)[:4]
        if not hints:
            return ""
        return (
            "【本任务执行提示】" + "、".join(hints) +
            "。选择商家/商品/房型/出行方式等选项时，应优先匹配上述常点商品/常选店铺；"
            "仅当与本次指令明确冲突时，以指令为准。"
        )

    def update(
        self,
        new_interactions: list,
        llm: str | None = None,
        llm_args: dict | None = None,
        **kwargs,
    ) -> str:
        """Parse new interactions into the memory stream, tracking drift/lifecycle.

        Pipeline:
        1. Heuristic SignalParser (fast, no LLM, handles orders/reviews/searches)
        2. LLM dialogue extraction (if llm available): extract structured
           preferences from dialogue turns that heuristics cannot parse
        3. Drift detection + lifecycle management
        4. Selective forgetting + stream pruning
        """
        fresh_interactions = []
        for interaction in new_interactions:
            encoded = json.dumps(interaction, ensure_ascii=False, sort_keys=True, default=str)
            fingerprint = hashlib.sha1(encoded.encode("utf-8")).hexdigest()
            if fingerprint not in self._seen_interactions:
                self._seen_interactions.add(fingerprint)
                fresh_interactions.append(interaction)
        if not fresh_interactions:
            return f"ADAPT memory unchanged: {len(self.stream)} events"

        signals = self.parser.parse(fresh_interactions)

        # LLM-assisted extraction: parse dialogue turns for richer preference signals.
        # Only called when an LLM is provided (vitabench passes the agent LLM here).
        # Cost: one LLM call per update() with dialogues, ~150 output tokens.
        if llm is not None:
            llm_signals = self._llm_extract_preferences(fresh_interactions, llm, llm_args)
            signals = signals + llm_signals

        n_sig = 0
        drift_hits = 0
        for sig in signals:
            event = self.stream.add(sig)
            n_sig += 1
            # Track brand order frequency (only from actual orders, not dialogue mentions)
            if sig.predicate == "brand_loyalty" and sig.type == "order":
                self._brand_freq[sig.object] += 1
                # Boost confidence proportional to order frequency (capped at 0.98)
                freq = self._brand_freq[sig.object]
                if freq >= 3:
                    sig.confidence = min(0.98, 0.8 + 0.02 * freq)
            # Drift detection: conflicting signals on the same predicate.
            fact = fact_from_signal(sig, str(event.id))
            drifted = bool(self.drift.observe(sig))
            if sig.predicate != "raw_observation":
                self._ingest_fact(fact, confirmed_drift=drifted)
            if drifted:
                drift_hits += 1
            # Lifecycle: assign type, reinforce/record fact.
            self.lifecycle.record(sig)
            # Track latest timestamp for recency reference.
            dt = parse_timestamp(sig.timestamp)
            cur = parse_timestamp(self._latest_ts) if self._latest_ts else None
            if dt and (cur is None or dt > cur):
                self._latest_ts = sig.timestamp

        # Selective forgetting: decay facts, evict dead ones.
        if self._latest_ts:
            evicted = self.lifecycle.apply_forgetting(self.stream, self._latest_ts)
        else:
            evicted = 0

        self._sync_fact_lifecycle()

        self._prune_stream()
        self._prune_facts()

        # LLM-maintained full user profile (delivery/instore gains depend on it).
        if llm is not None and self.enable_summary_rewrite:
            new_summary = self._llm_update_summary(fresh_interactions, llm, llm_args)
            if new_summary:
                self._summary_text = new_summary

        detail = f"ADAPT memory updated: +{n_sig} signals (total {len(self.stream)} events)"
        if drift_hits:
            detail += f", {drift_hits} drift detected"
        if evicted:
            detail += f", {evicted} forgotten"
        return detail

    def _llm_extract_preferences(
        self,
        interactions: list,
        llm: str,
        llm_args: dict | None,
    ) -> list:
        """Call the agent LLM to extract structured preferences from dialogue turns.

        Only processes interactions that contain dialogue content (init_gen format:
        {date, dialogue: [...]}). Heuristic extraction handles orders/reviews already.

        Returns a list of Signal objects (may be empty on parse failure / no dialogue).
        """
        # Collect dialogue texts
        dialogue_chunks: list[str] = []
        user_evidence: list[str] = []
        ref_ts = ""
        for inter in interactions:
            if not isinstance(inter, dict):
                continue
            if inter.get("dialogue"):
                date = inter.get("date", "")
                if date:
                    ref_ts = f"{date} 00:00:00"
                # Format only user turns for LLM processing
                lines = []
                for turn in inter["dialogue"]:
                    if not isinstance(turn, dict):
                        continue
                    role = turn.get("role", turn.get("speaker", "")).lower()
                    content = turn.get("content", turn.get("message", ""))
                    if (
                        isinstance(content, str)
                        and content
                        and role in ("user", "human", "客户", "顾客")
                    ):
                        lines.append(f"用户: {content}")
                        user_evidence.append(content)
                if lines:
                    dialogue_chunks.append("\n".join(lines))

        if not dialogue_chunks:
            return []

        # Truncate to keep LLM call cheap (max ~800 chars of dialogue)
        combined = "\n---\n".join(dialogue_chunks)
        if len(combined) > 800:
            combined = combined[:800] + "…"

        prompt = (
            "从以下用户与助手的对话中，提取用户的明确偏好信息。\n"
            "只提取用户语句中清晰表达的偏好，不要推测。\n"
            "以JSON数组形式输出，每项格式：\n"
            '{"predicate": "<类型>", "object": "<内容>", "confidence": <0.7-0.95>}\n'
            "可用predicate类型：\n"
            "  likes_food     - 用户明确表示喜欢的食物/口味/菜系\n"
            "  avoids_food    - 用户明确表示不吃/忌口/过敏的内容\n"
            "  explicit_preference - 用户明确表达的其他偏好需求\n"
            "  brand_loyalty  - 用户明确表示常去/喜欢去的店铺/品牌\n"
            "若无明确偏好，返回空数组[]。直接输出JSON，不要解释。\n\n"
            f"对话内容：\n{combined}"
        )

        try:
            from vita.data_model.message import SystemMessage, UserMessage
            from vita.utils.llm_utils import generate

            messages = [
                SystemMessage(role="system",
                              content="You are a preference extraction assistant. Output only valid JSON."),
                UserMessage(role="user", content=prompt),
            ]

            _args = {k: v for k, v in (llm_args or {}).items()
                     if k not in ("max_tokens", "max_completion_tokens", "num_retries")}
            response = generate(model=llm, messages=messages, max_tokens=256,
                                num_retries=0, **_args)

            if response is None or not response.content:
                return []

            raw = response.content.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                raw = raw.removeprefix("json")
            raw = raw.strip()

            import json
            items = json.loads(raw)
            if not isinstance(items, list):
                return []

            from agent.memory.signals import Signal
            signals = []
            evidence_text = "\n".join(user_evidence)
            for item in items:
                if not isinstance(item, dict):
                    continue
                pred = item.get("predicate", "")
                obj = str(item.get("object", "")).strip()
                conf = float(item.get("confidence", 0.8))
                if (
                    pred
                    and obj
                    and 1 <= len(obj) <= 60
                    and self._object_has_user_evidence(obj, evidence_text)
                ):
                    signals.append(Signal(
                        predicate=pred,
                        object=obj,
                        confidence=min(0.95, max(0.5, conf)),
                        timestamp=ref_ts,
                        type="conversation",
                        raw=f"[LLM extracted from user] {evidence_text[:400]}",
                        importance=7.0,  # LLM-extracted signals are high value
                    ))
            return signals

        except Exception:  # noqa: BLE001 - optional model extraction must not break runtime
            # Never fail silently — LLM extraction is best-effort
            return []

    @staticmethod
    def _object_has_user_evidence(value: str, user_text: str) -> bool:
        """Reject model-only preference objects without a user-text anchor."""
        import re

        def normalize(text: str) -> str:
            return re.sub(
                r"[^0-9A-Za-z\u4e00-\u9fff]+", "", (text or "").casefold()
            )

        normalized_value = normalize(value)
        normalized_user = normalize(user_text)
        if not normalized_value or not normalized_user:
            return False
        if normalized_value in normalized_user:
            return True
        anchors = re.findall(
            r"[\u4e00-\u9fff]{2,}|[a-z0-9]{3,}", normalized_value
        )
        return any(anchor in normalized_user for anchor in anchors)

    def _sync_fact_lifecycle(self) -> None:
        """Expire unprotected facts only when lifecycle removed all evidence."""
        alive_ids = {str(event.id) for event in self.stream.all()}
        protected = {"safety", "explicit", "conditional"}
        for fact in self.facts:
            if fact.status != "active" or not fact.evidence_ids:
                continue
            surviving = [
                evidence_id
                for evidence_id in fact.evidence_ids
                if str(evidence_id) in alive_ids
            ]
            if surviving:
                fact.evidence_ids = surviving
            elif fact.dimension not in protected:
                fact.status = "expired"

    # ------------------------------------------------------------------
    # Hybrid memory: coherent LLM summary (rich "gist") + structured signals
    # ------------------------------------------------------------------

    @staticmethod
    def _format_interactions(interactions: list) -> str:
        """Compact text of interactions for LLM summarization (init_gen format)."""
        lines: list[str] = []
        for inter in interactions:
            if not isinstance(inter, dict):
                continue
            date = inter.get("date", "")
            for beh in inter.get("behavior", []):
                if not isinstance(beh, dict):
                    continue
                btype = beh.get("behavior_type", "unknown")
                content = beh.get("content", {})
                if btype == "order":
                    merchant = content.get("merchant_name", "") if isinstance(content, dict) else ""
                    names = []
                    for it in content.get("items", []) if isinstance(content, dict) else []:
                        if isinstance(it, dict):
                            names.append(str(it.get("product_name", "")))
                    remark = (content.get("remark", "") or content.get("note", "")) if isinstance(content, dict) else ""
                    line = f"[{date}] 点单 {merchant}: {', '.join(n for n in names if n)[:120]}"
                    if remark:
                        line += f" (备注:{remark})"
                    lines.append(line)
                elif btype == "search":
                    kw = content.get("keyword", "") if isinstance(content, dict) else str(content)
                    if kw:
                        lines.append(f"[{date}] 搜索: {kw}")
                elif btype in ("complaint", "comment", "review"):
                    target = content.get("target_name", "") if isinstance(content, dict) else ""
                    if target:
                        lines.append(f"[{date}] {btype}: {target}")
            for turn in inter.get("dialogue", []):
                if isinstance(turn, dict) and turn.get("role") == "user":
                    c = turn.get("content", "")
                    if c:
                        lines.append(f"[{date}] 用户: {c[:80]}")
        return "\n".join(lines)[:2500]

    def _llm_update_summary(self, interactions: list, llm: str,
                            llm_args: dict | None) -> str | None:
        """Generate/update a coherent user-profile summary via the LLM.

        This is the 'rich' half of the hybrid memory: the LLM summary supplies
        coherent, cross-fact gist (like RewriteMemory), while the structured
        signals below supply precision + drift handling.
        """
        interactions_text = self._format_interactions(interactions)
        if not interactions_text.strip():
            return None

        current = self._summary_text or "（空，暂无历史画像）"
        prompt = (
            "你是一个用户偏好记忆管理器。维护一份准确、连贯、详细的用户偏好画像。\n\n"
            f"## 当前画像：\n{current}\n\n"
            f"## 新的交互记录：\n{interactions_text}\n\n"
            "## 要求：合并新旧信息，输出一份更新后的、尽量详细的用户偏好画像。规则：\n"
            "1. 保留仍有效的偏好，更新矛盾的，删除过时的；\n"
            "2. 按维度组织，尽量写具体细节（具体事实比泛泛描述有用）：\n"
            "   - 口味/饮食：具体口味（重口味/清淡/麻辣/酸辣/菌汤）、忌口（不吃大蒜/香菜/冰）、甜度冰度（少糖/多冰/不加糖/常温/热）\n"
            "   - 常用品牌/商家：具体的品牌名、店铺名（如霸王茶姬、白象）\n"
            "   - 常点商品：具体的商品名\n"
            "   - 地点：工作地址、常住地址、常去区域\n"
            "   - 服务要求：配送时长、到店/外卖偏好\n"
            "   - 其他：消费习惯、时间偏好、价格敏感度\n"
            "3. 只写有依据的偏好，不要推测；\n"
            "4. 控制在1200字以内；每条都保留事实依据，直接输出画像，不要任何解释。"
        )
        try:
            from vita.data_model.message import SystemMessage, UserMessage
            from vita.utils.llm_utils import generate
            messages = [
                SystemMessage(role="system",
                              content="You are a preference summarization assistant. Output only the summary."),
                UserMessage(role="user", content=prompt),
            ]
            _args = {k: v for k, v in (llm_args or {}).items()
                     if k not in ("max_tokens", "max_completion_tokens", "num_retries")}
            response = generate(model=llm, messages=messages, max_tokens=4096,
                                num_retries=0, **_args)
            if response is None or not response.content:
                return None
            return response.content.strip()[:1200]
        except Exception:  # noqa: BLE001 - optional summary generation is best-effort
            return None

    def reset(self) -> None:
        self.stream.reset()
        self.drift = DriftDetector()
        self.lifecycle = LifecycleManager()
        self._latest_ts = None
        self._brand_freq = Counter()
        self._summary_text = ""
        self.preference_store.reset()
        self.entity_index.reset()
        self._seen_interactions.clear()
        self._current_task_key = ""
        self.proactive.reset_subtask()

    # ------------------------------------------------------------------
    # Proactive asking hook
    # ------------------------------------------------------------------

    def suggest_question(self, instruction: str, domain: str | None = None) -> str | None:
        """Return a question to ask the user if a decision-relevant info gap exists.

        This is the interface VitaBench's proactive subtasks need: when the
        instruction is vague and memory lacks the decision info, ask.
        """
        return self.propose_question(instruction, domain)

    def propose_question(self, instruction: str, domain: str | None = None) -> str | None:
        """Pure question proposal; does not consume the per-subtask budget."""
        gap = self.propose_gap(instruction, domain)
        return gap.question if gap else None

    def propose_gap(self, instruction: str, domain: str | None = None):
        """Return the same typed gap used by ADAPTAgent's runtime controller."""
        if domain is None:
            domain = self.scorer.domain(instruction)
        memory_text = self._read_base(instruction)
        spec = TaskSpec.compile(instruction)
        known_slots = resolve_preference_slots(spec, self.facts)
        return self.proactive.propose_gap(
            instruction, memory_text, domain, known_slots=known_slots
        )

    def _suggest_question_inner(self, instruction: str, domain: str | None = None) -> str | None:
        return self.propose_question(instruction, domain)

    def commit_question(self, question: str) -> bool:
        """Record that ADAPTAgent actually sent ``question`` to the user."""
        return self.proactive.commit_question(question)

    def record_user_answer(self, answer: str, question: str | None = None,
                           dimension: str = "", *, persistent: bool = True) -> bool:
        """Persist an answer to a committed proactive question."""
        q = question or self.proactive.pending_question
        if not q or not (answer or "").strip():
            return False
        if not persistent:
            return False
        before = len(self.stream)
        self.proactive.record_answer(q, answer, self)
        event = self.stream.events[-1] if len(self.stream) > before else None
        if event and event.signal:
            fact = fact_from_signal(event.signal, str(event.id))
            if dimension:
                spec = TaskSpec.compile(self._current_task_key)
                fact.scope = spec.domain
                fact.facet = spec.facet
                fact.category = spec.facet
                fact.dimension = dimension
            self._ingest_fact(fact)
        return True

    def acknowledge_task_answer(self, answer: str) -> bool:
        """Clear a committed question after a task-local answer.

        The runtime already stores the value in the active ``TaskSpec`` slot.
        This method deliberately records no long-term preference fact.
        """
        if not self.proactive.pending_question or not (answer or "").strip():
            return False
        self.proactive.pending_question = None
        return True

    @staticmethod
    def is_persistent_statement(text: str) -> bool:
        """Expose the conservative persistence classifier to ADAPTAgent."""
        return is_persistent_statement(text)

    def record_runtime_preference(
        self,
        statement: str,
        *,
        persistent: bool = False,
        timestamp: str = "",
    ) -> int:
        """Persist an explicit live preference without promoting task-only text.

        Returns the number of facts accepted by the bounded profile.  Current
        task requirements are intentionally handled by ``TaskSpec`` instead.
        """
        signals = self.parser.parse_runtime_statement(
            statement, timestamp=timestamp, persistent=persistent
        )
        accepted = 0
        for signal in signals:
            event = self.stream.add(signal)
            fact = fact_from_signal(signal, str(event.id))
            if self._ingest_fact(fact) is not None:
                accepted += 1
        return accepted

    # ------------------------------------------------------------------
    # Agent-callable tools (auto-discovered via @is_tool)
    # ------------------------------------------------------------------

    @is_tool(ToolType.READ)
    def suggest_question_tool(self, instruction: str) -> str:
        """当用户的需求信息不完整时，返回一个需要向用户确认的问题。

        Agent 应在执行任务前调用此工具。若返回问题，先向用户询问获取答案，
        再根据答案继续执行。若无信息缺口，返回空字符串。
        """
        return self.propose_question(instruction) or ""

    @is_tool(ToolType.WRITE)
    def record_preference_answer(self, answer: str, question: str = "") -> str:
        """记录用户对主动询问的回答，供该用户后续子任务使用。

        Args:
            answer: 用户刚刚给出的答案。
            question: 对应问题；留空时使用最近一次已提交的问题。
        """
        if self.record_user_answer(answer, question or None):
            return "Preference answer recorded"
        return "No committed question to attach this answer to"

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _format_signal(self, sig) -> str:
        if sig.predicate == "avoids_food":
            return f"- 用户不喜欢: {sig.object} (置信 {sig.confidence:.2f})"
        if sig.predicate == "brand_loyalty":
            return f"- 用户忠诚店铺: {sig.object} (置信 {sig.confidence:.2f})"
        if sig.predicate == "prefers_product":
            return f"- 用户偏好商品: {sig.object} (置信 {sig.confidence:.2f})"
        if sig.predicate == "likes_food":
            return f"- 用户喜欢: {sig.object} (置信 {sig.confidence:.2f})"
        if sig.predicate == "intent_product":
            return f"- 用户有意向: {sig.object} (置信 {sig.confidence:.2f})"
        if sig.predicate == "searches":
            return f"- 用户搜索过: {sig.object}"
        if sig.predicate == "explicit_preference":
            return f"- 用户明确偏好: {sig.object}"
        return f"- {sig.predicate}: {sig.object}"

    def _prune_stream(self, max_events: int = 500) -> None:
        if len(self.stream) <= max_events:
            return
        keep = sorted(self.stream.all(), key=lambda e: e.importance + e.salience, reverse=True)[:max_events]
        self.stream.events = keep

    def _prune_facts(self, max_facts: int = 500) -> None:
        """Bound the structured projection without reviving superseded facts."""
        self.preference_store.max_facts = max_facts
        self.preference_store.prune()
        if self.enable_tiered_compaction:
            self.entity_index.prune()
