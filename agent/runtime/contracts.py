"""Immutable tool contracts compiled from observable tool schemas.

The compiler deliberately knows nothing about VitaBench entity names.  It
turns :class:`ToolMeta` objects into a small structural language consumed by
the binding graph, candidate decision layer, lineage checks and question
planner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class IdVariable:
    """One ID-bearing input in a tool contract."""

    argument: str
    entity_type: str
    required: bool
    many: bool = False


@dataclass(frozen=True)
class ToolContract:
    """Schema-derived execution contract for one visible tool."""

    name: str
    role: str
    required_arguments: tuple[str, ...]
    id_variables: tuple[IdVariable, ...]
    question_arguments: tuple[tuple[str, str], ...] = ()
    observation_entity: str = ""
    state_effect: str = ""

    @property
    def required_id_variables(self) -> tuple[IdVariable, ...]:
        return tuple(
            variable
            for variable in self.id_variables
            if variable.required and variable.entity_type != "user"
        )

    @property
    def id_arguments(self) -> dict[str, str]:
        return {
            variable.argument: variable.entity_type
            for variable in self.id_variables
        }

    @property
    def family(self) -> str:
        """Return a value-free family signature.

        The signature is intentionally structural: renaming a concrete
        candidate ID never changes policy scope, while changing the tool role
        or its input topology does.
        """

        inputs = ",".join(
            sorted(
                f"{variable.entity_type}{'[]' if variable.many else ''}"
                for variable in self.required_id_variables
            )
        )
        output = self.observation_entity or "none"
        return f"{self.role}:{inputs}->{output}"


class ToolContractCompiler:
    """Compile immutable contracts without changing runtime behaviour."""

    @staticmethod
    def compile(metas: Iterable[Any]) -> dict[str, ToolContract]:
        contracts: dict[str, ToolContract] = {}
        for meta in metas:
            role = getattr(getattr(meta, "role", None), "value", None) or str(
                getattr(meta, "role", "read")
            )
            required = tuple(sorted(getattr(meta, "required_arguments", set())))
            id_variables = tuple(
                IdVariable(
                    argument=argument,
                    entity_type=str(entity_type),
                    required=argument in required,
                    many=argument.endswith("_ids"),
                )
                for argument, entity_type in sorted(
                    getattr(meta, "id_arguments", {}).items()
                )
            )
            observation = getattr(meta, "observation_schema", None)
            contracts[meta.name] = ToolContract(
                name=meta.name,
                role=role,
                required_arguments=required,
                id_variables=id_variables,
                question_arguments=tuple(
                    sorted(getattr(meta, "question_arguments", {}).items())
                ),
                observation_entity=str(
                    getattr(observation, "entity_type", "") or ""
                ),
                state_effect=str(getattr(meta, "state_effect", "") or ""),
            )
        return contracts
