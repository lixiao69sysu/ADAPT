"""Immutable tool contracts compiled from observable tool schemas.

The compiler deliberately knows nothing about VitaBench entity names.  It
turns :class:`ToolMeta` objects into a small structural language consumed by
the binding graph, candidate decision layer, lineage checks and question
planner.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class IdVariable:
    """One ID-bearing input in a tool contract."""

    argument: str
    entity_type: str
    required: bool
    many: bool = False


@dataclass(frozen=True)
class ArgumentContract:
    name: str
    required: bool
    json_type: str = ""
    source_hint: str = ""
    question: str = ""
    description: str = ""
    persist_as_preference: bool = False
    # Preserve the observable JSON-schema fragment so normalization can be
    # recursive (not just aware of the outer ``array`` wrapper).  A copied
    # fragment keeps the contract independent from the environment object.
    json_schema: dict[str, Any] = field(
        default_factory=dict, compare=False, repr=False
    )


@dataclass(frozen=True)
class ActionCapability:
    """What a CREATE schema can express, distinct from candidate evidence.

    ``request`` means the environment accepts a user-visible request field;
    it is intentionally *not* proof that a candidate intrinsically satisfies
    that request.  This distinction prevents an echoed order note from being
    mistaken for fulfillment evidence.
    """

    mode: str = "intrinsic"  # intrinsic | requested | none
    request_arguments: tuple[str, ...] = ()
    list_request_arguments: tuple[str, ...] = ()

    @property
    def can_transmit_request(self) -> bool:
        return self.mode == "requested" and bool(self.request_arguments)


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
    arguments: tuple[ArgumentContract, ...] = ()
    action_capability: ActionCapability = ActionCapability()

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
            arguments = tuple(
                ArgumentContract(
                    name=argument,
                    required=argument in required,
                    json_type=str(schema.get("type", "") or ""),
                    source_hint=str(schema.get("x-adapt-source", "") or ""),
                    question=str(
                        schema.get("x-adapt-question-text", "")
                        or getattr(meta, "question_arguments", {}).get(argument, "")
                    ),
                    description=str(schema.get("description", "") or ""),
                    persist_as_preference=bool(
                        schema.get("x-adapt-persist-preference", False)
                    ),
                    json_schema=deepcopy(schema),
                )
                for argument, schema in sorted(
                    getattr(meta, "argument_schemas", {}).items()
                )
            )
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
                arguments=arguments,
                action_capability=ToolContractCompiler._action_capability(
                    role, arguments
                ),
            )
        return contracts

    @staticmethod
    def _action_capability(
        role: str, arguments: tuple[ArgumentContract, ...]
    ) -> ActionCapability:
        if role != "create":
            return ActionCapability(mode="none")
        request_arguments: list[str] = []
        list_arguments: list[str] = []
        for argument in arguments:
            # This is schema semantics, not a product/category vocabulary.
            # Providers commonly expose request fields as note/remark/comment
            # or describe them as attributes/options/customization.
            text = " ".join(
                (
                    argument.name,
                    argument.source_hint,
                    argument.description,
                    argument.question,
                )
            ).casefold()
            if not any(
                marker in text
                for marker in (
                    "note", "remark", "comment", "memo", "message",
                    "备注", "留言", "说明", "属性", "规格", "选项",
                    "attribute", "spec", "option", "custom",
                )
            ):
                continue
            request_arguments.append(argument.name)
            if argument.json_type == "array":
                list_arguments.append(argument.name)
        if not request_arguments:
            return ActionCapability(mode="intrinsic")
        ordered = [
            name for name in request_arguments if name not in list_arguments
        ] + list_arguments
        return ActionCapability(
            mode="requested",
            request_arguments=tuple(ordered),
            list_request_arguments=tuple(list_arguments),
        )
