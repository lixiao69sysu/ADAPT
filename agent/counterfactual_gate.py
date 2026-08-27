"""Observable pre-evaluation release gate for historical VitaBench traces.

The gate replays only user instructions, public tool schemas, tool calls and
tool results.  It never reads evaluator rubrics, target IDs, reward details or
target/distraction annotations.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agent.vitabench_bootstrap import enable_vitabench_utf8

enable_vitabench_utf8()

from agent.decision import CandidateLedger, TaskSpec, build_decision_card
from agent.runtime import TaskRuntime, ToolOutcomeNormalizer, ToolRegistry, ToolRole
from agent.runtime.argument_binding import ArgumentBindingResolver
from vita.domains.delivery.tools import DeliveryTools
from vita.domains.instore.tools import InStoreTools
from vita.domains.ota.tools import OTATools


@dataclass
class ReplayedSubtask:
    task_id: str
    subtask_index: int
    domain: str
    action: str
    instruction: str
    candidate_results: int = 0
    observed_candidates: int = 0
    had_admissible_binding: bool = False
    final_admissible_bindings: int = 0
    final_selection_basis: str = ""


@dataclass
class CounterfactualGateReport:
    checkpoint: str
    replayed_subtasks: int
    actionable_candidate_subtasks: int
    structurally_unbindable_subtasks: int
    write_calls_checked: int
    raw_write_type_errors: int
    normalized_write_type_errors: int
    normalized_type_error_cases: list[str]
    cases: list[ReplayedSubtask]

    @property
    def passed(self) -> bool:
        return (
            self.normalized_write_type_errors == 0
            and self.structurally_unbindable_subtasks == 0
        )


def _registries() -> dict[str, ToolRegistry]:
    toolkits = {
        "delivery": DeliveryTools(None),
        "instore": InStoreTools(None),
        "ota": OTATools(None),
    }
    registries: dict[str, ToolRegistry] = {}
    for domain, toolkit in toolkits.items():
        registry = ToolRegistry()
        registry.rebuild(list(toolkit.get_tools().values()))
        registries[domain] = registry
    return registries


def _groups(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for message in messages:
        starts = (
            message.get("role") == "assistant"
            and message.get("turn_idx") == 0
            and bool(message.get("content"))
        )
        if starts and current:
            groups.append(current)
            current = []
        current.append(message)
    if current:
        groups.append(current)
    return groups


def _domain_for_group(
    group: list[dict[str, Any]], registries: dict[str, ToolRegistry]
) -> str:
    names = {
        message.get("name", "")
        for message in group
        if message.get("role") == "tool"
    }
    if any(
        name.startswith("instore_") or "instore" in name for name in names
    ):
        return "instore"
    if any(
        marker in name
        for name in names
        for marker in (
            "hotel", "flight", "train", "attraction", "taxi", "ota_"
        )
    ):
        return "ota"
    if any("delivery" in name for name in names):
        return "delivery"
    # No domain tool was called.  Use only the visible instruction fallback.
    instruction = next(
        (
            str(message.get("content", ""))
            for message in group
            if message.get("role") == "user"
            and message.get("content") not in {None, "", "###STOP###"}
        ),
        "",
    )
    return TaskSpec.compile(instruction).domain


def _fixed_arguments(
    registry: ToolRegistry,
    spec: TaskSpec,
    runtime: TaskRuntime,
    profile: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    return {
        name: bound
        for name, contract in registry.contracts.items()
        if contract.role == "create"
        and (
            bound := ArgumentBindingResolver.bind(
                contract, spec, runtime.resolved_slots, profile
            )
        )
    }


def replay_checkpoint(path: str | Path) -> CounterfactualGateReport:
    checkpoint = Path(path)
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    registries = _registries()
    cases: list[ReplayedSubtask] = []
    raw_type_errors = 0
    normalized_type_errors = 0
    normalized_type_error_cases: list[str] = []
    write_calls = 0

    for simulation in payload.get("simulations", []):
        task_id = str(simulation.get("task_id", ""))
        for index, group in enumerate(_groups(simulation.get("messages", [])), 1):
            instruction = next(
                (
                    str(message.get("content", ""))
                    for message in group
                    if message.get("role") == "user"
                    and message.get("content") not in {None, "", "###STOP###"}
                ),
                "",
            )
            if not instruction:
                continue
            domain = _domain_for_group(group, registries)
            registry = registries[domain]
            spec = TaskSpec.compile(instruction, domain_hint=domain)
            runtime = TaskRuntime.begin(spec)
            card = build_decision_card(spec, [])
            ledger = CandidateLedger()
            profile = {"user_id": task_id}
            case = ReplayedSubtask(
                task_id=task_id,
                subtask_index=index,
                domain=domain,
                action=spec.action,
                instruction=instruction,
            )
            decisions = []

            for message in group:
                if message.get("role") == "assistant":
                    for call in message.get("tool_calls") or []:
                        if not isinstance(call, dict):
                            continue
                        name = str(call.get("name", ""))
                        role = registry.role(name)
                        if role not in {
                            ToolRole.CREATE,
                            ToolRole.PAY,
                            ToolRole.CANCEL,
                            ToolRole.MODIFY,
                        }:
                            continue
                        contract = registry.contract(name)
                        if contract is None:
                            continue
                        arguments = dict(call.get("arguments") or {})
                        write_calls += 1
                        raw_type_errors += len(
                            ArgumentBindingResolver.type_errors(
                                contract, arguments
                            )
                        )
                        normalized = ArgumentBindingResolver.normalize_arguments(
                            contract, arguments
                        )
                        remaining_errors = ArgumentBindingResolver.type_errors(
                            contract, normalized
                        )
                        normalized_type_errors += len(remaining_errors)
                        normalized_type_error_cases.extend(
                            f"{task_id}#{index}:{name}:{error}"
                            for error in remaining_errors
                        )
                    continue
                if message.get("role") != "tool" or message.get("error"):
                    continue
                name = str(message.get("name", ""))
                role = registry.role(name)
                if role not in {ToolRole.SEARCH, ToolRole.ENRICH, ToolRole.READ}:
                    continue
                outcome = ToolOutcomeNormalizer.normalize(
                    tool_name=name,
                    tool_role=role.value,
                    content=message.get("content"),
                )
                if not outcome.ok:
                    continue
                before = len(ledger.candidates)
                ledger.observe(
                    name,
                    message.get("content"),
                    registry.result_schema(name),
                )
                if len(ledger.candidates) == before:
                    continue
                case.candidate_results += 1
                ledger.ground_task_constraints(card)
                decision = registry.candidate_decision(
                    ledger,
                    card,
                    runtime=runtime,
                    fixed_arguments=_fixed_arguments(
                        registry, spec, runtime, profile
                    ),
                    profile=profile,
                )
                decisions.append(decision)
                case.had_admissible_binding = (
                    case.had_admissible_binding or bool(decision.admissible)
                )

            case.observed_candidates = len(ledger.candidates)
            if decisions:
                final = decisions[-1]
                case.final_admissible_bindings = len(final.admissible)
                case.final_selection_basis = final.selection_basis
            cases.append(case)

    actionable = [
        case
        for case in cases
        if case.action == "commit" and case.observed_candidates > 0
    ]
    unbindable = [
        case for case in actionable if not case.had_admissible_binding
    ]
    return CounterfactualGateReport(
        checkpoint=str(checkpoint),
        replayed_subtasks=len(cases),
        actionable_candidate_subtasks=len(actionable),
        structurally_unbindable_subtasks=len(unbindable),
        write_calls_checked=write_calls,
        raw_write_type_errors=raw_type_errors,
        normalized_write_type_errors=normalized_type_errors,
        normalized_type_error_cases=normalized_type_error_cases,
        cases=unbindable,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint")
    args = parser.parse_args()
    report = replay_checkpoint(args.checkpoint)
    print(json.dumps(asdict(report) | {"passed": report.passed}, ensure_ascii=False, indent=2))
    if not report.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
