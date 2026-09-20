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
from agent.memory.fact_store import FactStore
from agent.memory.facts import fact_from_signal
from agent.memory.grounding import ground_facts_to_candidates
from agent.memory.lifecycle import LifecycleManager
from agent.memory.proactive import ProactiveEngine, QuestionContext
from agent.memory.retrieval import RetrievalConfig, RetrievalScorer
from agent.memory.signals import SignalParser
from agent.memory.slots import resolve_preference_slots
from agent.memory.stream import MemoryStream, parse_timestamp


class ADAPTMemory:
    """ADAPT long-term preference memory with drift-aware retrieval.

    **Framework-free by construction.** This class imports nothing from
    ``vita``; a deployment that only needs the preference store can use it
    directly.

    The evaluation harness additionally needs two things this class no longer
    provides:

    * the ``BaseMemory`` interface the orchestrator calls, and
    * ``@is_tool`` auto-discovery, which is what puts
      ``read_preference_memory`` / ``record_preference_answer`` into the domain
      toolkit so the model can query and write memory mid-conversation
      (removing it is the E-042 regression: stock called
      ``query_preference_memory`` 30 times while the hidden-tool arm called it 0).

    Both are supplied by the adapter
    ``agent.adapters.vitabench_memory.VitaBenchADAPTMemory``, which inherits
    from this class *and* ``vita.memory.base.BaseMemory``. The two tool methods
    below stay plain methods here; the adapter re-declares them with the
    decorator.
    """

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
        summary_max_chars: int = 800,
        enable_tiered_compaction: bool = True,
        entity_index_max_entries: int = 500,
        **kwargs,
    ):
        # Cooperative `super()` with **no arguments**: in the vitabench adapter
        # the MRO continues into BaseMemory/ToolKitBase (which installs the
        # @is_tool registry); standalone it terminates at object. Our own
        # attributes are set *after* it, because BaseMemory's defaults would
        # otherwise overwrite `language` / `top_k`.
        super().__init__()
        self.language = language
        self.top_k = top_k
        self.similarity_threshold = 0.0
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
        # Compatibility name used by existing callers and diagnostics.
        self.fact_store = self.preference_store
        self.entity_index = EntityEvidenceIndex(entity_index_max_entries)
        self.facts = CombinedFactView(
            self.preference_store,
            self.entity_index,
            enable_tiered_compaction,
        )
        self.enable_summary_rewrite = enable_summary_rewrite
        self.summary_max_chars = summary_max_chars
        self._seen_interactions: set[str] = set()
        self._current_task_key: str = ""

    # ------------------------------------------------------------------
    # BaseMemory interface
    # ------------------------------------------------------------------

    def read(self, query: str | None = None, with_suggestion: bool = True) -> str:
        """Return a compact, deterministic Decision Card for the current task.

        Repeated framework reads are intentionally side-effect free: retrieval
        does not change salience and proposing a question does not spend budget.

        When the LLM profile summary is enabled it is prepended as one bounded
        block. Trace comparison against the stock baseline showed that its
        advantage on the same model comes from exactly this: a *generalized*
        preference summary ("喜欢冷色调", "对哈密瓜过敏") that the policy model can
        apply directly, instead of item-level facts it must generalize itself
        (E-046). The card below stays the precision half used by the validators.
        """
        query = query or ""
        summary = self._render_summary_block()
        if not query:
            card_text = self._render_memory_overview()
            return f"{summary}{card_text}" if summary else card_text
        card = self.compile_task(query)
        if with_suggestion:
            question = self.propose_question(query)
            if question:
                card.ask.insert(0, question)
        rendered = card.render()
        return f"{summary}{rendered}" if summary else rendered

    def _render_memory_overview(self, max_facts: int = 8) -> str:
        """Render the whole-memory view through the polarity-aware renderer.

        The overview must not assert a polarity it does not know. Joining raw
        ``fact.value`` under a fixed ``PREFER`` label turned a durable safety
        fact such as an allergy into a stated preference, and
        ``read_preference_memory`` reads exactly this path (E-059). Polarity is
        a typed property of the fact, so both read paths share one renderer.
        """
        active = [fact for fact in self.facts if fact.status == "active"]
        if not active:
            return "No user preference information available yet."
        negatives: list[str] = []
        positives: list[str] = []
        for fact in sorted(active, key=lambda f: f.confidence, reverse=True):
            if not fact.value:
                continue
            target = negatives if fact.polarity == "negative" else positives
            if fact.value not in target:
                target.append(fact.value)
        if not negatives and not positives:
            return "No user preference information available yet."
        return DecisionCard(avoid=negatives, prefer=positives).render(
            max_facts=max_facts
        )

    def _render_summary_block(self) -> str:
        """The bounded profile summary block, or an empty string when disabled."""
        if not self.enable_summary_rewrite:
            return ""
        text = (self._summary_text or "").strip()
        if not text:
            return ""
        return (
            "## 用户偏好归纳（来自历史交互，供推理参考）\n"
            f"{text[: self.summary_max_chars]}\n\n"
        )

    def compile_task(self, instruction: str) -> DecisionCard:
        """Compile instruction and active facts into a bounded Decision Card."""
        spec = TaskSpec.compile(instruction)
        spec.resolved_slots.update(resolve_preference_slots(spec, self.facts))
        return build_decision_card(spec, self.facts)

    def resolve_task_slots(self, instruction: str) -> dict[str, str]:
        """Return strong, unambiguous historical values for task-choice slots."""
        spec = TaskSpec.compile(instruction)
        return resolve_preference_slots(spec, self.facts)

    def storage_stats(self) -> dict[str, int | bool]:
        """Observable storage diagnostics for traces and zero-model audits."""
        return {
            "tiered_compaction": self.enable_tiered_compaction,
            "preference_entries": len(self.preference_store.facts),
            "entity_entries": len(self.entity_index),
            "combined_entries": len(self.facts),
        }

    def _ingest_fact(
        self, fact, *, confirmed_drift: bool = False
    ):
        if self.enable_tiered_compaction and fact.dimension in ENTITY_DIMENSIONS:
            return self.entity_index.ingest(fact)
        return self.preference_store.ingest(
            fact, confirmed_drift=confirmed_drift
        )

    def apply_candidate_grounding(
        self,
        card: DecisionCard,
        candidates: list[object],
        *,
        max_positive: int = 64,
    ) -> dict[str, int]:
        """Replace the pre-search pool with facts grounded to live candidates."""
        grounded = ground_facts_to_candidates(self.facts, candidates)
        positive = [fact for fact in grounded if fact.polarity != "negative"]
        negative = [fact for fact in grounded if fact.polarity == "negative"]

        selected_values: list[str] = []
        for fact in positive:
            if fact.value not in selected_values:
                selected_values.append(fact.value)
            if len(selected_values) >= max_positive:
                break
        card.preference_pool = selected_values
        card.preference_weights.clear()
        card.preference_decisive.clear()
        card.preference_source_types.clear()
        selected = set(selected_values)
        for fact in positive:
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
        }

    def begin_subtask(self, instruction: str) -> None:
        """Explicit state transition invoked by ADAPTAgent, never by read()."""
        task_key = (instruction or "").strip()
        if task_key != self._current_task_key:
            self._current_task_key = task_key
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
                    if isinstance(content, str) and content:
                        role_label = "用户" if role in ("user", "human", "客户", "顾客") else "助手"
                        lines.append(f"{role_label}: {content}")
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
            for item in items:
                if not isinstance(item, dict):
                    continue
                pred = item.get("predicate", "")
                obj = str(item.get("object", "")).strip()
                conf = float(item.get("confidence", 0.8))
                if pred and obj and 1 <= len(obj) <= 60:
                    signals.append(Signal(
                        predicate=pred,
                        object=obj,
                        confidence=min(0.95, max(0.5, conf)),
                        timestamp=ref_ts,
                        type="conversation",
                        raw=f"[LLM extracted] {pred}: {obj}",
                        importance=7.0,  # LLM-extracted signals are high value
                    ))
            return signals

        except Exception:  # noqa: BLE001 - optional model extraction must not break runtime
            # Never fail silently — LLM extraction is best-effort
            return []

    # ------------------------------------------------------------------
    # Hybrid memory: coherent LLM summary (rich "gist") + structured signals
    # ------------------------------------------------------------------

    @staticmethod
    def _format_interactions(interactions: list) -> str:
        """Keep source fields and corrections until semantic summarization.

        The stock formatter supports both benchmark interaction shapes and
        preserves product attributes and review bodies. A prefix character cut
        here used to discard the most recent corrections before the LLM saw them.
        The generated summary still has its separate presentation budget.
        """
        from vita.memory.rewrite_memory import RewriteMemory

        return RewriteMemory._format_interactions(interactions)

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
            "你是一个用户偏好记忆管理器。维护一份准确、连贯的用户偏好画像。\n\n"
            f"## 当前画像：\n{current}\n\n"
            f"## 新的交互记录：\n{interactions_text}\n\n"
            "## 要求：合并新旧信息，输出更新后的用户偏好画像。规则：\n"
            "1. 保留仍有效的偏好，更新与之矛盾的旧偏好，删除过时项；**新的明确表述优先于更早的推论**；\n"
            "2. 优先输出**可复用的偏好维度**，而不是一次性的商品清单。维度示例（不限于此）：\n"
            "   - 审美/风格：颜色倾向（冷色调/暖色调/低饱和）、风格（简约/复古）\n"
            "   - 口味/饮食：口味（重口味/清淡/麻辣/酸辣/菌汤）、忌口与过敏（如对某食材过敏）、甜度冰度\n"
            "   - 出行：方式（动车/高铁/飞机）、座位与舱位等级、住宿价位与档次、常选品牌\n"
            "   - 地点：常住/工作地址所属区域、常去商圈、可接受的距离或时长\n"
            "   - 服务：配送时长要求、单人/多人、到店或外卖、时间偏好\n"
            "   - 消费：价格区间、频率、对促销的偏好\n"
            "3. 具体商品名、商家名只在**重复出现**或**代表上述维度**时保留，且必须紧跟其所属维度；\n"
            "4. 只写有依据的偏好，不要推测；有冲突时以最新一次为准；\n"
            f"5. 控制在 {self.summary_max_chars} 字以内；直接输出画像，不要任何解释。"
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
            response = generate(model=llm, messages=messages, max_tokens=2048,
                                num_retries=0, **_args)
            if response is None or not response.content:
                return None
            return response.content.strip()[: self.summary_max_chars]
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
        proposal = self.propose(instruction, domain)
        return proposal.question if proposal else None

    def propose(self, instruction: str, domain: str | None = None):
        """Pure proposal carrying the slot the question is about (E-069)."""
        spec = TaskSpec.compile(instruction)
        known_slots = resolve_preference_slots(spec, self.facts)
        context = QuestionContext(
            instruction=instruction,
            domain=spec.domain,
            facet=spec.facet,
            action=spec.action,
            unknown_slots=tuple(spec.unknown_slots or ()),
            resolved_slots={
                slot: str(value)
                for slot, value in (spec.resolved_slots or {}).items()
                if value
            },
            known_slots=dict(known_slots or {}),
        )
        return self.proactive.propose(context=context)

    def _suggest_question_inner(self, instruction: str, domain: str | None = None) -> str | None:
        return self.propose_question(instruction, domain)

    def commit_question(self, question: str, **metadata) -> bool:
        """Record that a question was actually sent to the user.

        ``metadata`` (``slot``/``value``/``is_confirmation``) comes from the
        :class:`~agent.memory.proactive.Proposal` the question came from, so the
        reply can later be resolved to a decision dimension.
        """
        return self.proactive.commit_question(question, **metadata)

    def record_user_answer(
        self,
        answer: str,
        question: str | None = None,
        dimension: str = "",
    ) -> bool:
        """Persist an answer to a committed proactive question.

        The stored fact carries the **resolved slot value**, not the reply text:
        an affirmative answer to "这次仍然不加糖吗？" stores 无糖, not "是的"
        (E-069). The slot becomes the fact's dimension so later turns can read it.
        """
        q = question or self.proactive.pending_question
        if not q or not (answer or "").strip():
            return False
        before = len(self.stream)
        slot, value = self.proactive.record_answer(q, answer, self)
        if not value:
            # The reply was consumed but resolved to nothing usable (a bare
            # "不用了" to an open question). Nothing to ingest, and inventing a
            # value here is exactly what the design spine forbids.
            return True
        event = self.stream.events[-1] if len(self.stream) > before else None
        if event and event.signal:
            fact = fact_from_signal(event.signal, str(event.id))
            resolved_dimension = dimension or slot
            if resolved_dimension:
                spec = TaskSpec.compile(self._current_task_key)
                fact.scope = spec.domain
                fact.facet = spec.facet
                fact.category = spec.facet
                fact.dimension = resolved_dimension
            self._ingest_fact(fact)
        return True

    # ------------------------------------------------------------------
    # Agent-callable tools (auto-discovered via @is_tool)
    # ------------------------------------------------------------------

    def suggest_question_tool(self, instruction: str) -> str:
        """当用户的需求信息不完整时，返回一个需要向用户确认的问题。

        Agent 应在执行任务前调用此工具。若返回问题，先向用户询问获取答案，
        再根据答案继续执行。若无信息缺口，返回空字符串。
        """
        return self.propose_question(instruction) or ""

    def query_preference_memory(self, query: str) -> str:
        """根据具体问题查询用户偏好记忆，返回与该问题相关的偏好条目。

        在挑选候选、生成搜索关键词或说明推荐理由之前调用，
        可以得到该用户在此情境下的偏好证据，而不是仅依赖固定卡片。

        Args:
            query: 当前情境或需求，例如"垃圾桶 家居用品 购买偏好"。
        """
        return self.read(query)

    def read_preference_memory(self) -> str:
        """读取用户偏好记忆的整体视图（包含任务相关的偏好与待确认问题）。"""
        return self.read()

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
