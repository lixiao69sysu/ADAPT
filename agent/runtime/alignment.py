"""Open-world preference atoms aligned to observed candidate attributes.

The vocabulary is induced from the current Decision Card and current tool
results.  It intentionally contains no product-domain schema or benchmark IDs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable


_DYNAMIC_KV_RE = re.compile(
    r"([\u4e00-\u9fffA-Za-z_]{1,16})\s*[:：]\s*([^,，)\]\n]+)"
)
_TAG_RE = re.compile(r"tags\s*[=:]\s*(\[[^\]]*\])")
_VALUE_SPLIT_RE = re.compile(r"[\s,，/|、（）()\[\]{};；]+")
_NORMALIZE_RE = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9]+")
_ALTERNATIVE_RE = re.compile(r"(?:或者|或是|任选|都行|均可|二选一)")
_TASK_OVERLAP_STOP = {
    "帮我",
    "给我",
    "一下",
    "一个",
    "一款",
    "一种",
    "商品",
    "服务",
    "适合",
    "需要",
    "想要",
    "推荐",
    "购买",
    "下单",
    "预约",
}
_TECHNICAL_KEYS = {
    "id", "user_id", "product_id", "product_ids", "store_id", "shop_id",
    "hotel_id", "room_id", "order_id", "quantity", "price", "date",
    "create_time", "update_time", "longitude", "latitude",
    "tags",
}


def _normalize(value: str) -> str:
    return _NORMALIZE_RE.sub("", value or "").casefold()


def _usable(value: str) -> bool:
    normalized = _normalize(value)
    return len(normalized) >= 2 and not normalized.isdigit() and "_" not in value


@dataclass(frozen=True)
class PreferenceAtom:
    """One candidate-groundable unit with an open-world attribute key."""

    meta_type: str
    attribute_key: str
    value: str
    source_preference: str
    polarity: str = "prefer"
    condition: str = ""
    weight: float = 1.0
    decisive: bool = True
    evidence_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidateAttribute:
    key: str
    value: str
    source: str


@dataclass
class CandidateAttributeMap:
    candidate_id: str
    attributes: tuple[CandidateAttribute, ...]

    @classmethod
    def from_candidate(cls, candidate: Any) -> "CandidateAttributeMap":
        values: list[CandidateAttribute] = []

        def add(key: str, value: Any, source: str) -> None:
            text = str(value or "").strip()
            if key in _TECHNICAL_KEYS or not _usable(text):
                return
            values.append(CandidateAttribute(key or "open", text, source))
            # Merchant/service names are atomic identities. Splitting a value
            # such as "Brand (City shop)" makes generic location suffixes look
            # like reusable brand evidence across unrelated candidates.
            parts = () if key in {"store_name", "shop_name", "merchant_name"} else _VALUE_SPLIT_RE.split(text)
            for part in parts:
                if part != text and _usable(part):
                    values.append(CandidateAttribute(key or "open", part, source))

        add("name", getattr(candidate, "name", ""), "name")
        for key, value in (getattr(candidate, "attributes", {}) or {}).items():
            add(str(key), value, "field")
        raw = getattr(candidate, "raw", "") or ""
        for key, value in _DYNAMIC_KV_RE.findall(raw):
            add(key.strip(), value.strip(), "dynamic_field")
        for blob in _TAG_RE.findall(raw):
            for tag in re.findall(r"['\"]([^'\"]+)['\"]", blob):
                add("tag", tag, "tag")

        unique: dict[tuple[str, str], CandidateAttribute] = {}
        for attribute in values:
            unique.setdefault(
                (attribute.key, _normalize(attribute.value)), attribute
            )
        return cls(str(getattr(candidate, "candidate_id", "")), tuple(unique.values()))

    def matches(self, atom: PreferenceAtom) -> bool:
        target = _normalize(atom.value)
        if not target:
            return False
        for attribute in self.attributes:
            if (
                atom.attribute_key not in {"legacy_text", "open"}
                and attribute.key != atom.attribute_key
            ):
                continue
            observed = _normalize(attribute.value)
            if observed == target or (
                len(target) >= 2 and observed.startswith(target)
            ) or (
                len(target) >= 3 and target in observed
            ):
                return True
        return False


class EvidenceAlignment:
    """A comparable preference-evidence matrix for one candidate set."""

    def __init__(
        self,
        candidates: Iterable[Any],
        preferences: Iterable[Any],
        legacy_matcher: Callable[[str, str], bool],
    ) -> None:
        self._candidates = list(candidates)
        self._maps = {
            str(getattr(candidate, "candidate_id", "")):
            CandidateAttributeMap.from_candidate(candidate)
            for candidate in self._candidates
        }
        self._legacy_matcher = legacy_matcher
        self.atoms = self._atomize(list(preferences))
        self._matches: dict[str, tuple[PreferenceAtom, ...]] = {}
        for candidate in self._candidates:
            candidate_id = str(getattr(candidate, "candidate_id", ""))
            attribute_map = self._maps[candidate_id]
            raw = getattr(candidate, "raw", "") or ""
            self._matches[candidate_id] = tuple(
                atom
                for atom in self.atoms
                if (
                    self._legacy_matcher(atom.value, raw)
                    if atom.attribute_key == "legacy_text"
                    else attribute_map.matches(atom)
                )
            )

    def _atomize(self, preferences: list[Any]) -> tuple[PreferenceAtom, ...]:
        vocabulary: dict[str, tuple[str, str, str]] = {}
        source_priority = {"dynamic_field": 4, "field": 3, "tag": 2, "name": 1}
        for attribute_map in self._maps.values():
            for attribute in attribute_map.attributes:
                normalized = _normalize(attribute.value)
                if not _usable(attribute.value):
                    continue
                previous = vocabulary.get(normalized)
                if previous is None or source_priority.get(attribute.source, 0) > source_priority.get(previous[1], 0):
                    vocabulary[normalized] = (
                        attribute.key,
                        attribute.source,
                        attribute.value,
                    )

        ordered_values = sorted(vocabulary, key=lambda value: (len(value), value), reverse=True)
        atoms_by_identity: dict[tuple[str, str], PreferenceAtom] = {}
        for source in preferences:
            if isinstance(source, (tuple, list)):
                preference = str(source[0])
                weight = float(source[1]) if len(source) > 1 else 1.0
                allow_legacy = bool(source[2]) if len(source) > 2 else True
                decisive = bool(source[3]) if len(source) > 3 else True
                evidence_types = tuple(source[4]) if len(source) > 4 else ()
            else:
                preference = str(source)
                weight = 1.0
                allow_legacy = True
                decisive = True
                evidence_types = ()
            normalized = _normalize(preference)
            matched_any = False
            index = 0
            while index < len(normalized):
                match = next(
                    (value for value in ordered_values if normalized.startswith(value, index)),
                    "",
                )
                if not match:
                    index += 1
                    continue
                key, _source, observed_value = vocabulary[match]
                # Coverage measures distinct grounded attributes, not how many
                # memory sentences happened to repeat the same evidence.
                identity = (key, match)
                previous = atoms_by_identity.get(identity)
                combined_types = tuple(
                    dict.fromkeys(
                        [
                            *(previous.evidence_types if previous else ()),
                            *evidence_types,
                        ]
                    )
                )
                combined_decisive = (
                    decisive
                    or bool(previous and previous.decisive)
                    or len(set(combined_types)) >= 2
                )
                if previous is None or weight > previous.weight or combined_decisive != previous.decisive:
                    atoms_by_identity[identity] = PreferenceAtom(
                        meta_type="attribute",
                        attribute_key=key,
                        value=observed_value,
                        source_preference=(
                            preference
                            if previous is None or weight > previous.weight
                            else previous.source_preference
                        ),
                        weight=max(weight, previous.weight if previous else 0.0),
                        decisive=combined_decisive,
                        evidence_types=combined_types,
                    )
                matched_any = True
                index += len(match)
            # A remembered attribute can be less specific than the catalog's
            # observed spelling (for example "near metro" versus "near metro
            # entrance"). CandidateAttributeMap.matches() already supports
            # this direction, but atomization previously discarded the source
            # before it reached that matcher. Preserve the remembered value as
            # the atom and borrow only the open-world attribute key from the
            # current candidate vocabulary. This is candidate-set local and
            # does not introduce a domain synonym table.
            if not matched_any and len(normalized) >= 2:
                prefix_matches = [
                    value for value in ordered_values if value.startswith(normalized)
                ]
                for match in prefix_matches:
                    key, _source, _observed_value = vocabulary[match]
                    identity = (key, normalized)
                    previous = atoms_by_identity.get(identity)
                    combined_types = tuple(
                        dict.fromkeys(
                            [
                                *(previous.evidence_types if previous else ()),
                                *evidence_types,
                            ]
                        )
                    )
                    combined_decisive = (
                        decisive
                        or bool(previous and previous.decisive)
                        or len(set(combined_types)) >= 2
                    )
                    if (
                        previous is None
                        or weight > previous.weight
                        or combined_decisive != previous.decisive
                    ):
                        atoms_by_identity[identity] = PreferenceAtom(
                            meta_type="attribute",
                            attribute_key=key,
                            value=preference,
                            source_preference=preference,
                            weight=max(weight, previous.weight if previous else 0.0),
                            decisive=combined_decisive,
                            evidence_types=combined_types,
                        )
                    matched_any = True
            # Candidate names are often more specific than the user's open-
            # world category phrase (for example a brand-prefixed appliance).
            # When no exact candidate attribute grounded, recover the longest
            # observable phrase shared by the instruction and one attribute.
            # This is induced from the live candidate set, not a category list.
            if not matched_any and "current_instruction" in evidence_types:
                overlaps: list[tuple[int, str, str]] = []
                for candidate_value in ordered_values:
                    overlap = _longest_common_substring(normalized, candidate_value)
                    if (
                        len(overlap) >= 3
                        and overlap not in _TASK_OVERLAP_STOP
                        and not overlap.isdigit()
                    ):
                        key, _source, _observed_value = vocabulary[candidate_value]
                        overlaps.append((len(overlap), key, overlap))
                if overlaps:
                    _, key, overlap = max(overlaps)
                    atoms_by_identity[(key, overlap)] = PreferenceAtom(
                        meta_type="task_identity",
                        attribute_key=key,
                        value=overlap,
                        source_preference=preference,
                        weight=weight,
                        decisive=True,
                        evidence_types=evidence_types,
                    )
                    matched_any = True
            if not matched_any and allow_legacy and _usable(preference):
                identity = ("legacy_text", _normalize(preference))
                previous = atoms_by_identity.get(identity)
                if previous is None or weight > previous.weight:
                    atoms_by_identity[identity] = PreferenceAtom(
                        meta_type="attribute",
                        attribute_key="legacy_text",
                        value=preference,
                        source_preference=preference,
                        weight=weight,
                        decisive=decisive,
                        evidence_types=evidence_types,
                    )
        return tuple(atoms_by_identity.values())

    def task_identity_atom(self) -> PreferenceAtom | None:
        """Return one candidate-grounded identity anchor for an unseen task.

        The anchor is used only when the typed compiler has no groundable
        category/entity constraint.  Alternative requests stay a ranking
        choice rather than becoming an impossible conjunction.
        """
        atoms = [
            atom
            for atom in self.atoms
            if "current_instruction" in atom.evidence_types
            and atom.attribute_key not in {
                "legacy_text",
                "store_name",
                "shop_name",
                "merchant_name",
            }
            and not _ALTERNATIVE_RE.search(atom.source_preference)
        ]
        if not atoms:
            return None
        frequencies = {
            atom: sum(atom in self.matches(candidate) for candidate in self._candidates)
            for atom in atoms
        }
        # A task category normally recurs across more candidates than a soft
        # style/brand preference. Length then favors the most specific anchor.
        return max(atoms, key=lambda atom: (frequencies[atom], len(_normalize(atom.value))))

    def satisfies_task_identity(
        self, candidate: Any, atom: PreferenceAtom | None
    ) -> bool:
        return atom is None or atom in self.matches(candidate)

    def matches(self, candidate: Any) -> tuple[PreferenceAtom, ...]:
        return self._matches.get(
            str(getattr(candidate, "candidate_id", "")), ()
        )
    def coverage(self, candidate: Any) -> int:
        return len(self.matches(candidate))

    def score(self, candidate: Any) -> float:
        return sum(atom.weight for atom in self.matches(candidate))

    def decisive_score(self, candidate: Any) -> float:
        return sum(atom.weight for atom in self.matches(candidate) if atom.decisive)

    def matrix(self) -> dict[str, tuple[str, ...]]:
        return {
            candidate_id: tuple(atom.value for atom in atoms)
            for candidate_id, atoms in self._matches.items()
        }


def _longest_common_substring(left: str, right: str) -> str:
    """Return a deterministic longest contiguous overlap in O(n*m)."""
    if not left or not right:
        return ""
    previous = [0] * (len(right) + 1)
    best_length = 0
    best_end = 0
    for left_index, left_char in enumerate(left, start=1):
        current = [0] * (len(right) + 1)
        for right_index, right_char in enumerate(right, start=1):
            if left_char != right_char:
                continue
            current[right_index] = previous[right_index - 1] + 1
            length = current[right_index]
            if length > best_length:
                best_length = length
                best_end = left_index
        previous = current
    return left[best_end - best_length : best_end]
