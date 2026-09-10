"""ADAPT V2 agent built as a narrow extension of VitaBench stock behavior."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger
from vita.agent.base import ValidAgentInputMessage
from vita.agent.llm_agent import LLMAgentState
from vita.agent.personalization_agent import PersonalizationAgent
from vita.data_model.message import (
    AssistantMessage,
    MultiToolMessage,
    ToolMessage,
    UserMessage,
)

from agent.v2.config import V2FeatureFlags
from agent.v2.executor import StockCompatibleExecutor
from agent.v2.memory import HybridMemory
from agent.v2.observation import ObservationStore
from agent.v2.planner import ActionPlan, ModelPlanner
from agent.v2.transaction import OperationJournal, TransactionKernel
from agent.v2.workspace import DecisionWorkspace, build_workspace

_EVIDENCE_POLICY = """## ADAPT V2 evidence-linked memory
The entries below are soft, historical evidence. They may be incomplete or
conflicting. The user's current instruction and current-turn corrections always
take priority. Decide relevance yourself; do not treat an observation as a hard
constraint merely because it appears here.
"""


class ADAPTV2(PersonalizationAgent):
    """Stock-first agent with independently switchable V2 capabilities.

    With every feature disabled, the inherited system prompt and inherited
    generation method are used directly, preserving the stock model request.
    """

    VERSION = "adapt_v2"

    def __init__(
        self,
        *args: Any,
        feature_flags: V2FeatureFlags | None = None,
        planner: ModelPlanner | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.language = kwargs.get("language")
        self.feature_flags = feature_flags or V2FeatureFlags()
        if self.feature_flags.hybrid_memory and not isinstance(self.memory, HybridMemory):
            self.memory = HybridMemory(
                language=getattr(self.memory, "language", None),
                rewrite=self.memory,
            )
        self.observations = ObservationStore()
        self.operation_journal = OperationJournal()
        self.transaction_kernel = TransactionKernel(
            self.observations, self.operation_journal
        )
        self.planner = planner or ModelPlanner()
        self.executor = StockCompatibleExecutor()
        self.last_workspace: DecisionWorkspace | None = None
        self.last_plan: ActionPlan | None = None
        self.shadow_events: list[dict[str, Any]] = []
        self._turn_id = 0
        self._transaction_replans = 2
        self._active_v2_instruction: str | None = None
        self._configured_tool_names: tuple[str, ...] = ()

    @property
    def system_prompt(self) -> str:
        self._sync_runtime_context()
        base = super().system_prompt
        if not self.feature_flags.hybrid_memory or not isinstance(self.memory, HybridMemory):
            return base
        evidence = self.memory.render_evidence(
            query=self._current_instruction,
            candidate_fields=self.observations.candidate_fields(),
        )
        if not evidence:
            return base
        return f"{base}\n\n{_EVIDENCE_POLICY}{evidence}"

    def process_interactions(self, interactions: list):
        # HybridMemory delegates its primary summary update to stock RewriteMemory
        # and records the same interactions in the evidence store.
        if isinstance(self.memory, HybridMemory):
            # VitaBench exposes history before assigning the next instruction;
            # do not incorrectly scope new evidence to the previous subtask.
            self.memory.set_context("")
        return super().process_interactions(interactions)

    def generate_next_message(
        self, message: ValidAgentInputMessage, state: LLMAgentState
    ) -> tuple[AssistantMessage, LLMAgentState]:
        self._sync_runtime_context()
        tool_names = tuple(str(getattr(tool, "name", tool)) for tool in self.tools)
        if tool_names != self._configured_tool_names:
            self.transaction_kernel.configure_tools(self.tools)
            self._configured_tool_names = tool_names
        self._observe_input(message)
        advisory_messages = list(state.messages)
        if isinstance(message, MultiToolMessage):
            advisory_messages.extend(message.tool_messages)
        else:
            advisory_messages.append(message)

        # This direct inheritance path is the parity contract. Shadow collection
        # is allowed to mutate only V2-local stores, never the request.
        if not self.feature_flags.changes_execution_request:
            if self.feature_flags.shadow_workspace or self.feature_flags.shadow_planner:
                self._prepare_advisory(advisory_messages, shadow=True)
            assistant, state = super().generate_next_message(message, state)
            if self.feature_flags.shadow_transaction and assistant is not None:
                return self._transaction_preflight(assistant, state, "")
            return assistant, state

        workspace, plan = self._prepare_advisory(advisory_messages, shadow=False)
        advisory_parts: list[str] = []
        if self.feature_flags.decision_workspace and workspace is not None:
            advisory_parts.append("## Decision workspace\n" + workspace.render())
        if (self.feature_flags.planner or self.feature_flags.voi_questions) and plan is not None:
            advisory_parts.append(
                "## Advisory action plan\n"
                + plan.render(include_voi=self.feature_flags.voi_questions)
            )
        advisory = "\n\n".join(advisory_parts)
        assistant, state = self.executor.execute(
            message=message,
            state=state,
            model=self.llm,
            tools=self.tools,
            enable_think=self.enable_think,
            llm_args=self.llm_args,
            advisory_context=advisory,
        )
        if assistant is None:
            return assistant, state
        return self._transaction_preflight(assistant, state, advisory)

    def _sync_runtime_context(self) -> None:
        """React to the stock orchestrator's inherited instruction hook."""
        if self._active_v2_instruction == self._current_instruction:
            return
        self.observations.reset()
        self.last_workspace = None
        self.last_plan = None
        instruction = self._current_instruction or ""
        if isinstance(self.memory, HybridMemory):
            self.memory.set_context(instruction)
        self.transaction_kernel.begin_instruction(instruction, self.user_profile)
        self._active_v2_instruction = self._current_instruction

    def _observe_input(self, message: ValidAgentInputMessage) -> None:
        messages = message.tool_messages if isinstance(message, MultiToolMessage) else [message]
        for item in messages:
            if isinstance(item, UserMessage):
                self._turn_id += 1
                self.transaction_kernel.observe_user_turn(
                    str(item.content or ""), self.user_profile
                )
            elif isinstance(item, ToolMessage):
                self.observations.observe(item.name, item.content, self._turn_id)
                self.transaction_kernel.observe_result(item, self._turn_id)

    def _prepare_advisory(
        self, existing_messages: list[Any], *, shadow: bool
    ) -> tuple[DecisionWorkspace | None, ActionPlan | None]:
        wants_workspace = any(
            (
                self.feature_flags.decision_workspace,
                self.feature_flags.planner,
                self.feature_flags.voi_questions,
                self.feature_flags.shadow_workspace,
                self.feature_flags.shadow_planner,
            )
        )
        if not wants_workspace:
            return None, None
        beliefs = []
        if isinstance(self.memory, HybridMemory):
            beliefs = self.memory.belief_store.retrieve(
                self._current_instruction,
                self.observations.candidate_fields(),
            )
        explicit = [f"date={value}" for value in sorted(self.transaction_kernel.explicit_dates)]
        explicit.extend(
            f"address={value}" for value in sorted(self.transaction_kernel.explicit_addresses)
        )
        workspace = build_workspace(
            instruction=self._current_instruction or "",
            messages=existing_messages,
            observation_store=self.observations,
            beliefs=beliefs,
            tools=self.tools,
            operation_states=self.operation_journal.recent(),
            explicit_constraints=explicit,
        )
        self.last_workspace = workspace
        plan = None
        wants_plan = any(
            (
                self.feature_flags.planner,
                self.feature_flags.voi_questions,
                self.feature_flags.shadow_planner,
            )
        )
        if wants_plan:
            plan = self.planner.plan(
                workspace,
                model=self.llm,
                llm_args=self.llm_args,
                observations=self.observations,
            )
            self.last_plan = plan
        if shadow or self.feature_flags.shadow_workspace or self.feature_flags.shadow_planner:
            self.shadow_events.append(
                {
                    "turn_id": self._turn_id,
                    "workspace": workspace.model_dump(mode="json"),
                    "plan": plan.model_dump(mode="json") if plan else None,
                }
            )
        return workspace, plan

    def _transaction_preflight(
        self,
        assistant: AssistantMessage,
        state: LLMAgentState,
        advisory: str,
    ) -> tuple[AssistantMessage, LLMAgentState]:
        if not (
            self.feature_flags.transaction_enforcement
            or self.feature_flags.shadow_transaction
        ):
            return assistant, state

        for attempt in range(self._transaction_replans + 1):
            calls = assistant.tool_calls or []
            validations = [
                self.transaction_kernel.validate(call, self._turn_id, commit=False)
                for call in calls
            ]
            problems = [
                problem
                for result in validations
                for problem in result.problems
            ]
            if self.feature_flags.shadow_transaction:
                self.shadow_events.append(
                    {
                        "turn_id": self._turn_id,
                        "transaction_shadow": {
                            "calls": [call.model_dump(mode="json") for call in calls],
                            "problems": problems,
                        },
                    }
                )
            if not problems or not self.feature_flags.transaction_enforcement:
                if self.feature_flags.transaction_enforcement:
                    for call in calls:
                        self.transaction_kernel.validate(call, self._turn_id, commit=True)
                return assistant, state

            correction = (
                "ADAPT V2 transaction validation rejected the proposed write. "
                "Correct the arguments using only current tool evidence; do not "
                "change the user's explicit date or address:\n- "
                + "\n- ".join(sorted(set(problems)))
            )
            logger.warning(correction)
            for call in calls:
                state.messages.append(
                    ToolMessage(
                        id=call.id or f"adapt-v2-rejected-{attempt}",
                        name=call.name,
                        role="tool",
                        content=correction,
                        requestor="assistant",
                        error=True,
                    )
                )
            if not calls:
                return assistant, state
            assistant, state = self.executor.execute(
                message=None,
                state=state,
                model=self.llm,
                tools=self.tools,
                enable_think=self.enable_think,
                llm_args=self.llm_args,
                advisory_context=advisory,
            )
            if assistant is None:
                break

        fallback = AssistantMessage(
            role="assistant",
            content=(
                "当前操作未通过事务安全校验，因此没有执行写操作。"
                if getattr(self, "language", None) != "english"
                else "The write was not executed because it failed transaction validation."
            ),
        )
        state.messages.append(fallback)
        return fallback, state

    def debug_snapshot(self) -> dict[str, Any]:
        return {
            "agent_version": self.VERSION,
            "feature_flags": self.feature_flags.as_dict(),
            "turn_id": self._turn_id,
            "observations": self.observations.snapshot(),
            "operations": self.operation_journal.recent(limit=len(self.operation_journal.records)),
            "last_workspace": (
                self.last_workspace.model_dump(mode="json") if self.last_workspace else None
            ),
            "last_plan": self.last_plan.model_dump(mode="json") if self.last_plan else None,
            "shadow_events": self.shadow_events,
        }

    def dump_debug_trace(self, path: Path, append: bool = True) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with path.open(mode, encoding="utf-8") as handle:
            handle.write(json.dumps(self.debug_snapshot(), ensure_ascii=False) + "\n")
