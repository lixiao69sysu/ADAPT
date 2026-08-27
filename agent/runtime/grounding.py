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
_QUANTITY_PHRASE = re.compile(
    r"(?:\d+|[零〇一二两三四五六七八九十百千万]+)\s*"
    r"(?:个|件|瓶|杯|份|张|位|间|套|盒|袋|罐|支|双|晚|天|人)"
)
_LATIN_TOKEN = re.compile(r"[a-z][a-z0-9&.'-]+", re.I)
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")
_CJK_UNIGRAM_STOP = set("我你他她它的了吧呀啊帮给买点送来去想要个一这那在到和与及为请")
_REQUEST_OBJECT_RE = re.compile(
    r"(?:帮(?:我|我们)?|给我|替我|快帮我|再帮我|想|要)"
    r"[^，。！？!?\n]{0,10}?"
    r"(?:吃|喝|买|点|订|定|团|下单|预约|预定)"
    r"(?:点|个|一个|一杯|一份|一张|一双|一件|一本)?"
    r"([^，。！？!?\n]{1,40})"
)
_REQUEST_OBJECT_SUFFIX_RE = re.compile(
    r"(?:送|寄|配送)?(?:到|去|来|至)(?:家(?:里|中)?|公司|单位|店里).*$|"
    r"(?:帮我|请|吧|呢|呀|啊|了|就行|即可)+$"
)
_REQUEST_QUALIFIER_RE = re.compile(
    r"(?:^|[，,；;])([^，,。！？!?；;]{1,24}?的)(?=[，,。！？!?；;]|$)"
)


def _semantic_text(value: str) -> str:
    text = _TECHNICAL_KEY.sub(" ", str(value or "").casefold())
    text = _QUANTITY_PHRASE.sub(" ", text)
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
        # Quantity phrases have already been removed structurally above, so a
        # legitimate one-character entity can remain useful without letting
        # the "瓶" in "两瓶" favor a "大瓶装" candidate.
        for width in range(2, min(4, len(run)) + 1):
            features.update(
                run[index : index + width]
                for index in range(len(run) - width + 1)
            )
        features.update(
            character for character in run if character not in _CJK_UNIGRAM_STOP
        )
    return features


def _request_object_features(instruction: str) -> set[str]:
    """Extract verb-object anchors without a category or merchant lexicon.

    Context nouns such as the ``饭`` in ``晚饭`` must not tie the requested
    object in ``想吃点粉类的``.  The extraction is deliberately syntactic:
    it keeps every observable transaction/desire object and never names a
    product family, user, merchant, or benchmark entity.
    """
    anchors: set[str] = set()
    for match in _REQUEST_OBJECT_RE.finditer(instruction or ""):
        value = _REQUEST_OBJECT_SUFFIX_RE.sub("", match.group(1)).strip(" ，。！？!?")
        if value:
            anchors.update(_features(value))
    # Chinese requests commonly place a mandatory modifier in a following
    # clause: "一本小说，日文原著的".  Keeping that qualifier at the
    # same layer prevents the head noun alone from selecting an incompatible
    # candidate.  The rule is grammatical and open-world: it contains no
    # language, category, product, or merchant values.
    for match in _REQUEST_QUALIFIER_RE.finditer(instruction or ""):
        anchors.update(_features(match.group(1)))
    return anchors


def semantic_overlap_score(left: str, right: str) -> float:
    """Value-free lexical overlap used for both entities and tool semantics."""
    matched = _features(left) & _features(right)
    return float(sum(min(len(feature), 4) for feature in matched))


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
    raw = str(getattr(candidate, "raw", "") or "")
    # VitaBench's legacy rows flatten a parent and a leaf on one line, while
    # the generic ``key=value`` compatibility parser can only retain the first
    # comma-delimited fragment of a compound attribute.  Keep the remainder
    # as intrinsic evidence after removing topology values.  This preserves
    # fields such as language/category/tags without allowing a merchant name
    # or relational ID to impersonate the leaf candidate.
    intrinsic_raw = re.sub(
        r"\b[A-Za-z][A-Za-z0-9_]*(?:_id|_ids)\s*[:=]\s*[^,)\]\s]+",
        " ",
        raw,
        flags=re.I,
    )
    for key, value in attributes.items():
        if key.casefold().endswith("_name") and str(value).casefold() != name.casefold():
            intrinsic_raw = intrinsic_raw.replace(str(value), " ")
    evidence = [name, *values, intrinsic_raw]
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
        request_features = _request_object_features(instruction)
        candidates_by_id = {
            str(getattr(candidate, "candidate_id", "")): candidate
            for candidate in candidates
        }
        raw: dict[str, tuple[str, float, tuple[str, ...]]] = {}
        for candidate_id, candidate in candidates_by_id.items():
            candidate_features = _features(_candidate_text(candidate))
            matched = task_features & candidate_features
            request_matched = request_features & candidate_features
            # The request object is a stronger current-task signal than
            # incidental context in the same utterance.  Full-text overlap is
            # retained as a secondary open-world fallback.
            score = float(
                100 * sum(min(len(feature), 4) for feature in request_matched)
                + sum(min(len(feature), 4) for feature in matched)
            )
            raw[candidate_id] = (
                _family(candidate, candidates_by_id),
                score,
                tuple(
                    sorted(
                        request_matched or matched,
                        key=lambda item: (-len(item), item),
                    )[:12]
                ),
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
