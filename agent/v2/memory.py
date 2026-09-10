"""Evidence-linked soft memory for ADAPT V2.

The store deliberately avoids domain/category taxonomies.  It preserves what
was observed, where it came from and when it changed; the model remains
responsible for deciding whether an observation is relevant to the open-world
request in front of it.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field
from vita.environment.toolkit import ToolType, is_tool
from vita.memory.base import BaseMemory
from vita.memory.rewrite_memory import RewriteMemory

BeliefStatus = Literal["active", "contradicted", "superseded"]


class PreferenceBelief(BaseModel):
    belief_id: str
    statement: str
    polarity: str = "observed"
    context: str = ""
    confidence: float = Field(default=0.55, ge=0.0, le=1.0)
    first_seen: str
    last_seen: str
    evidence_ids: list[str] = Field(default_factory=list)
    status: BeliefStatus = "active"
    conflicts_with: list[str] = Field(default_factory=list)


class EvidenceRecord(BaseModel):
    evidence_id: str
    observed_at: str
    source_type: str
    content: str


def _canonical(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _terms(text: str) -> set[str]:
    normalized = str(text or "").lower()
    words = set(re.findall(r"[a-z0-9_\-]{2,}|[\u4e00-\u9fff]{2,}", normalized))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    words.update(chinese[i : i + 2] for i in range(max(0, len(chinese) - 1)))
    return words


class BeliefStore:
    """Append-only evidence plus revisable, explicitly conflict-aware beliefs."""

    def __init__(self) -> None:
        self.beliefs: dict[str, PreferenceBelief] = {}
        self.evidence: dict[str, EvidenceRecord] = {}

    def observe_interactions(self, interactions: list[Any], context: str = "") -> None:
        for interaction in interactions:
            payload = (
                interaction.model_dump(mode="json")
                if isinstance(interaction, BaseModel)
                else interaction
            )
            canonical = _canonical(payload)
            evidence_id = "ev-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
            if evidence_id in self.evidence:
                continue
            if isinstance(payload, dict):
                observed_at = str(payload.get("timestamp") or payload.get("date") or _now())
                source_type = str(payload.get("type") or "interaction")
                content = _canonical(payload.get("content", payload))
            else:
                observed_at = _now()
                source_type = "interaction"
                content = canonical
            self.evidence[evidence_id] = EvidenceRecord(
                evidence_id=evidence_id,
                observed_at=observed_at,
                source_type=source_type,
                content=content,
            )
            belief_id = "belief-" + hashlib.sha256(
                f"{source_type}:{content}".encode()
            ).hexdigest()[:16]
            existing = self.beliefs.get(belief_id)
            if existing:
                existing.last_seen = observed_at
                if evidence_id not in existing.evidence_ids:
                    existing.evidence_ids.append(evidence_id)
                continue
            self.beliefs[belief_id] = PreferenceBelief(
                belief_id=belief_id,
                statement=f"Observed {source_type}: {content[:1200]}",
                context=context,
                first_seen=observed_at,
                last_seen=observed_at,
                evidence_ids=[evidence_id],
            )

    def add(self, belief: PreferenceBelief) -> PreferenceBelief:
        missing = [item for item in belief.evidence_ids if item not in self.evidence]
        if missing:
            raise ValueError(f"Unknown evidence ids: {missing}")
        for conflict_id in belief.conflicts_with:
            conflict = self.beliefs.get(conflict_id)
            if conflict and conflict.status == "active":
                conflict.status = "contradicted"
        self.beliefs[belief.belief_id] = belief
        return belief

    def retrieve(
        self,
        query: str | None = None,
        candidate_fields: list[str] | None = None,
        limit: int = 8,
    ) -> list[PreferenceBelief]:
        needles = _terms(" ".join([query or "", *(candidate_fields or [])]))
        active = [item for item in self.beliefs.values() if item.status == "active"]
        if not needles:
            return sorted(active, key=lambda item: item.last_seen, reverse=True)[:limit]

        def score(item: PreferenceBelief) -> tuple[float, str]:
            haystack = _terms(f"{item.statement} {item.context}")
            overlap = len(needles & haystack)
            return (overlap * item.confidence, item.last_seen)

        ranked = sorted(active, key=score, reverse=True)
        relevant = [item for item in ranked if score(item)[0] > 0]
        return (relevant or ranked)[:limit]

    def render(
        self,
        query: str | None = None,
        candidate_fields: list[str] | None = None,
        char_budget: int = 5000,
    ) -> str:
        lines: list[str] = []
        for belief in self.retrieve(query, candidate_fields):
            evidence = ",".join(belief.evidence_ids[:3])
            line = (
                f"- [{belief.belief_id}] {belief.statement} "
                f"(polarity={belief.polarity}, confidence={belief.confidence:.2f}, "
                f"first={belief.first_seen}, last={belief.last_seen}, evidence={evidence})"
            )
            if sum(len(item) + 1 for item in lines) + len(line) > char_budget:
                break
            lines.append(line)
        return "\n".join(lines)

    def reset(self) -> None:
        self.beliefs.clear()
        self.evidence.clear()


class HybridMemory(BaseMemory):
    """Stock RewriteMemory plus a separate evidence-linked soft store."""

    def __init__(
        self,
        language: str | None = None,
        rewrite: RewriteMemory | None = None,
        belief_store: BeliefStore | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(language=language, **kwargs)
        self.rewrite = rewrite or RewriteMemory(language=language, **kwargs)
        self.belief_store = belief_store or BeliefStore()
        self.current_context = ""

    def set_context(self, context: str) -> None:
        self.current_context = context

    def read(self, query: str | None = None) -> str:
        # Keep the stock memory section stable; evidence is appended separately
        # by ADAPTV2.system_prompt under a clearly soft heading.
        return self.rewrite.read(query=query)

    def render_evidence(
        self, query: str | None = None, candidate_fields: list[str] | None = None
    ) -> str:
        return self.belief_store.render(query, candidate_fields)

    def update(
        self,
        new_interactions: list,
        llm: str | None = None,
        llm_args: dict | None = None,
        **kwargs: Any,
    ) -> str:
        updated = self.rewrite.update(
            new_interactions=new_interactions,
            llm=llm,
            llm_args=llm_args,
            **kwargs,
        )
        self.belief_store.observe_interactions(new_interactions, self.current_context)
        return updated

    @is_tool(ToolType.READ)
    def read_preference_memory(self) -> str:
        """Read the stock-compatible preference summary."""
        return self.read()

    @is_tool(ToolType.READ)
    def query_preference_memory(self, query: str) -> str:
        """Query preference summary and evidence-linked soft observations."""
        evidence = self.render_evidence(query=query)
        return self.read(query=query) + (f"\n\nEvidence-linked observations:\n{evidence}" if evidence else "")

    def reset(self) -> None:
        self.rewrite.reset()
        self.belief_store.reset()
