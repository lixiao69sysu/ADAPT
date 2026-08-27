"""Lossless, open-schema candidate observations and workflow compilation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class ManifestField:
    path: str
    value: Any
    json_type: str = ""


@dataclass(frozen=True)
class CandidateManifest:
    candidate_id: str
    entity_type: str
    name: str
    tool_name: str
    record_path: str
    fields: tuple[ManifestField, ...]
    parent_ids: tuple[str, ...] = ()
    inventory: int | None = None
    price: float | None = None
    confidence: float = 1.0
    parser: str = "json_schema"
    raw_record: Any = field(default=None, compare=False, repr=False)

    def attributes(self) -> dict[str, str]:
        return {field.path: str(field.value) for field in self.fields}


def _schema_at(schema: Any, path: tuple[str, ...]) -> dict[str, Any]:
    node = schema if isinstance(schema, dict) else {}
    for part in path:
        if part.isdigit():
            node = node.get("items", {}) if isinstance(node, dict) else {}
        else:
            node = node.get("properties", {}).get(part, {}) if isinstance(node, dict) else {}
        while isinstance(node, dict) and "$ref" in node:
            ref = node["$ref"].split("/")[-1]
            node = schema.get("$defs", schema.get("definitions", {})).get(ref, {})
    return node if isinstance(node, dict) else {}


def _walk_records(value: Any, path: tuple[str, ...] = ()):
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from _walk_records(child, (*path, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_records(child, (*path, str(index)))


def _flatten(value: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _flatten(child, (*path, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _flatten(child, (*path, str(index)))
    else:
        yield ".".join(path), value


def _id_fields(record: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    for key, value in record.items():
        field_schema = properties.get(key, {}) if isinstance(properties, dict) else {}
        role = str(field_schema.get("x-adapt-role", ""))
        if role in {"id", "parent_id"} or key.casefold().endswith("_id"):
            if isinstance(value, (str, int)) and str(value).strip():
                fields.append(key)
    return fields


def _semantic_field(
    record: dict[str, Any], schema: dict[str, Any], roles: tuple[str, ...], names: tuple[str, ...]
):
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    lowered_keys = {key.casefold(): key for key in record}
    for name in names:
        exact_key = lowered_keys.get(name)
        if exact_key is not None and record[exact_key] not in (None, "", []):
            return record[exact_key]
    for key, value in record.items():
        declared = str((properties.get(key, {}) or {}).get("x-adapt-role", ""))
        if declared in roles:
            if value not in (None, "", []):
                return value
    return None


class CandidateManifestParser:
    """Parse JSON results without requiring benchmark-specific annotations."""

    @staticmethod
    def parse(
        tool_name: str, payload: Any, result_schema: dict[str, Any] | None = None
    ) -> tuple[CandidateManifest, ...]:
        schema = result_schema or {}
        manifests: list[CandidateManifest] = []
        seen: set[tuple[str, str]] = set()
        for path, record in _walk_records(payload):
            record_schema = _schema_at(schema, path)
            ids = _id_fields(record, record_schema)
            if not ids:
                continue
            flattened = tuple(
                ManifestField(
                    ".".join((*path, field_path)) if path else field_path,
                    value,
                    str(_schema_at(schema, (*path, *field_path.split("."))).get("type", "")),
                )
                for field_path, value in _flatten(record)
            )
            # Property order is observable schema structure. Earlier IDs are
            # parents of later IDs; no ID prefix or benchmark entity vocabulary
            # is consulted.
            prior_ids: list[str] = []
            for id_field in ids:
                candidate_id = str(record[id_field]).strip()
                key = (candidate_id, ".".join(path))
                if key in seen:
                    continue
                seen.add(key)
                field_schema = (record_schema.get("properties", {}) or {}).get(id_field, {})
                entity_type = str(field_schema.get("x-adapt-entity", "")).strip()
                if not entity_type:
                    entity_type = id_field.casefold().removesuffix("_id") or "entity"
                prefix = id_field.casefold().removesuffix("_id")
                name_value = _semantic_field(
                    record,
                    record_schema,
                    ("name",),
                    (f"{prefix}_name", "name", "title", "label"),
                )
                inventory_value = _semantic_field(
                    record, record_schema, ("inventory",), ("inventory", "stock", "quantity")
                )
                price_value = _semantic_field(
                    record, record_schema, ("price",), ("price", "amount", "cost")
                )
                try:
                    inventory = int(inventory_value) if inventory_value is not None else None
                except (TypeError, ValueError):
                    inventory = None
                try:
                    price = float(price_value) if price_value is not None else None
                except (TypeError, ValueError):
                    price = None
                manifests.append(
                    CandidateManifest(
                        candidate_id=candidate_id,
                        entity_type=entity_type,
                        name=str(name_value or ""),
                        tool_name=tool_name,
                        record_path=".".join(path),
                        fields=flattened,
                        parent_ids=tuple(prior_ids),
                        inventory=inventory,
                        price=price,
                        raw_record=record,
                    )
                )
                prior_ids.append(candidate_id)
        return tuple(manifests)


@dataclass(frozen=True)
class WorkflowNode:
    tool_name: str
    role: str
    input_entities: tuple[str, ...]
    output_entities: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowEdge:
    producer: str
    consumer: str
    entity_type: str


@dataclass(frozen=True)
class ExecutionWorkflowGraph:
    nodes: tuple[WorkflowNode, ...]
    edges: tuple[WorkflowEdge, ...]

    @classmethod
    def compile(cls, contracts: Iterable[Any]) -> "ExecutionWorkflowGraph":
        nodes = tuple(
            WorkflowNode(
                contract.name,
                contract.role,
                tuple(sorted({v.entity_type for v in contract.required_id_variables})),
                tuple(sorted(set(getattr(contract, "observation_entities", ())) | ({contract.observation_entity} if contract.observation_entity else set()))),
            )
            for contract in contracts
        )
        edges = tuple(
            WorkflowEdge(left.tool_name, right.tool_name, entity)
            for left in nodes
            for right in nodes
            if left.tool_name != right.tool_name
            for entity in sorted(set(left.output_entities) & set(right.input_entities))
        )
        return cls(nodes, edges)


@dataclass(frozen=True)
class SearchPlan:
    instruction: str
    required_qualifiers: tuple[str, ...]
    forbidden_qualifiers: tuple[str, ...]
    allowed_tool_families: tuple[str, ...]
    query_variants: tuple[str, ...]
    coverage_status: str = "unobserved"

    @classmethod
    def compile(cls, spec: Any, contracts: Iterable[Any]) -> "SearchPlan":
        required = tuple(dict.fromkeys(str(item.value) for item in spec.must if item.value))
        forbidden = tuple(dict.fromkeys(str(item.value) for item in spec.avoid if item.value))
        families = tuple(sorted({item.family for item in contracts if item.role == "search"}))
        variants = tuple(dict.fromkeys((spec.instruction, *required)))
        return cls(spec.instruction, required, forbidden, families, variants)
