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

from typing import Any, Dict, List, Optional
from pathlib import Path

from vita.memory.base import BaseMemory
from vita.environment.toolkit import ToolType, is_tool

from agent.memory.retrieval import RetrievalConfig, RetrievalScorer
from agent.memory.signals import SignalParser
from agent.memory.stream import MemoryStream, parse_timestamp
from agent.memory.drift import DriftDetector
from agent.memory.lifecycle import LifecycleManager
from agent.memory.proactive import ProactiveEngine
from agent.memory.summarizer import MemorySummarizer
from agent.evolution.runtime import load_strategy_changes


def _same_pref(a: str, b: str) -> bool:
    """Check if two preference values refer to the same thing."""
    if not a or not b:
        return False
    a, b = a.strip(), b.strip()
    if a == b:
        return True
    if len(a) >= 3 and len(b) >= 3:
        return a in b or b in a
    return False


class ADAPTMemory(BaseMemory):
    """ADAPT long-term preference memory with drift-aware retrieval."""

    # 记忆分层：哪些 predicate 是持久的，哪些是短暂的
    DURABLE_PREDICATES = {
        "avoids_food", "delivery_address", "brand_loyalty", "explicit_preference",
    }
    EPHEMERAL_PREDICATES = {
        "searches", "intent_product", "taste_preference", "urgency",
    }

    def __init__(
        self,
        language: str = None,
        top_k: int = 20,
        w_relevance: float = 0.5,
        w_recency: float = 0.2,
        w_importance: float = 0.3,
        half_life_days: float = 180.0,
        drift_threshold: int = 2,
        max_questions: int = 2,
        strategy_registry: str | Path | None = None,
        candidate_strategy_id: str | None = None,
        **kwargs,
    ):
        super().__init__(language=language, top_k=top_k, **kwargs)
        strategy_changes = load_strategy_changes(
            strategy_registry, candidate_strategy_id
        )
        retrieval_changes = strategy_changes.get("memory.retrieval", {})
        paging_changes = strategy_changes.get("memory.context_paging", {})
        self.stream = MemoryStream()
        self.parser = SignalParser()
        self.scorer = RetrievalScorer(RetrievalConfig(
            w_relevance=w_relevance,
            w_recency=w_recency,
            w_importance=w_importance,
            half_life_days=half_life_days,
            top_k=top_k,
            product_type_expansions=retrieval_changes.get(
                "product_type_expansions", {}
            ),
            ignore_generic_bigrams=bool(
                retrieval_changes.get("ignore_generic_bigrams", False)
            ),
            structured_signal_boost=retrieval_changes.get(
                "structured_signal_boost", {}
            ),
        ))
        self.drift = DriftDetector(drift_threshold=drift_threshold)
        self.lifecycle = LifecycleManager()
        self.proactive = ProactiveEngine(max_questions=max_questions)
        self.summarizer = MemorySummarizer()
        self._latest_ts: Optional[str] = None
        self._recency_reference = retrieval_changes.get("recency_reference")
        self._raw_excerpt_mode = paging_changes.get("raw_excerpt", "prefix")
        self._raw_excerpt_chars = int(paging_changes.get("max_chars", 100))

    def _classify_layer(self, sig) -> str:
        """Classify a signal into memory layer: durable, normal, or ephemeral."""
        if sig.predicate in self.DURABLE_PREDICATES:
            return "durable"
        if sig.predicate in self.EPHEMERAL_PREDICATES:
            return "ephemeral"
        return "normal"

    # ------------------------------------------------------------------
    # BaseMemory interface
    # ------------------------------------------------------------------

    def read(self, query: str = None, with_suggestion: bool = True) -> str:
        """Return formatted preference memory for injection into the system prompt.

        Args:
            query: The current subtask instruction. Used for task-aware retrieval.
            with_suggestion: When True (default), append a "suggested question"
                line if the proactive engine detects an information gap. This is
                the reliable channel for proactive asking — the agent sees it
                directly in the injected memory text.
        """
        query = query or ""
        base = self._read_base(query) if self.stream.events else ""

        # Proactive asking: append the suggested question so the agent asks
        # the user before guessing (vital for proactive subtasks). Works even
        # when memory is empty (cold start) — that's exactly when asking matters.
        if with_suggestion and query:
            question = self._suggest_question_inner(query)
            if question:
                if base:
                    base += "\n"
                base += f"【信息不完整，建议先向用户询问】{question}"

        if not base:
            return "No user preference information available yet."
        return base

    def _read_base(self, query: str) -> str:
        """Retrieve and format memory facts without proactive suggestion."""
        reference = (
            parse_timestamp(self._latest_ts or "")
            if self._recency_reference == "latest_observed_event_time"
            else None
        )
        events = (
            self.scorer.retrieve(self.stream, query, now=reference)
            if query
            else self.stream.most_important(self.top_k)
        )

        # Collect non-drifted signals for summarization, with layer filtering
        durable_signals: list = []
        normal_signals: list = []
        raw_excerpts: list = []
        for ev in events:
            sig = ev.signal
            # Suppress events pointing to a drifted-away preference value.
            if self.drift.suppress_drifted(ev):
                continue
            if sig and sig.predicate != "raw_observation":
                layer = self._classify_layer(sig)
                if layer == "durable":
                    durable_signals.append(sig)
                elif layer != "ephemeral":
                    normal_signals.append(sig)
                # ephemeral signals are dropped unless they're recent/high-confidence
                elif sig.confidence >= 0.7:
                    normal_signals.append(sig)
            elif ev.raw_text:
                excerpt = self._raw_excerpt(ev.raw_text, query)
                raw_excerpts.append(f"- 观察[{ev.timestamp}] ({ev.type}): {excerpt}")

        # Prioritize durable + best normal signals
        all_signals = durable_signals + normal_signals
        if not all_signals and not raw_excerpts:
            return "No user preference information available yet."

        # Use summarizer when we have enough signals
        if len(all_signals) >= 3:
            summary = self.summarizer.summarize(all_signals)
            # Also include any raw excerpts (up to 2)
            if raw_excerpts:
                summary += "\n" + "\n".join(raw_excerpts[:2])
            return summary

        # Fallback to raw listing for few signals
        lines = []
        for sig in all_signals:
            lines.append(self._format_signal(sig))
        lines.extend(raw_excerpts)
        return "\n".join(lines)

    def _raw_excerpt(self, text: str, query: str) -> str:
        limit = self._raw_excerpt_chars
        if len(text) <= limit or self._raw_excerpt_mode != "query_centered":
            return text[:limit]
        chars = [character for character in query if "一" <= character <= "鿿"]
        grams = {
            chars[index] + chars[index + 1]
            for index in range(len(chars) - 1)
        } - {"帮我", "给我", "送到", "家里", "赶紧", "新的", "上次", "一个", "现在"}
        positions = [
            position
            for gram in grams
            if (position := text.find(gram)) >= 0
        ]
        if not positions:
            return text[:limit]
        start = max(0, min(positions) - limit // 3)
        return text[start : start + limit]

    def update(
        self,
        new_interactions: list,
        llm: Optional[str] = None,
        llm_args: Optional[dict] = None,
        **kwargs,
    ) -> str:
        """Parse new interactions into the memory stream, tracking drift/lifecycle.

        Args:
            new_interactions: List of interaction records (dict or Interaction).
        """
        # Each update() call = a new subtask begins in VitaBench. Reset the
        # proactive ask budget so a fresh subtask can ask again.
        self.proactive.reset_subtask()

        signals = self.parser.parse(new_interactions)
        n_sig = 0
        drift_hits = 0
        n_reinforced = 0
        for sig in signals:
            # Confidence accumulation: same preference seen before → boost.
            existing = [
                e for e in self.stream.events
                if e.signal.predicate == sig.predicate
                and _same_pref(e.signal.object, sig.object)
            ]
            if existing:
                sig.confidence = min(1.0, sig.confidence + 0.15 * len(existing))
                n_reinforced += 1

            self.stream.add(sig)
            n_sig += 1
            # Drift detection: conflicting signals on the same predicate.
            if self.drift.observe(sig):
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
        detail = f"ADAPT memory updated: +{n_sig} signals (total {len(self.stream)} events)"
        if n_reinforced:
            detail += f", {n_reinforced} reinforced"
        if drift_hits:
            detail += f", {drift_hits} drift detected"
        if evicted:
            detail += f", {evicted} forgotten"
        return detail

    def reset(self) -> None:
        self.stream.reset()
        self.drift = DriftDetector()
        self.lifecycle = LifecycleManager()
        self._latest_ts = None

    # ------------------------------------------------------------------
    # Proactive asking hook
    # ------------------------------------------------------------------

    def suggest_question(self, instruction: str, domain: Optional[str] = None) -> Optional[str]:
        """Return a question to ask the user if a decision-relevant info gap exists.

        This is the interface VitaBench's proactive subtasks need: when the
        instruction is vague and memory lacks the decision info, ask.
        """
        return self._suggest_question_inner(instruction, domain, consume=True)

    def _suggest_question_inner(
        self,
        instruction: str,
        domain: Optional[str] = None,
        consume: bool = False,
    ) -> Optional[str]:
        # Infer domain from the instruction if not given (e.g. read() path).
        if domain is None:
            domain = self.scorer.domain(instruction)
        memory_text = self._read_base(instruction)
        return self.proactive.decide_to_ask(
            instruction, memory_text, domain, consume=consume
        )

    # ------------------------------------------------------------------
    # Agent-callable tools (auto-discovered via @is_tool)
    # ------------------------------------------------------------------

    @is_tool(ToolType.READ)
    def suggest_question_tool(self, instruction: str) -> str:
        """当用户的需求信息不完整时，返回一个需要向用户确认的问题。

        Agent 应在执行任务前调用此工具。若返回问题，先向用户询问获取答案，
        再根据答案继续执行。若无信息缺口，返回空字符串。
        """
        direct = instruction.strip()
        if direct.startswith(("请问", "您希望", "您想")) or direct.endswith(("?", "？")):
            if self.proactive.asked_this_subtask < self.proactive.max_questions:
                self.proactive.asked_this_subtask += 1
                return direct

        q = self.suggest_question(instruction)
        if q:
            return q
        return "无需继续询问；请使用已有信息继续完成任务，不要再次调用本工具。"

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
