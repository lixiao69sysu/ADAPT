"""Current-task relevance, isolated from historical preference alignment."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_ACTION_WORDS = (
    "帮我",
    "请",
    "推荐",
    "搜索",
    "查找",
    "购买",
    "下单",
    "预订",
    "预约",
    "需要",
    "想要",
    "给我",
    "buy",
    "book",
    "find",
    "search",
    "recommend",
    "please",
)
_TECHNICAL_KEY = re.compile(r"\b[A-Za-z][A-Za-z0-9_]*(?:_id|_ids)\b", re.I)
_NUMBER_OR_DATE = re.compile(r"\b\d+(?:[-/:.]\d+)*\b")
_LATIN_TOKEN = re.compile(r"[a-z][a-z0-9&.'-]+", re.I)
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")
_CJK_UNIGRAM_STOP = set("我你他她它的了吧呀啊帮给买点送来去想要个一这那在到和与及为请")


def _semantic_text(value: str) -> str:
    text = _TECHNICAL_KEY.sub(" ", str(value or "").casefold())
    text = _NUMBER_OR_DATE.sub(" ", text)
    for word in _ACTION_WORDS:
        text = text.replace(word, " ")
    return " ".join(text.split())


def _features(value: str) -> set[str]:
    text = _semantic_text(value)
    features = {token.casefold() for token in _LATIN_TOKEN.findall(text)}
    for run in _CJK_RUN.findall(text):
        # Bounded 2-4 character n-grams handle unseen Chinese entity phrases
        # without a product/category dictionary or longest-substring hard gate.
        for width in range(2, min(4, len(run)) + 1):
            features.update(
                run[index : index + width]
                for index in range(len(run) - width + 1)
            )
        features.update(character for character in run if character not in _CJK_UNIGRAM_STOP)
    return features


def _candidate_text(candidate: Any) -> str:
    attributes = getattr(candidate, "attributes", {}) or {}
    name = str(getattr(candidate, "name", "") or "")
    values: list[str] = []
    for key, value in attributes.items():
        field = str(key).casefold()
        text = str(value or "")
        # A flattened result row often repeats parent names beside the leaf.
        # Task grounding must describe the candidate itself: relational IDs
        # and a different entity's ``*_name`` are topology, not identity.
        if field.endswith(("_id", "_ids")):
            continue
        if field.endswith("_name") and text.casefold() != name.casefold():
            continue
        values.append(text)
    # When parsed attributes exist, raw text is deliberately excluded because
    # it may contain flattened parent entities.  Raw is only a compatibility
    # fallback for unstructured candidate observations.
    evidence = [name, *values] if attributes else [name, getattr(candidate, "raw", "")]
    return " ".join(str(part) for part in evidence if part)


def _family(candidate: Any, candidates_by_id: dict[str, Any]) -> str:
    parent_types = sorted(
        {
            str(getattr(candidates_by_id[parent_id], "entity_type", ""))
            for parent_id in getattr(candidate, "parent_ids", ())
            if parent_id in candidates_by_id
        }
    )
    suffix = f"<{','.join(parent_types)}>" if parent_types else ""
    return f"{getattr(candidate, 'entity_type', 'unknown')}{suffix}"


@dataclass(frozen=True)
class TaskRelevance:
    candidate_id: str
    family: str
    score: float
    band: int
    matched_features: tuple[str, ...]


class TaskRelevanceMatrix:
    """Lexical live-task evidence only; memory is not an input."""

    def __init__(self, instruction: str, candidates: list[Any]) -> None:
        task_features = _features(instruction)
        candidates_by_id = {
            str(getattr(candidate, "candidate_id", "")): candidate
            for candidate in candidates
        }
        raw: dict[str, tuple[str, float, tuple[str, ...]]] = {}
        for candidate_id, candidate in candidates_by_id.items():
            matched = task_features & _features(_candidate_text(candidate))
            score = float(sum(min(len(feature), 4) for feature in matched))
            raw[candidate_id] = (
                _family(candidate, candidates_by_id),
                score,
                tuple(sorted(matched, key=lambda item: (-len(item), item))[:12]),
            )
        maximum = max((item[1] for item in raw.values()), default=0.0)
        self.rows: dict[str, TaskRelevance] = {}
        for candidate_id, (family, score, matched) in raw.items():
            band = 2 if maximum > 0 and score == maximum else (1 if score > 0 else 0)
            self.rows[candidate_id] = TaskRelevance(
                candidate_id, family, score, band, matched
            )
        top_families = {
            row.family for row in self.rows.values() if row.band == 2
        }
        self.reliable_grounding = bool(maximum >= 1 and len(top_families) == 1)
        self.single_structural_family = len(
            {row.family for row in self.rows.values()}
        ) <= 1

    def row(self, candidate: Any) -> TaskRelevance:
        candidate_id = str(getattr(candidate, "candidate_id", ""))
        return self.rows.get(
            candidate_id,
            TaskRelevance(candidate_id, "unknown", 0.0, 0, ()),
        )
