"""Schema/topology-derived candidate bindings.

This module is the functional core for ID compatibility.  It never mutates a
ledger, never calls an environment, and contains no product, hotel, room or
other benchmark entity vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any, Iterable

from agent.runtime.contracts import IdVariable, ToolContract


@dataclass(frozen=True)
class BindingNode:
    candidate_id: str
    entity_type: str
    parent_ids: tuple[str, ...]
    inventory: int | None = None


@dataclass(frozen=True)
class BoundArguments:
    tool_name: str
    arguments: tuple[tuple[str, Any], ...]
    leaf_ids: tuple[str, ...]
    parent_ids: tuple[str, ...]
    provenance: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return dict(self.arguments)


class CandidateBindingGraph:
    """Immutable view of candidates and observed parent/child edges."""

    def __init__(self, nodes: Iterable[BindingNode], *, epoch: int = 0) -> None:
        self.epoch = int(epoch)
        self.nodes = {node.candidate_id: node for node in nodes}
        self.children_by_parent: dict[str, set[str]] = {}
        for node in self.nodes.values():
            for parent_id in node.parent_ids:
                self.children_by_parent.setdefault(parent_id, set()).add(
                    node.candidate_id
                )

    @classmethod
    def from_ledger(cls, ledger: Any, *, epoch: int = 0) -> "CandidateBindingGraph":
        return cls(
            (
                BindingNode(
                    candidate_id=candidate.candidate_id,
                    entity_type=candidate.entity_type,
                    parent_ids=tuple(candidate.parent_ids),
                    inventory=candidate.inventory,
                )
                for candidate in ledger.candidates.values()
            ),
            epoch=epoch,
        )

    def structural_leaf_ids(self) -> tuple[str, ...]:
        parents = {
            parent_id
            for node in self.nodes.values()
            for parent_id in node.parent_ids
            if parent_id in self.nodes
        }
        leaves = [node_id for node_id in self.nodes if node_id not in parents]
        return tuple(sorted(leaves or self.nodes))

    def _candidate_ids_for(
        self,
        variable: IdVariable,
        all_variables: tuple[IdVariable, ...],
    ) -> tuple[str, ...]:
        exact = tuple(
            sorted(
                node.candidate_id
                for node in self.nodes.values()
                if node.entity_type == variable.entity_type
            )
        )
        if exact:
            return exact
        unresolved_types = {
            item.entity_type
            for item in all_variables
            if item.entity_type != "user"
            and not any(
                node.entity_type == item.entity_type for node in self.nodes.values()
            )
        }
        exact_expected_types = {
            item.entity_type
            for item in all_variables
            if any(
                node.entity_type == item.entity_type for node in self.nodes.values()
            )
        }
        leaf_types = {
            self.nodes[node_id].entity_type for node_id in self.structural_leaf_ids()
        } - exact_expected_types
        topology_child_types = {
            node.entity_type
            for node in self.nodes.values()
            if any(parent_id in self.nodes for parent_id in node.parent_ids)
        } - exact_expected_types
        fallback_types = topology_child_types or leaf_types
        # Conservative legacy fallback: one unresolved schema type may bind to
        # one observed leaf type.  Ambiguity is never guessed.
        if len(unresolved_types) == 1 and len(fallback_types) == 1:
            return tuple(
                node_id
                for node_id in self.structural_leaf_ids()
                if self.nodes[node_id].entity_type in fallback_types
            )
        return ()

    def enumerate_bindings(
        self,
        contract: ToolContract,
        *,
        fixed_arguments: dict[str, Any] | None = None,
        limit: int = 256,
    ) -> tuple[BoundArguments, ...]:
        """Enumerate structurally valid bindings for a CREATE-like contract."""

        fixed = dict(fixed_arguments or {})
        variables = contract.required_id_variables
        choices: list[tuple[str, ...]] = []
        bind_variables: list[IdVariable] = []
        for variable in variables:
            if variable.argument in fixed:
                values = fixed[variable.argument]
                values = values if isinstance(values, list) else [values]
                choices.append(tuple(str(value) for value in values))
            else:
                candidates = self._candidate_ids_for(variable, variables)
                if contract.role == "create" and len(variables) == 1:
                    structural_leaves = set(self.structural_leaf_ids())
                    candidates = tuple(
                        candidate_id
                        for candidate_id in candidates
                        if candidate_id in structural_leaves
                    )
                choices.append(candidates)
            bind_variables.append(variable)
        if any(not choice for choice in choices):
            return ()
        if not bind_variables:
            return (
                BoundArguments(
                    contract.name,
                    tuple(sorted(fixed.items())),
                    (),
                    (),
                    (),
                ),
            )
        bindings: list[BoundArguments] = []
        for combination in product(*choices):
            # Two distinct required schema variables cannot silently collapse
            # onto one candidate node.  If a tool intentionally reuses an ID,
            # its schema should expose one variable rather than two aliases.
            if len(set(combination)) != len(combination):
                continue
            arguments = dict(fixed)
            for variable, candidate_id in zip(bind_variables, combination):
                arguments[variable.argument] = (
                    [candidate_id] if variable.many else candidate_id
                )
            failures = self.validate_arguments(contract, arguments)
            if failures:
                continue
            selected = tuple(dict.fromkeys(combination))
            referenced_parents = {
                parent_id
                for candidate_id in selected
                for parent_id in self.nodes[candidate_id].parent_ids
                if parent_id in selected
            }
            leaves = tuple(
                candidate_id
                for candidate_id in selected
                if candidate_id not in referenced_parents
            )
            bindings.append(
                BoundArguments(
                    tool_name=contract.name,
                    arguments=tuple(sorted(arguments.items())),
                    leaf_ids=leaves or selected,
                    parent_ids=tuple(sorted(referenced_parents)),
                    provenance=tuple(
                        f"candidate:{candidate_id}@{self.epoch}"
                        for candidate_id in selected
                    ),
                )
            )
            if len(bindings) >= limit:
                break
        return tuple(bindings)

    def validate_arguments(
        self, contract: ToolContract, arguments: dict[str, Any]
    ) -> tuple[str, ...]:
        failures: list[str] = []
        variables = {variable.argument: variable for variable in contract.id_variables}
        selected_by_argument: dict[str, tuple[str, ...]] = {}
        for argument, variable in variables.items():
            if variable.entity_type == "user" or argument not in arguments:
                continue
            raw = arguments[argument]
            values = raw if isinstance(raw, list) else [raw]
            ids = tuple(str(value) for value in values)
            selected_by_argument[argument] = ids
            candidates = self._candidate_ids_for(variable, contract.required_id_variables)
            for candidate_id in ids:
                if candidate_id not in self.nodes:
                    failures.append(f"{argument}={candidate_id} has no current candidate provenance")
                elif candidate_id not in candidates:
                    failures.append(
                        f"{argument} expects {variable.entity_type} ID but "
                        f"{candidate_id} is {self.nodes[candidate_id].entity_type}"
                    )
        for argument, values in selected_by_argument.items():
            selected_others = {
                candidate_id
                for other_argument, other_values in selected_by_argument.items()
                if other_argument != argument
                for candidate_id in other_values
                if candidate_id in self.nodes
            }
            for candidate_id in values:
                node = self.nodes.get(candidate_id)
                if not node or not node.parent_ids or not selected_others:
                    continue
                observed_selected_parents = set(node.parent_ids) & selected_others
                selected_children = self.children_by_parent.get(candidate_id, set()) & selected_others
                parent_types = {
                    self.nodes[parent_id].entity_type
                    for parent_id in node.parent_ids
                    if parent_id in self.nodes
                }
                relation_expected = bool(
                    parent_types
                    & {
                        self.nodes[other_id].entity_type
                        for other_id in selected_others
                    }
                )
                if relation_expected and not observed_selected_parents and not selected_children:
                    parent_argument = next(
                        (
                            other_argument
                            for other_argument, other_values in selected_by_argument.items()
                            if other_argument != argument
                            and any(
                                other_id in self.nodes
                                and self.nodes[other_id].entity_type in parent_types
                                for other_id in other_values
                            )
                        ),
                        "selected_parent_id",
                    )
                    parent_value = next(
                        (
                            other_id
                            for other_id in selected_by_argument.get(parent_argument, ())
                            if other_id in self.nodes
                        ),
                        "unknown",
                    )
                    failures.append(
                        f"{argument}={candidate_id} was not observed under "
                        f"{parent_argument}={parent_value}"
                    )
        return tuple(dict.fromkeys(failures))
