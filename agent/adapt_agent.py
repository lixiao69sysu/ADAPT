"""Complete ADAPT agent assembled outside the read-only VitaBench package."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger

from agent.vitabench_bootstrap import enable_vitabench_utf8

enable_vitabench_utf8()

from vita.agent.base import ValidAgentInputMessage
from vita.agent.llm_agent import LLMAgentState
from vita.agent.personalization_agent import PersonalizationAgent
from vita.data_model.message import (
    AssistantMessage,
    MultiToolMessage,
    SystemMessage,
    ToolMessage,
    ToolCall,
    UserMessage,
)
from vita.utils.llm_utils import generate

from agent.decision import (
    CandidateLedger,
    Constraint,
    ConstraintOperator,
    ConstraintTarget,
    DecisionCard,
    TaskSpec,
    is_search_tool,
    resolve_profile_address,
)
from agent.framework.context import compact_messages
from agent.intent import (
    DesiredOutcome,
    accepts_visible_recommendation,
    selected_ordinal,
)
from agent.lessons import ExecutionLessonStore
from agent.memory.adapt_memory import ADAPTMemory
from agent.runtime import (
    ADAPTAgentState,
    ActionTransaction,
    attribute_preflight_failure,
    CandidateAttributionEngine,
    CallLineageLedger,
    DebugEventStore,
    InformationGap,
    InformationGapContract,
    OperationJournal,
    PaymentDisposition,
    QuestionGate,
    ReplanContext,
    ResponseJournal,
    RuntimePhase,
    RuntimePolicyAdapter,
    RuntimePolicyStore,
    SchemaQuestionPlanner,
    TaskRuntime,
    TrajectoryEvidenceSource,
    ToolErrorLedger,
    ToolEffect,
    ToolOutcomeNormalizer,
    ToolRegistry,
    ToolRole,
    UserEvent,
    UserEventKind,
    default_gap,
)

_STATEFUL_AGENT_ATTRIBUTES = (
    "task_spec", "decision_card", "ledger", "runtime", "tool_registry",
    "tool_errors", "operations", "lineage", "responses", "question_gate",
    "lessons", "runtime_policies", "debug", "_current_instruction",
    "_current_corrections", "_instruction_epoch", "_tool_epoch",
    "_active_context", "_pending_tool_registry",
)

_STATEFUL_MEMORY_ATTRIBUTES = (
    "proactive", "_current_instruction", "_current_task_spec",
    "_current_answers", "_current_session_facts", "_pending_answer",
)

_ADAPT_POLICY = """

## ADAPT decision protocol
1. Treat the current user instruction and corrections as hard constraints. Memory preferences are soft unless marked AVOID.
2. Build the first search from the current instruction, MUST/AVOID, and current-session answers. Historical PREFER facts are post-search ranking evidence only unless the current instruction repeats them.
3. Every ID used in create/book/pay must be copied from the Candidate Ledger. Never invent an ID or substitute a nearby name.
4. Do not choose a larger/smaller, flavored, bundled, room-view, seat, sugar, ice, date, origin or destination variant unless it satisfies MUST exactly.
5. If ASK contains a decision-critical gap, ask one focused question. Do not ask again for information already supplied by the user or account profile.
6. The same search may be attempted at most twice. Then choose from observed candidates, ask the user, or report that no compliant option exists.
7. After create/book, pay only when separately authorized. If payment is declined, finish without paying.
8. Never use evaluator rewards, rubrics, target IDs, or target/distraction annotations. They are not agent observations.
9. When PHASE=ready_to_create, call the exposed CREATE tool immediately with the best compliant Candidate Ledger entry. Do not reconfirm, search, or inspect it again.
"""


@dataclass
class SubtaskContext:
    instruction_epoch: int
    tool_epoch: int
    instruction: str
    tool_registry: ToolRegistry
    has_activity: bool = False

class ADAPTAgent(PersonalizationAgent):
    """PersonalizationAgent with task compilation and guarded execution."""

    def __init__(
        self,
        *args,
        enable_candidate_validation: bool = True,
        enable_lessons: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        if not isinstance(self.memory, ADAPTMemory):
            raise TypeError("ADAPTAgent requires ADAPTMemory")
        self.enable_candidate_validation = enable_candidate_validation
        self.enable_lessons = enable_lessons
        self.task_spec = TaskSpec.compile("")
        self.decision_card = DecisionCard()
        self.ledger = CandidateLedger()
        self.runtime = TaskRuntime.begin(self.task_spec)
        self.tool_registry = ToolRegistry()
        self.tool_errors = ToolErrorLedger()
        self.operations = OperationJournal()
        self.lineage = CallLineageLedger()
        self.responses = ResponseJournal()
        self.question_gate = QuestionGate()
        self.lessons = ExecutionLessonStore(str(self.user_profile.get("user_id", "")))
        self.runtime_policies = RuntimePolicyStore(
            str(self.user_profile.get("user_id", ""))
        )
        self.debug = DebugEventStore(
            {"user_id": str(self.user_profile.get("user_id", ""))}
        )
        self._replan_limit = 2
        self._instruction_epoch = 0
        self._tool_epoch = 0
        self._active_context: SubtaskContext | None = None
        self._pending_tool_registry: ToolRegistry | None = None

    def _capture_framework_state(self) -> dict[str, Any]:
        """Capture all controller values that can change during generation."""
        snapshot = {
            name: deepcopy(getattr(self, name))
            for name in _STATEFUL_AGENT_ATTRIBUTES
            if hasattr(self, name)
        }
        memory_state = {
            name: deepcopy(getattr(self.memory, name))
            for name in _STATEFUL_MEMORY_ATTRIBUTES
            if hasattr(self.memory, name)
        }
        if memory_state:
            snapshot["__memory_runtime__"] = memory_state
        return snapshot

    def _restore_framework_state(self, snapshot: dict[str, Any]) -> None:
        """Restore the caller-owned snapshot before processing a turn."""
        for name, value in snapshot.items():
            if name == "__memory_runtime__":
                for memory_name, memory_value in value.items():
                    setattr(self.memory, memory_name, deepcopy(memory_value))
                continue
            setattr(self, name, deepcopy(value))

    def get_init_state(self, message_history: Optional[list] = None) -> ADAPTAgentState:
        base_state = super().get_init_state(message_history=message_history)
        return ADAPTAgentState(
            system_messages=base_state.system_messages,
            messages=base_state.messages,
            framework_state=self._capture_framework_state(),
        )

    def _operation_journal(self) -> OperationJournal:
        """Return the journal, including lightweight unit-test constructions."""
        journal = getattr(self, "operations", None)
        if journal is None:
            journal = OperationJournal()
            self.operations = journal
        return journal

    def _lineage_ledger(self) -> CallLineageLedger:
        lineage = getattr(self, "lineage", None)
        if lineage is None:
            lineage = CallLineageLedger()
            self.lineage = lineage
        return lineage

    def _candidate_shortlist(
        self, limit: int = 5, registry: ToolRegistry | None = None
    ):
        """Project the single CandidateDecision into a presentation list."""
        active_registry = registry or getattr(self, "tool_registry", None)
        if active_registry is None:
            return self.ledger.shortlist(self.decision_card, limit=limit)
        decision = self._candidate_decision(active_registry)
        candidates = []
        seen: set[str] = set()
        for binding in decision.ordered:
            for candidate_id in binding.leaf_ids:
                if candidate_id in seen or candidate_id not in self.ledger.candidates:
                    continue
                seen.add(candidate_id)
                candidates.append(self.ledger.candidates[candidate_id])
                if len(candidates) >= limit:
                    return candidates
        if candidates:
            return candidates
        if not any(
            contract.role == "create"
            for contract in active_registry.contracts.values()
        ):
            return self.ledger.shortlist(self.decision_card, limit=limit)
        return []

    def _candidate_decision(self, registry: ToolRegistry | None = None):
        active_registry = registry or self.tool_registry
        from agent.runtime.argument_binding import ArgumentBindingResolver

        fixed_arguments: dict[str, dict[str, object]] = {}
        for name, contract in active_registry.contracts.items():
            if contract.role != "create":
                continue
            resolved = ArgumentBindingResolver.bind(
                contract,
                self.task_spec,
                self.runtime.resolved_slots,
                getattr(self, "user_profile", {}),
            )
            if resolved:
                fixed_arguments[name] = resolved
        return active_registry.candidate_decision(
            self.ledger,
            self.decision_card,
            runtime=self.runtime,
            instruction_epoch=getattr(self, "_instruction_epoch", 0),
            fixed_arguments=fixed_arguments,
            profile=getattr(self, "user_profile", {}),
        )

    def set_current_instruction(self, instruction: str):
        previous = self._current_instruction
        # The orchestrator invokes this hook exactly once per subtask. Text is
        # not a subtask identity: two consecutive tasks may legitimately have
        # identical instructions and must still receive fresh operation,
        # question, search and candidate state.
        if previous:
            self._finalize_visible_trajectory()
        if self._pending_tool_registry is not None:
            self.tool_registry = self._pending_tool_registry
            self._pending_tool_registry = None
        self.ledger.reset()
        self.tool_errors.reset()
        self.operations.reset()
        self.lineage.reset()
        self.responses.reset()
        self.memory.begin_subtask(instruction)
        self._current_corrections: list[str] = []
        self.task_spec = TaskSpec.compile(
            instruction, domain_hint=self.tool_registry.domain_hint()
        )
        self.task_spec.resolved_slots.update(
            self.memory.resolve_task_slots(instruction)
        )
        self.decision_card = self.memory.compile_task(
            instruction, spec=self.task_spec
        )
        self.runtime = TaskRuntime.begin(self.task_spec)
        current_user_id = str(self.user_profile.get("user_id", ""))
        if current_user_id != self.lessons.user_id:
            self.lessons.reset(current_user_id)
        self.lessons.begin_subtask()
        self.runtime_policies.begin_subtask(current_user_id)
        runtime_policy = self.runtime_policies.policy(
            self.task_spec.domain,
            self.task_spec.facet,
            tool_family=self.ledger.policy_tool_family(),
            entity_signature=self.ledger.policy_entity_signature(),
        )
        if self.enable_lessons:
            RuntimePolicyAdapter.apply(
                runtime_policy, self.runtime, self.ledger, self.tool_errors
            )
        self.debug.emit(
            "runtime_policy_applied",
            instruction=instruction,
            domain=self.task_spec.domain,
            facet=self.task_spec.facet,
            capabilities=list(runtime_policy.active_capabilities),
            max_searches=self.ledger.max_searches_per_family,
            force_decision=self.runtime.force_decision_after_candidates,
            require_max_preference_coverage=(
                self.ledger.require_max_preference_coverage
            ),
        )
        self.debug.emit(
            "subtask_begin",
            instruction=instruction,
            domain=self.task_spec.domain,
            facet=self.task_spec.facet,
            action=self.task_spec.action,
        )
        self._instruction_epoch += 1
        self._active_context = SubtaskContext(
            self._instruction_epoch,
            self._tool_epoch,
            instruction,
            self.tool_registry,
        )
        super().set_current_instruction(instruction)

    @property
    def system_prompt(self) -> str:
        # PersonalizationAgent.system_prompt always injects memory.read().
        # ADAPT owns a phase-aware Decision Card instead: exposing that base
        # memory block here would leak historical entities into the first
        # search before candidate-induced grounding can establish relevance.
        prompt_time = getattr(self, "time", None)
        base = self.domain_policy.format(time=prompt_time or "")
        profile_text = self._format_user_profile()
        if profile_text:
            base += (
                "\n\n## 当前用户基础信息\n"
                "以下是该用户的账户注册信息，可直接使用：\n"
                f"{profile_text}"
            )
        lessons = (
            self.lessons.render(self.task_spec.domain, self.task_spec.facet)
            if self.enable_lessons
            else ""
        )
        decision = self._candidate_decision()
        entity_types = {
            self.ledger.candidates[candidate_id].entity_type
            for binding in decision.admissible
            for candidate_id in binding.leaf_ids
            if candidate_id in self.ledger.candidates
        }
        ledger = self.ledger.render(
            self.decision_card,
            entity_types=entity_types or self.ledger.structural_leaf_types(),
        )
        additions = [_ADAPT_POLICY]
        additions.append(
            "## Current instruction\n" + (self.task_spec.instruction or "none")
        )
        if self.runtime.phase in {RuntimePhase.START, RuntimePhase.SEARCH}:
            additions.append(self.decision_card.render_for_search())
        else:
            additions.append(self.decision_card.render())
        additions.append(self.runtime.render())
        if lessons:
            additions.append(lessons)
        if ledger:
            additions.append(ledger)
        return base + "\n\n" + "\n\n".join(additions)

    def update_tools(self, tools):
        # Tool schemas are observations; evaluator/task labels are never accepted.
        self._tool_names = {tool.name for tool in tools}
        registry = ToolRegistry()
        registry.rebuild(tools)
        self._tool_epoch += 1
        if self._active_context is not None and self._active_context.has_activity:
            self._pending_tool_registry = registry
            self.debug.emit(
                "tools_staged_for_next_subtask", tool_epoch=self._tool_epoch
            )
        else:
            self.tool_registry = registry
            self._bind_current_task_to_tools()
        super().update_tools(tools)

    def _bind_current_task_to_tools(self) -> None:
        """Bind tools only before the first observation of an instruction."""
        if not self._current_instruction:
            return
        self.task_spec = TaskSpec.compile(
            self._current_instruction, domain_hint=self.tool_registry.domain_hint()
        )
        self.task_spec.resolved_slots.update(
            self.memory.resolve_task_slots(
                self._current_instruction, spec=self.task_spec
            )
        )
        self.decision_card = self.memory.compile_task(
            self._current_instruction, spec=self.task_spec
        )
        self.runtime = TaskRuntime.begin(self.task_spec)
        runtime_policy = self.runtime_policies.policy(
            self.task_spec.domain,
            self.task_spec.facet,
            tool_family=self.ledger.policy_tool_family(),
            entity_signature=self.ledger.policy_entity_signature(),
        )
        if self.enable_lessons:
            RuntimePolicyAdapter.apply(
                runtime_policy, self.runtime, self.ledger, self.tool_errors
            )
        if self._active_context is not None:
            self._active_context.tool_registry = self.tool_registry
            self._active_context.tool_epoch = self._tool_epoch
        self.debug.emit(
            "task_context_bound",
            source="tool_topology",
            domain=self.task_spec.domain,
            facet=self.task_spec.facet,
        )

    def process_interactions(self, interactions: list):
        """Keep the task-local card synchronized with incremental memory.

        VitaBench sets the next instruction before delivering that subtask's
        newly visible history. Without this refresh, system_prompt reads the
        new memory while framework gates continue using the pre-update card.
        """
        result = super().process_interactions(interactions)
        self._refresh_decision_after_memory_update()
        return result

    def _refresh_decision_after_memory_update(self) -> None:
        if not self._current_instruction:
            return
        try:
            self.decision_card = self.memory.compile_task(
                self._current_instruction, spec=self.task_spec
            )
        except TypeError:
            # Preserve compatibility with minimal memory doubles while the
            # production ADAPTMemory always accepts the typed task spec.
            self.decision_card = self.memory.compile_task(
                self._current_instruction
            )
        resolver = getattr(self.memory, "resolve_task_slots", None)
        if resolver:
            try:
                memory_slots = resolver(
                    self._current_instruction, spec=self.task_spec
                )
            except TypeError:
                memory_slots = resolver(self._current_instruction)
        else:
            memory_slots = {}
        self.task_spec.resolved_slots.update(memory_slots)
        runtime = getattr(self, "runtime", None)
        if runtime is not None:
            runtime.apply_memory_slots(memory_slots)
        storage_stats = getattr(self.memory, "storage_stats", lambda: {})()
        self.debug.emit(
            "decision_card_refreshed",
            facet=self.task_spec.facet,
            must=len(self.decision_card.must),
            avoid=len(self.decision_card.avoid),
            prefer=len(self.decision_card.prefer),
            **storage_stats,
        )

    def generate_next_message(
        self, message: ValidAgentInputMessage, state: LLMAgentState
    ) -> tuple[AssistantMessage, ADAPTAgentState]:
        """Generate from caller-owned state so VitaBench retries fully roll back."""
        if isinstance(state, ADAPTAgentState):
            working_state = state.model_copy(deep=True)
            self._restore_framework_state(working_state.framework_state)
        else:
            # Backward compatibility for old checkpoints and minimal test states.
            working_state = ADAPTAgentState(
                system_messages=deepcopy(state.system_messages),
                messages=deepcopy(state.messages),
                framework_state=self._capture_framework_state(),
            )
        assistant, updated = self._generate_next_message_impl(message, working_state)
        return assistant, ADAPTAgentState(
            system_messages=updated.system_messages,
            messages=updated.messages,
            framework_state=self._capture_framework_state(),
        )

    def _generate_next_message_impl(
        self, message: ValidAgentInputMessage, state: LLMAgentState
    ) -> tuple[AssistantMessage, LLMAgentState]:
        self._observe_input(message)
        if isinstance(message, MultiToolMessage):
            state.messages.extend(message.tool_messages)
        else:
            state.messages.append(message)

        if self.runtime.phase == RuntimePhase.DONE:
            completion = self._terminal_response()
            self._emit_framework_message(state, completion, "completion")
            self.debug.emit("runtime_completed", facet=self.task_spec.facet)
            return completion, state

        payment_question = self._framework_payment_question()
        if payment_question:
            assistant = AssistantMessage(role="assistant", content=payment_question)
            self._emit_framework_message(state, assistant, "payment_question")
            self.debug.emit("payment_question", facet=self.task_spec.facet)
            return assistant, state

        parameter_probe = self._framework_parameter_probe()
        if parameter_probe:
            self._emit_framework_message(state, parameter_probe, "tool_proposal")
            return parameter_probe, state

        recovered_write = self._framework_recovered_write()
        if recovered_write:
            self._emit_framework_message(state, recovered_write, "tool_proposal")
            return recovered_write, state

        enrichment = self._framework_enrichment()
        if enrichment:
            self._emit_framework_message(state, enrichment, "tool_proposal")
            self.debug.emit(
                "hierarchical_enrichment",
                count=len(enrichment.tool_calls or []),
                facet=self.task_spec.facet,
            )
            return enrichment, state

        recommendation = self._framework_recommendation()
        if recommendation:
            displayed = tuple(
                candidate.candidate_id
                for candidate in self._candidate_shortlist(limit=3)
                if candidate.name
            )
            self._emit_framework_message(
                state,
                recommendation,
                "recommendation",
                candidate_ids=displayed,
            )
            self.debug.emit(
                "recommendation_finalized",
                candidates=len(self._candidate_shortlist(limit=3)),
                facet=self.task_spec.facet,
            )
            return recommendation, state

        # A once-only response guard may close the runtime without producing a
        # second recommendation. Do not fall through into the policy model.
        if self.runtime.phase == RuntimePhase.DONE:
            completion = self._terminal_response()
            self._emit_framework_message(state, completion, "completion")
            return completion, state

        question = self._framework_question()
        if question:
            assistant = AssistantMessage(role="assistant", content=question)
            self._emit_framework_message(state, assistant, "information_question")
            return assistant, state

        replan_context = ReplanContext()
        for attempt in range(self._replan_limit + 1):
            self._refresh_system_message(state)
            compact_messages(state.messages)
            allowed_tools = self.tool_registry.allowed_tools(self.runtime, self.ledger)
            self.debug.emit(
                "decision_surface",
                phase=self.runtime.phase.value,
                allowed_tool_count=len(allowed_tools),
                allowed_roles=sorted(
                    {self.tool_registry.role(tool.name).value for tool in allowed_tools}
                ),
                attempt=attempt,
            )
            if not allowed_tools and self.runtime.phase in {
                RuntimePhase.READY_TO_CREATE,
                RuntimePhase.READY_TO_PAY,
                RuntimePhase.READY_TO_WORKFLOW,
            }:
                self.debug.emit(
                    "failure_attributed",
                    owner="framework",
                    stage="decision_surface",
                    reason="an irreversible phase has no executable exposed tool",
                )
            generation_messages = self._generation_messages(
                state, allowed_tools, attempt, replan_context
            )
            assistant = generate(
                model=self.llm,
                tools=allowed_tools,
                messages=generation_messages,
                enable_think=self.enable_think,
                **self.llm_args,
            )
            if assistant is None:
                return assistant, state

            transaction = ActionTransaction.prepare(
                assistant, self._prepare_tool_call
            )
            valid = transaction.validate(
                lambda prepared, exposed=allowed_tools: self._preflight(
                    prepared, exposed
                )
            )
            if valid:
                transaction.emit(state.messages)
                transaction.commit(self._observe_assistant)
                return transaction.prepared, state

            problems = list(transaction.issues)
            correction = "ADAPT preflight rejected this action:\n- " + "\n- ".join(
                problems
            )
            logger.warning(correction)
            self.debug.emit("preflight_rejected", attempt=attempt, problems=problems)
            attribution = attribute_preflight_failure(
                problems,
                allowed_tool_count=len(allowed_tools),
                proposed_call_count=len(transaction.prepared.tool_calls or []),
            )
            self.debug.emit(
                "failure_attributed",
                owner=attribution.owner.value,
                stage=attribution.stage,
                reason=attribution.reason,
            )
            replan_context = ReplanContext(tuple(problems))

        if self.runtime.phase == RuntimePhase.READY_TO_PAY:
            fallback_content = "订单已创建，目前尚未支付。"
        else:
            self.runtime.phase = RuntimePhase.UNSATISFIABLE
            fallback_content = "现有候选无法满足硬约束，我没有执行下单。"
        fallback = AssistantMessage(role="assistant", content=fallback_content)
        state.messages.append(fallback)
        return fallback, state

    def _generation_messages(
        self,
        state,
        allowed_tools,
        attempt: int,
        replan_context: ReplanContext | None = None,
    ):
        """Use a phase-focused context once observation is complete.

        Keeping the full search transcript in READY_TO_CREATE anchors smaller
        policy models to the earlier search call even though that tool is no
        longer exposed. The system prompt already contains the compact
        Candidate Ledger, so retain only the latest user turn and an explicit
        controller directive during irreversible phases.
        """
        if self.runtime.phase not in {
            RuntimePhase.READY_TO_CREATE,
            RuntimePhase.READY_TO_PAY,
            RuntimePhase.READY_TO_WORKFLOW,
        }:
            messages = list(state.system_messages) + list(state.messages)
            if replan_context and replan_context.issues:
                messages.append(
                    UserMessage(role="user", content=replan_context.render())
                )
            return messages
        latest_user = next(
            (
                message
                for message in reversed(state.messages)
                if isinstance(message, UserMessage)
            ),
            None,
        )
        allowed_names = ", ".join(tool.name for tool in allowed_tools) or "none"
        chosen_candidate = None
        if (
            self.runtime.phase == RuntimePhase.READY_TO_CREATE
            and hasattr(self, "ledger")
            and hasattr(self, "decision_card")
        ):
            if self.runtime.selected_candidate_id:
                chosen_candidate = self.ledger.candidates.get(
                    self.runtime.selected_candidate_id
                )
        candidate_directive = ""
        if chosen_candidate is not None:
            parent_ids = ", ".join(chosen_candidate.parent_ids) or "none"
            candidate_directive = (
                " Explicitly user-selected candidate: "
                f"ID={chosen_candidate.candidate_id}; "
                f"exact name={chosen_candidate.name}; parent IDs={parent_ids}. "
                "Use this exact candidate and its observed parent IDs."
            )
        if self.runtime.phase == RuntimePhase.READY_TO_CREATE:
            action = (
                "Call exactly one exposed CREATE tool now using the selected "
                "ExecutionPlan, the best compliant Candidate Ledger IDs, and "
                f"all required arguments.{candidate_directive}"
            )
        elif self.runtime.phase == RuntimePhase.READY_TO_PAY:
            action = "Call the exposed PAY tool now using the pending observed order ID."
        else:
            action = (
                "Call exactly one exposed workflow tool now using only the "
                "observed state ID and the user's requested change."
            )
        directive = (
            "## Runtime controller\n"
            f"Focused irreversible phase, replan attempt {attempt + 1}. "
            f"Allowed tools: {allowed_names}. {action} "
            "Do not call any search/read tool, ask a question, or answer "
            "with text only. The Candidate Ledger in the system prompt is "
            "the complete observation snapshot."
        )
        if replan_context and replan_context.issues:
            directive += "\n" + replan_context.render()
        # Some OpenAI-compatible Qwen servers accept only one system message,
        # even when multiple system messages are consecutive at the beginning.
        system_content = "\n\n".join(
            message.content or "" for message in state.system_messages
        )
        focused = [
            SystemMessage(role="system", content=f"{system_content}\n\n{directive}")
        ]
        if latest_user is not None:
            focused.append(latest_user)
        return focused

    def _emit_framework_message(
        self,
        state: LLMAgentState,
        assistant: AssistantMessage,
        response_type: str,
        *,
        candidate_ids: tuple[str, ...] = (),
    ) -> None:
        """Emit first, then commit every framework-side state mutation."""
        transaction = ActionTransaction.prepare(assistant)
        transaction.emit(state.messages)

        def commit(message: AssistantMessage) -> None:
            if response_type == "tool_proposal":
                self._observe_assistant(message)
                return
            if response_type == "payment_question":
                self.runtime.commit_payment_question()
                return
            if response_type == "information_question":
                gap = self._information_gap_contract().next_gap(
                    resolved=self.runtime.resolved_slots,
                    asked=self.runtime.asked_dimensions,
                )
                if gap is not None:
                    self.runtime.commit_question(
                        gap.argument_name or gap.dimension,
                        question_id=gap.question_id,
                        tool_family=gap.tool_family,
                        expected_type=gap.expected_type,
                        persist_as_preference=gap.persist_as_preference,
                    )
                    self.memory.commit_question(message.content or gap.question)
                    self.debug.emit(
                        "question_committed",
                        dimension=gap.dimension,
                        source=gap.source,
                    )
                return
            epoch = self._operation_journal().epoch
            self.responses.commit(
                epoch,
                self.ledger.candidate_version,
                response_type,
                candidate_ids=candidate_ids,
            )
            if response_type == "recommendation":
                self.runtime.phase = RuntimePhase.DONE

        transaction.commit(commit)

    def _framework_payment_question(self) -> str:
        if self.runtime.phase != RuntimePhase.READY_TO_PAY:
            return ""
        if self.runtime.can_execute_payment():
            return ""
        if self.runtime.payment_clarification_pending:
            return "请确认：需要我现在支付吗？如果暂时不支付，我会保留未支付订单并结束当前任务。"
        if self.runtime.has_current_payment_question():
            return ""
        return "订单已创建并处于待支付状态。需要我现在支付吗？"

    def _promote_current_answer(
        self, dimension: str, value: str, evidence: str
    ) -> None:
        """Make an explicit clarification outrank historical soft preferences."""
        if dimension not in {"caffeine", "size", "room_type", "taste", "dessert"} or not value:
            return
        constraint = Constraint(
            dimension,
            value,
            source="current_user_answer",
            hard=True,
            evidence_span=evidence,
        )
        if not any(
            existing.kind == constraint.kind and existing.value == constraint.value
            for existing in self.task_spec.must
        ):
            self.task_spec.must.append(constraint)
        self.decision_card.must.insert(0, value)
        self.decision_card.constraints.insert(0, constraint)

    def _refresh_system_message(self, state: LLMAgentState) -> None:
        content = self.system_prompt
        if state.system_messages:
            state.system_messages[0].content = content
        else:
            state.system_messages.append(SystemMessage(role="system", content=content))

    def _observe_input(self, message: ValidAgentInputMessage) -> None:
        active_context = getattr(self, "_active_context", None)
        if active_context is not None:
            active_context.has_activity = True
        messages = (
            message.tool_messages
            if isinstance(message, MultiToolMessage)
            else [message]
        )
        for item in messages:
            if isinstance(item, ToolMessage):
                role = self.tool_registry.role(item.name)
                outcome = ToolOutcomeNormalizer.normalize(
                    tool_name=item.name,
                    tool_role=role.value,
                    content=item.content,
                    error=item.error,
                )
                lineage = self._lineage_ledger()
                lineage_record = lineage.get(item.id)
                active_context = getattr(self, "_active_context", None)
                tool_epoch = (
                    active_context.tool_epoch
                    if active_context is not None
                    else getattr(self, "_tool_epoch", 0)
                )
                if lineage.records():
                    workflow_ids = (
                        tuple(
                            state_id
                            for state_id in outcome.workflow_ids
                            if lineage_record is None
                            or state_id not in lineage_record.input_ids
                        )
                        if role
                        in {
                            ToolRole.CREATE,
                            ToolRole.PAY,
                            ToolRole.CANCEL,
                            ToolRole.MODIFY,
                            ToolRole.STATE_READ,
                        }
                        else ()
                    )
                    _, lineage_failures = lineage.observe_result(
                        call_id=item.id or "",
                        tool_name=item.name,
                        instruction_epoch=getattr(self, "_instruction_epoch", 0),
                        tool_epoch=tool_epoch,
                        succeeded=outcome.ok,
                        output_state_ids=workflow_ids,
                    )
                    if lineage_failures:
                        self.debug.emit(
                            "tool_result_lineage_rejected",
                            tool=item.name,
                            problems=list(lineage_failures),
                        )
                        continue
                candidates_before = set(self.ledger.candidates)
                if outcome.ok and role in {
                    ToolRole.SEARCH, ToolRole.ENRICH, ToolRole.READ
                }:
                    self.ledger.observe(
                        item.name,
                        item.content,
                        self.tool_registry.result_schema(item.name),
                    )
                elif outcome.ok and role in {
                    ToolRole.CREATE,
                    ToolRole.PAY,
                    ToolRole.CANCEL,
                    ToolRole.MODIFY,
                    ToolRole.STATE_READ,
                }:
                    self.ledger.observe_state(item.name, item.content)
                    if role == ToolRole.STATE_READ:
                        self.runtime.observe_workflow_state(
                            has_state=bool(
                                self.ledger.pending_payment_ids
                                or any(self.ledger.state_ids.values())
                            )
                        )
                if outcome.effect == ToolEffect.CREATED_PENDING_PAYMENT:
                    self.ledger.pending_payment_ids.update(
                        state_id
                        for state_id in outcome.workflow_ids
                        if lineage_record is None
                        or state_id not in lineage_record.input_ids
                    )
                elif outcome.effect in {ToolEffect.PAID, ToolEffect.CANCELLED}:
                    if outcome.workflow_ids:
                        self.ledger.pending_payment_ids.difference_update(
                            outcome.workflow_ids
                        )
                    else:
                        self.ledger.pending_payment_ids.clear()
                attempt = self.tool_errors.observe_result(
                    item.id, item.name, item.content or "", not outcome.ok
                )
                self._operation_journal().observe_result(
                    item.id,
                    item.name,
                    self.tool_registry.role(item.name).value,
                    not outcome.ok,
                )
                self.runtime.observe_tool_outcome(
                    item.name,
                    outcome,
                    raw_error=item.content or "",
                )
                if lineage.records() and lineage_record is not None:
                    lineage.observe_result(
                        call_id=item.id or "",
                        tool_name=item.name,
                        instruction_epoch=getattr(self, "_instruction_epoch", 0),
                        tool_epoch=tool_epoch,
                        succeeded=outcome.ok,
                        output_candidate_ids=tuple(
                            sorted(set(self.ledger.candidates) - candidates_before)
                        ),
                        output_state_ids=(
                            tuple(
                                state_id
                                for state_id in outcome.workflow_ids
                                if state_id not in lineage_record.input_ids
                            )
                            if role
                            in {
                                ToolRole.CREATE,
                                ToolRole.PAY,
                                ToolRole.CANCEL,
                                ToolRole.MODIFY,
                                ToolRole.STATE_READ,
                            }
                            else ()
                        ),
                    )
                self.debug.emit(
                    "tool_result",
                    tool=item.name,
                    error=not outcome.ok,
                    transport_error=item.error,
                    outcome_source=outcome.source,
                    outcome_effect=outcome.effect.value,
                    phase=self.runtime.phase.value,
                    candidates=len(self.ledger.candidates),
                    tracked_attempt=attempt is not None,
                )
                if outcome.ok and role in {
                    ToolRole.SEARCH, ToolRole.ENRICH, ToolRole.READ
                }:
                    grounding_stats = self.memory.apply_candidate_grounding(
                        self.decision_card,
                        [
                            candidate
                            for candidate in self.ledger.candidates.values()
                            if candidate.entity_type not in {"order", "unknown"}
                        ],
                    )
                    schema_constraints = self.ledger.ground_task_constraints(
                        self.decision_card
                    )
                    self.debug.emit("candidate_memory_retrieval", **grounding_stats)
                    self.debug.emit(
                        "candidate_schema_constraints",
                        count=len(schema_constraints),
                        values=[constraint.value for constraint in schema_constraints],
                    )
                    self._apply_structural_runtime_policy()
                    decision = self._candidate_decision()
                    self.runtime.apply_candidate_decision(decision)
                    self.debug.emit(
                        "candidate_decision",
                        admissible=len(decision.admissible),
                        ordered=len(decision.ordered),
                        next_phase=decision.next_phase.value,
                        selection_basis=decision.selection_basis,
                        missing_arguments=list(decision.missing_arguments),
                        enrichment_requests=len(decision.needs_enrichment),
                    )
                    self._emit_candidate_attribution(decision)
                    self._emit_preference_alignment()
                if not outcome.ok:
                    self.debug.emit(
                        "failure_attributed",
                        owner="environment",
                        stage="tool_result",
                        reason="an emitted tool call returned an observable failure",
                        tool=item.name,
                    )
                    self._record_lesson(
                        "tool_error",
                        item.content or item.name,
                        "Do not repeat the same invalid arguments; use exact IDs and constraints from the latest tool result.",
                    )
            elif isinstance(item, UserMessage):
                text = item.content or ""
                pending_dimension = self.runtime.pending_question_dimension
                pending_question_id = self.runtime.pending_question_id
                pending_persist_as_preference = (
                    self.runtime.pending_question_persist_as_preference
                )
                was_ready_to_pay = self.runtime.phase == RuntimePhase.READY_TO_PAY
                was_done = self.runtime.phase == RuntimePhase.DONE
                user_event = self.runtime.observe_user(text)
                if user_event.kind == UserEventKind.CURRENT_CORRECTION:
                    self._apply_current_correction(text)
                # Resolve presentation references while the recommendation's
                # operation epoch is still current. Selecting a displayed item
                # may open a new transaction epoch immediately afterwards.
                self._resolve_user_selection(text, user_event=user_event)
                if (
                    (was_ready_to_pay and self.runtime.revision_requested)
                    or (
                        was_done
                        and self.runtime.phase != RuntimePhase.DONE
                        and self.runtime.authorization.create_authorized
                    )
                ):
                    epoch = self._operation_journal().begin_new_epoch()
                    self.debug.emit("operation_epoch_started", epoch=epoch)
                self.debug.emit(
                    "user_observation",
                    phase=self.runtime.phase.value,
                    delegated=self.runtime.authorization.choice_delegated,
                    pending_answer_dimension=pending_dimension,
                    user_event=user_event.kind.value,
                    payment_intent=user_event.payment_intent.value,
                )
                resolved_answer = self.runtime.resolved_slots.get(
                    pending_dimension, text
                )
                if (
                    self.memory.proactive.pending_question
                    and resolved_answer != "__delegated__"
                ):
                    # A clarification always updates this task, but becomes a
                    # durable preference only when the public tool schema
                    # explicitly declares it persistent.  A one-off answer
                    # such as a delivery address or today's room type must not
                    # pollute the next subtask.
                    if pending_persist_as_preference:
                        self.memory.record_user_answer(
                            resolved_answer,
                            dimension=pending_dimension,
                            persistent=True,
                        )
                    else:
                        self.memory.acknowledge_task_answer(resolved_answer)
                    self.decision_card = self.memory.compile_task(
                        self.task_spec.instruction,
                        spec=self.task_spec,
                    )
                    self._promote_current_answer(
                        pending_dimension, resolved_answer, text
                    )
                    # A post-search clarification can make the existing ledger
                    # executable. Re-evaluate it instead of forcing a duplicate
                    # search merely because observe_user() returned to SEARCH.
                    if self.ledger.candidates:
                        self.runtime.apply_candidate_decision(
                            self._candidate_decision()
                        )
                elif (
                    not user_event.is_payment
                    and
                    callable(getattr(self.memory, "is_persistent_statement", None))
                    and self.memory.is_persistent_statement(text)
                ):
                    # Outside a schema-marked question, persist only an
                    # explicit future-default statement.  Current corrections
                    # still flow through TaskSpec above and remain local.
                    added = self.memory.record_runtime_preference(
                        text, persistent=True
                    )
                    if added:
                        self.decision_card = self.memory.compile_task(
                            self.task_spec.instruction,
                            spec=self.task_spec,
                        )
                        if self.ledger.candidates:
                            self.runtime.apply_candidate_decision(
                                self._candidate_decision()
                            )
                        self.debug.emit(
                            "persistent_runtime_preference_recorded",
                            facts=added,
                        )
                if user_event.kind == UserEventKind.CURRENT_CORRECTION:
                    self._record_user_correction_lesson(user_event)

    def _apply_current_correction(self, text: str) -> None:
        """Overlay visible user corrections onto the active task contract."""
        correction = (text or "").strip()
        if not correction:
            return
        corrections = getattr(self, "_current_corrections", [])
        corrections.append(correction)
        self._current_corrections = corrections[-4:]
        revised_instruction = "\n".join(
            [
                self._current_instruction or self.task_spec.instruction,
                *(
                    f"当前用户明确纠正：{value}"
                    for value in self._current_corrections
                ),
            ]
        )
        revised = TaskSpec.compile(
            revised_instruction, domain_hint=self.tool_registry.domain_hint()
        )
        # Latest scalar/entity corrections replace older values. Independent
        # negative constraints remain additive because safety sets can coexist.
        latest_by_slot: dict[tuple[object, str, str], Constraint] = {}
        retained: list[Constraint] = []
        for constraint in revised.must:
            if any(
                constraint.evidence_span and constraint.evidence_span in value
                for value in self._current_corrections
            ):
                constraint.source = "user_correction"
            slot = (
                constraint.target,
                constraint.kind,
                constraint.argument_name,
            )
            if constraint.kind == "authorization":
                retained.append(constraint)
            else:
                latest_by_slot[slot] = constraint
        revised.must = [*retained, *latest_by_slot.values()]
        for constraint in revised.avoid:
            if any(
                constraint.evidence_span and constraint.evidence_span in value
                for value in self._current_corrections
            ):
                constraint.source = "user_correction"
        self.task_spec = revised
        self.decision_card = self.memory.compile_task(
            revised_instruction, spec=revised
        )
        self.runtime.spec = revised
        self.runtime.resolved_slots.update(revised.resolved_slots)
        self.runtime.selected_candidate_id = ""
        self.runtime.selection_made = False
        self.runtime.planned_create_tool = ""
        self.runtime.planned_create_arguments = {}
        if self.ledger.candidates and self.runtime.phase not in {
            RuntimePhase.READY_TO_PAY,
            RuntimePhase.WAIT_PAY_RESULT,
        }:
            self.runtime.phase = RuntimePhase.SELECT
            self.runtime.apply_candidate_decision(self._candidate_decision())
        self.debug.emit(
            "current_correction_applied",
            correction=correction[:160],
            must=[constraint.value for constraint in revised.must],
            avoid=[constraint.value for constraint in revised.avoid],
            phase=self.runtime.phase.value,
        )

    def _preflight(self, assistant: AssistantMessage, allowed_tools=None) -> list[str]:
        problems: list[str] = []
        calls = assistant.tool_calls or []
        question_decision = self.question_gate.evaluate(
            assistant.content or "", self.runtime
        )
        has_question = self.question_gate.is_question(assistant.content or "")
        if has_question and calls:
            problems.append(
                "questions must be sent as a standalone assistant message, never attached to tool calls"
            )
        elif has_question and not question_decision.allowed:
            problems.append(f"question gate: {question_decision.reason}")
        allowed_names = {tool.name for tool in (allowed_tools or [])}
        irreversible_calls = [
            call
            for call in calls
            if self.tool_registry.role(call.name)
            in {ToolRole.CREATE, ToolRole.PAY, ToolRole.CANCEL, ToolRole.MODIFY}
        ]
        if len(irreversible_calls) > 1:
            problems.append(
                "one assistant message may contain at most one irreversible tool call"
            )
        for call in calls:
            if call.name not in allowed_names:
                problems.append(
                    f"tool {call.name} is not allowed in phase {self.runtime.phase.value}"
                )
                continue
            role = self.tool_registry.role(call.name)
            problems.extend(
                self.tool_registry.validate_required(call.name, call.arguments)
            )
            problems.extend(
                self.tool_registry.validate_argument_types(
                    call.name, call.arguments
                )
            )
            problems.extend(
                self._operation_journal().validate(
                    role.value, call.name, call.arguments
                )
            )
            failure_reason = self.tool_errors.rejection_reason(
                call.name, call.arguments, role
            )
            if failure_reason:
                problems.append(f"tool failure guard: {failure_reason}")
            meta = self.tool_registry.meta.get(call.name)
            lineage = getattr(self, "lineage", None)
            if meta is not None and lineage is not None and lineage.records():
                input_ids = [
                    str(item)
                    for argument, kind in meta.id_arguments.items()
                    if kind != "user" and argument in call.arguments
                    for item in (
                        call.arguments[argument]
                        if isinstance(call.arguments[argument], list)
                        else [call.arguments[argument]]
                    )
                ]
                problems.extend(
                    lineage.validate_inputs(
                        tool_role=role.value,
                        input_ids=input_ids,
                        instruction_epoch=getattr(self, "_instruction_epoch", 0),
                        operation_epoch=self._operation_journal().epoch,
                        profile_ids=(str(self.user_profile.get("user_id", "")),),
                    )
                )
            if role == ToolRole.ENRICH:
                if self.ledger.enrichment_read_count(call.name, call.arguments) >= 1:
                    problems.append(
                        "this parent detail ID was already read; choose an unexpanded parent ID from the Candidate Ledger"
                    )
            if role == ToolRole.SEARCH:
                count, family_count = self.ledger.preview_search(
                    call.name, call.arguments
                )
                if (
                    count > self.ledger.max_searches_per_family
                    or family_count > self.ledger.max_searches_per_family
                ):
                    problems.append(
                        "semantic search family budget exhausted after "
                        f"{family_count - 1} attempts"
                    )
            if self.enable_candidate_validation:
                tool_meta = self.tool_registry.meta.get(call.name)
                problems.extend(
                    self.ledger.validate_write(
                        call.name,
                        call.arguments,
                        self.decision_card,
                        self.user_profile,
                        tool_meta,
                    )
                )
                if role == ToolRole.CREATE:
                    problems.extend(
                        self.ledger.validate_ranked_choice(
                            call.arguments,
                            self.decision_card,
                            self.runtime.selected_candidate_id,
                            tool_meta.id_arguments if tool_meta else None,
                        )
                    )
            if (
                role == ToolRole.CREATE
                and not self.runtime.authorization.create_authorized
            ):
                problems.append("CREATE is not authorized by the user")
            if role == ToolRole.PAY and not self.runtime.can_execute_payment():
                problems.append("PAY is not authorized by the user")

        if not calls and self.task_spec.action == "commit":
            content = assistant.content or ""
            asks = "?" in content or "？" in content
            if not asks and any(
                word in content for word in ("已下单", "已预订", "马上为您", "帮您下单")
            ):
                problems.append(
                    "text claims or promises execution but contains no WRITE tool call"
                )
        return list(dict.fromkeys(problems))

    def _terminal_response(self) -> AssistantMessage:
        epoch = self._operation_journal().epoch
        already_emitted = self.responses.has(
            epoch, self.ledger.candidate_version, "completion"
        )
        disposition = self.runtime.authorization.payment_disposition
        payment_terminal = {
            PaymentDisposition.DECLINED: "订单已创建，按你的要求没有支付。",
            PaymentDisposition.DEFERRED: "订单已创建并保持未支付，你之后可以继续处理。",
            PaymentDisposition.SELF_PAY: "订单已创建，我没有代为支付。",
            PaymentDisposition.COMPLETED: "订单已创建并支付成功。",
        }.get(disposition)
        response = AssistantMessage(
            role="assistant",
            content=(
                "当前任务已结束，没有重复执行。"
                if already_emitted
                else (
                    payment_terminal
                    or "操作已成功完成。"
                    if self.runtime.write_succeeded
                    else "当前任务已结束，未执行新的操作。"
                )
            ),
        )
        return response

    def _prepare_tool_call(self, call: ToolCall) -> ToolCall:
        prepared = ToolCall(
            id=call.id,
            name=call.name,
            arguments=dict(call.arguments or {}),
        )
        prepared = self._normalize_search_call(prepared)
        role = self.tool_registry.role(prepared.name)
        if role in {ToolRole.CREATE, ToolRole.MODIFY}:
            prepared = self._normalize_profile_arguments(prepared)
        if role in {ToolRole.CANCEL, ToolRole.MODIFY}:
            prepared = self._bind_workflow_arguments(prepared)
        if role == ToolRole.CREATE and self.runtime.planned_create_tool == prepared.name:
            arguments = dict(prepared.arguments or {})
            # CandidateDecision owns entity/profile bindings. The model may
            # supply remaining operational fields, but cannot swap the chosen
            # tool or IDs after deterministic selection.
            arguments.update(self.runtime.planned_create_arguments)
            prepared = ToolCall(
                id=prepared.id,
                name=prepared.name,
                arguments=arguments,
            )
            prepared = self._normalize_profile_arguments(prepared)
        # Recovery is allowed to mutate only the private prepared copy.
        self.tool_errors.recover(prepared.name, prepared.arguments, role)
        contract = self.tool_registry.contract(prepared.name)
        if contract is not None:
            if role == ToolRole.CREATE:
                prepared = self._bind_transmittable_preference_request(
                    prepared, contract
                )
            from agent.runtime.argument_binding import ArgumentBindingResolver

            prepared = ToolCall(
                id=prepared.id,
                name=prepared.name,
                arguments=ArgumentBindingResolver.normalize_arguments(
                    contract, prepared.arguments
                ),
            )
        return prepared

    def _bind_transmittable_preference_request(
        self, call: ToolCall, contract) -> ToolCall:
        """Transmit explicit avoids only through a declared CREATE field.

        This is deliberately one-way: a note/attribute field carries user
        intent to the environment but never proves the chosen candidate has
        fulfilled it.  Candidate and safety validation stay intrinsic.
        """
        capability = getattr(contract, "action_capability", None)
        if capability is None or not capability.can_transmit_request:
            return call
        values: list[str] = []
        for constraint in self.decision_card.constraints:
            operator = getattr(constraint.operator, "value", constraint.operator)
            target = getattr(constraint.target, "value", constraint.target)
            if (
                operator == "excludes"
                and target == "candidate"
                and constraint.value
                and constraint.value not in values
            ):
                values.append(str(constraint.value))
        if not values:
            return call
        # The wording is content-agnostic and visible to the tool provider.
        # Cap it so a large legacy profile cannot monopolize a CREATE payload.
        request = "用户要求：请勿包含" + "、".join(values[:3])
        arguments = dict(call.arguments or {})
        for name in capability.request_arguments:
            if arguments.get(name) not in (None, "", []):
                continue
            arguments[name] = [request] if name in capability.list_request_arguments else request
            return ToolCall(id=call.id, name=call.name, arguments=arguments)
        return call

    def _bind_workflow_arguments(self, call: ToolCall) -> ToolCall:
        """Bind unique observed state/profile IDs and current task arguments."""
        meta = self.tool_registry.meta.get(call.name)
        contract = self.tool_registry.contract(call.name)
        if meta is None or contract is None:
            return call
        arguments = dict(call.arguments or {})
        for name, entity_type in meta.id_arguments.items():
            if arguments.get(name) not in (None, "", []):
                continue
            if entity_type == "user":
                value = self.user_profile.get("user_id")
                if value not in (None, ""):
                    arguments[name] = value
                continue
            observed = self.tool_registry.workflow_state_ids(
                meta, self.ledger, entity_type
            )
            explicit = [
                value for value in observed if value in self.task_spec.instruction
            ]
            selected = explicit or observed
            if len(selected) == 1:
                arguments[name] = selected if name.endswith("_ids") else selected[0]
        from agent.runtime.argument_binding import ArgumentBindingResolver

        planned = ArgumentBindingResolver.bind(
            contract,
            self.task_spec,
            self.runtime.resolved_slots,
            self.user_profile,
        )
        for name, value in planned.items():
            arguments.setdefault(name, value)
        return ToolCall(id=call.id, name=call.name, arguments=arguments)

    def _normalize_profile_arguments(self, call: ToolCall) -> ToolCall:
        """Return a copy with account aliases bound to WRITE arguments."""
        arguments = dict(call.arguments or {})
        address_keys = ("address", "delivery_address", "location")
        for constraint in self.decision_card.constraints:
            if (
                constraint.target != ConstraintTarget.ARGUMENT
                or constraint.operator != ConstraintOperator.RESOLVES_PROFILE
            ):
                continue
            expected = resolve_profile_address(
                self.user_profile, constraint.value
            )
            if not expected:
                continue
            key = next(
                (name for name in address_keys if name in arguments),
                constraint.argument_name or "address",
            )
            previous = arguments.get(key)
            if previous == expected:
                continue
            arguments[key] = expected
        return ToolCall(id=call.id, name=call.name, arguments=arguments)

    def _normalize_search_call(self, call: ToolCall) -> ToolCall:
        """Return a schema-shape-normalized copy without semantic rewriting."""
        arguments = dict(call.arguments or {})
        if not is_search_tool(call.name) or not isinstance(call.arguments, dict):
            return ToolCall(id=call.id, name=call.name, arguments=arguments)
        for key in ("keywords", "key_words"):
            value = arguments.get(key)
            if isinstance(value, str):
                value = [value]
            if isinstance(value, list):
                arguments[key] = list(
                    dict.fromkeys(
                        str(item).strip() for item in value if str(item).strip()
                    )
                )
        return ToolCall(id=call.id, name=call.name, arguments=arguments)

    def _framework_enrichment(self) -> AssistantMessage | None:
        """Expand parent candidates from tool dependencies, not category names."""
        if (
            self.runtime.phase != RuntimePhase.SELECT
            or self.runtime.execution_ready
            or not self.runtime.authorization.create_authorized
        ):
            return None
        decision = self._candidate_decision()
        calls: list[ToolCall] = []
        for request in decision.needs_enrichment:
            meta = self.tool_registry.meta.get(request.tool_name)
            arguments = request.as_arguments()
            if meta is not None:
                for argument, kind in meta.id_arguments.items():
                    if kind == "user" and argument in meta.required_arguments:
                        value = self.user_profile.get("user_id")
                        if value is not None:
                            arguments[argument] = value
            if self.tool_registry.validate_required(request.tool_name, arguments):
                continue
            calls.append(
                ToolCall(
                    id=f"adapt-enrich-{self.ledger._turn}-{len(calls)}",
                    name=request.tool_name,
                    arguments=arguments,
                )
            )
        return AssistantMessage(role="assistant", tool_calls=calls) if calls else None

    def _framework_parameter_probe(self) -> AssistantMessage | None:
        """Probe a bounded environment-compatible address representation.

        A probe is READ-only. It never changes a WRITE argument by itself;
        ``ToolErrorLedger.recover`` requires a later successful tool result
        before the representation can be used for CREATE.
        """
        probe = self.tool_errors.next_address_probe()
        if probe is None or probe.tool_name not in self.tool_registry.meta:
            return None
        call = ToolCall(
            id=f"adapt-parameter-probe-{len(self.runtime.events)}",
            name=probe.tool_name,
            arguments={probe.argument: probe.probe_value},
        )
        self.debug.emit(
            "tool_parameter_probe",
            tool=probe.tool_name,
            argument=probe.argument,
            failed_value=probe.failed_value,
            probe_value=probe.probe_value,
        )
        return AssistantMessage(role="assistant", tool_calls=[call])

    def _framework_recovered_write(self) -> AssistantMessage | None:
        """Retry a failed CREATE while freezing every unaffected argument."""
        if (
            self.runtime.phase
            not in {RuntimePhase.READY_TO_CREATE, RuntimePhase.WAIT_CREATE_RESULT}
            or self._operation_journal().successful(ToolRole.CREATE.value)
        ):
            return None
        recovered = self.tool_errors.recovered_create_attempt()
        if recovered is None or recovered.tool_name not in self.tool_registry.meta:
            return None
        if self.runtime.phase == RuntimePhase.WAIT_CREATE_RESULT:
            # A recovered proposal proves the preceding attempt failed. Bring
            # legacy/manual state in line with the typed outcome transition.
            self.runtime.phase = RuntimePhase.READY_TO_CREATE
        call = ToolCall(
            id=f"adapt-recovered-write-{len(self.runtime.events)}",
            name=recovered.tool_name,
            arguments=recovered.arguments,
        )
        assistant = AssistantMessage(role="assistant", tool_calls=[call])
        allowed_tools = self.tool_registry.allowed_tools(self.runtime, self.ledger)
        problems = self._preflight(assistant, allowed_tools)
        if problems:
            self.debug.emit(
                "recovered_write_rejected",
                tool=recovered.tool_name,
                problems=problems,
            )
            return None
        self.debug.emit(
            "recovered_write_replayed",
            tool=recovered.tool_name,
            argument=recovered.recovery.argument,
            failed_value=recovered.recovery.failed_value,
            recovered_value=recovered.recovery.recovered_value,
        )
        return assistant

    def _framework_recommendation(self) -> AssistantMessage | None:
        """Finish recommendation tasks directly from observed candidates.

        This prevents keyword-changing search loops and trailing confirmation
        questions, while ensuring every recommended name came from the current
        subtask's environment results.
        """
        if self.task_spec.completion.desired_outcome != DesiredOutcome.INFORM:
            return None
        if self.runtime.phase != RuntimePhase.SELECT:
            return None
        response_key = (
            self._operation_journal().epoch,
            self.ledger.candidate_version,
            "recommendation",
        )
        if self.responses.has(*response_key):
            self.runtime.phase = RuntimePhase.DONE
            return None
        ranked = [
            candidate
            for candidate in self._candidate_shortlist(limit=3)
            if candidate.name
        ]
        shortlist = ranked[:3]
        if not shortlist:
            return None
        lines = ["根据当前需求和你的偏好，我的推荐是："]
        for index, candidate in enumerate(shortlist, 1):
            details = []
            if candidate.price is not None:
                details.append(f"¥{candidate.price:g}")
            if candidate.inventory is not None:
                details.append(f"库存{candidate.inventory}")
            suffix = f"（{'，'.join(details)}）" if details else ""
            lines.append(f"{index}. {candidate.name}{suffix}")
        lines.append("首选为第 1 项，以上名称均来自当前实际候选结果。")
        return AssistantMessage(role="assistant", content="\n".join(lines))

    def _observe_assistant(self, assistant: AssistantMessage) -> None:
        active_context = getattr(self, "_active_context", None)
        if active_context is not None:
            active_context.has_activity = True
        if assistant.tool_calls:
            for call in assistant.tool_calls:
                role = self.tool_registry.role(call.name)
                if role == ToolRole.SEARCH:
                    self.ledger.register_search(call.name, call.arguments)
                elif role == ToolRole.ENRICH:
                    self.ledger.register_enrichment_read(call.name, call.arguments)
                self.runtime.mark_proposal(role.value)
                self.debug.emit(
                    "tool_proposal",
                    tool=call.name,
                    role=role.value,
                    phase=self.runtime.phase.value,
                )
                if role == ToolRole.CREATE:
                    meta = self.tool_registry.meta.get(call.name)
                    proposed_ids = []
                    if meta is not None:
                        proposed_ids = [
                            str(item)
                            for argument, kind in meta.id_arguments.items()
                            if kind != "user" and argument in call.arguments
                            for item in (
                                call.arguments[argument]
                                if isinstance(call.arguments[argument], list)
                                else [call.arguments[argument]]
                            )
                        ]
                    selected_id = self.runtime.selected_candidate_id
                    if selected_id:
                        self.debug.emit(
                            "create_consistency",
                            selection_source="runtime_selected",
                            selected_candidate_id=selected_id,
                            selected_candidate_ids=[selected_id],
                            proposed_candidate_ids=proposed_ids,
                            consistent=selected_id in proposed_ids,
                        )
                    else:
                        # An already-authorized task may deterministically
                        # select and CREATE without displaying a shortlist.
                        # Attribute that transition to the same candidate
                        # authority rather than silently dropping it from the
                        # CREATE-consistency denominator.
                        decision = self._candidate_decision()
                        decision_ids = (
                            list(decision.selected.leaf_ids)
                            if decision.selected is not None
                            else []
                        )
                        if decision_ids:
                            self.debug.emit(
                                "create_consistency",
                                selection_source="candidate_decision",
                                selected_candidate_id=decision_ids[0],
                                selected_candidate_ids=decision_ids,
                                proposed_candidate_ids=proposed_ids,
                                consistent=set(decision_ids).issubset(proposed_ids),
                            )
                self.tool_errors.register_proposal(
                    call.id,
                    call.name,
                    call.arguments,
                    role,
                )
                self._operation_journal().register(
                    call.id,
                    role.value,
                    call.name,
                    call.arguments,
                )
                meta = self.tool_registry.meta.get(call.name)
                input_ids = []
                if meta is not None:
                    input_ids = [
                        str(item)
                        for argument, kind in meta.id_arguments.items()
                        if kind != "user" and argument in call.arguments
                        for item in (
                            call.arguments[argument]
                            if isinstance(call.arguments[argument], list)
                            else [call.arguments[argument]]
                        )
                    ]
                active_context = getattr(self, "_active_context", None)
                self._lineage_ledger().register(
                    call_id=call.id or f"adapt-call-{len(self.runtime.events)}",
                    instruction_epoch=getattr(self, "_instruction_epoch", 0),
                    tool_epoch=(
                        active_context.tool_epoch
                        if active_context is not None
                        else getattr(self, "_tool_epoch", 0)
                    ),
                    operation_epoch=self._operation_journal().epoch,
                    tool_name=call.name,
                    tool_role=role.value,
                    input_ids=input_ids,
                )
                if role == ToolRole.CREATE and meta is not None:
                    coverage_gap = self.ledger.preference_coverage_gap(
                        call.arguments,
                        self.decision_card,
                        meta.id_arguments,
                    )
                    if coverage_gap:
                        self.debug.emit(
                            "preference_undercoverage_observed",
                            strict=False,
                        )
            return
        if self.runtime.phase == RuntimePhase.REPORT:
            self.runtime.phase = RuntimePhase.DONE
            self.runtime.record("report_completed")
            return
        if self.question_gate.is_question(assistant.content or ""):
            decision = self.question_gate.evaluate(
                assistant.content or "", self.runtime
            )
            self.question_gate.commit(decision, self.runtime)
            self.debug.emit(
                "question_committed",
                dimension=decision.dimension,
                phase=self.runtime.phase.value,
            )

    def _framework_question(self) -> str:
        gap = self._information_gap_contract().next_gap(
            resolved=self.runtime.resolved_slots,
            asked=self.runtime.asked_dimensions,
        )
        if gap is None:
            return ""
        return gap.question

    def _information_gap_contract(self) -> InformationGapContract:
        """Compile decision-critical questions from the typed task contract."""
        gaps: list[InformationGap] = [
            default_gap(dimension, "task_spec")
            for dimension in self.runtime.critical_gaps()
        ]
        if self.task_spec.action == "commit":
            registry = getattr(self, "tool_registry", None)
            if registry is not None:
                gaps.extend(
                    question.as_gap()
                    for question in SchemaQuestionPlanner.questions(
                        registry.contracts
                    )
                )
        unique: dict[str, InformationGap] = {}
        for gap in gaps:
            unique.setdefault(gap.dimension, gap)
        return InformationGapContract(tuple(unique.values()))

    def _resolve_user_selection(
        self, text: str, *, user_event: UserEvent | None = None
    ) -> None:
        snapshot = self.responses.latest_snapshot(
            self._operation_journal().epoch, "recommendation"
        )
        if snapshot is None:
            return
        index = user_event.selection_index if user_event is not None else selected_ordinal(text)
        if not index and accepts_visible_recommendation(text):
            # Framework recommendations explicitly identify the first rendered
            # candidate as the preferred option.  Bind the acceptance to that
            # immutable rendered order, never to a newly recomputed ranking.
            index = 1
        if not index:
            return
        _, candidate_ids = snapshot
        if not 1 <= index <= len(candidate_ids):
            return
        selected = self.ledger.candidates.get(candidate_ids[index - 1])
        if selected is None:
            return
        # The ordinal is resolved against the exact list actually rendered.
        self.runtime.authorization.create_authorized = True
        self.runtime.authorization.candidate_choice_authorized = True
        self.runtime.selected_candidate_id = selected.candidate_id
        self.runtime.selection_made = True
        decision = self._candidate_decision()
        self.runtime.select_candidate(selected.candidate_id, decision=decision)

    def _emit_preference_alignment(self) -> None:
        """Expose capability-level evidence without evaluator information."""
        candidates = self._candidate_shortlist(limit=8)
        if not candidates or not self.decision_card.alignment_preferences():
            return
        from agent.runtime.ranking import CandidateRanker

        alignment = CandidateRanker.preference_alignment(
            candidates, self.decision_card
        )
        coverages = [alignment.coverage(candidate) for candidate in candidates]
        scores = [alignment.score(candidate) for candidate in candidates]
        decisive_scores = [
            alignment.decisive_score(candidate) for candidate in candidates
        ]
        self.debug.emit(
            "preference_alignment",
            atom_count=len(alignment.atoms),
            dynamic_attribute_keys=sorted(
                {
                    atom.attribute_key
                    for atom in alignment.atoms
                    if atom.attribute_key != "legacy_text"
                }
            ),
            candidate_count=len(candidates),
            best_coverage=max(coverages, default=0),
            best_score=max(scores, default=0.0),
            best_decisive_score=max(decisive_scores, default=0.0),
            best_candidate_count=sum(
                score == max(scores, default=0.0)
                for score in scores
            ),
        )

    def _emit_candidate_attribution(self, decision) -> None:
        """Record all shadow policies without changing the production choice."""
        candidates = list(self.ledger.structural_leaf_candidates())
        if not candidates:
            return
        comparison = CandidateAttributionEngine.compare(
            candidates, self.decision_card, decision=decision
        )
        self._latest_candidate_attribution = comparison
        for policy, batch in comparison.items():
            self.debug.emit(
                "candidate_attribution",
                policy=policy,
                instruction_epoch=getattr(self, "_instruction_epoch", 0),
                candidate_version=self.ledger.candidate_version,
                summary=batch.summary.as_dict(),
                top3=[record.as_dict() for record in batch.records[:3]],
            )

    @staticmethod
    def _is_delegation(text: str) -> bool:
        return any(
            marker in (text or "") for marker in ("随便", "看着办", "不太清楚", "都行")
        )

    def _finalize_visible_trajectory(self) -> None:
        if self.runtime.has_unresolved_payment_failure(
            self.ledger.pending_payment_ids
        ):
            self._record_lesson(
                "unresolved_operation",
                "authorized operation ended with an unresolved workflow state",
                "After CREATE returns an unpaid order, explicitly obtain payment authorization and complete or decline payment.",
            )
        if self.ledger.candidates and any(
            count > 1 for count in self.ledger.search_counts.values()
        ):
            self._record_lesson(
                "repeat_search",
                "the same normalized search signature was emitted more than once",
                "Use observed candidates after a repeated search instead of issuing the same search again.",
            )

    def _record_lesson(self, failure_class: str, trigger: str, correction: str) -> None:
        # Raw-text callers may not manufacture correction evidence. User
        # correction lessons enter only through _record_user_correction_lesson.
        if failure_class == "user_correction":
            self.debug.emit(
                "lesson_suppressed",
                failure_class=failure_class,
                owner="harness",
                reason="user correction requires a typed CURRENT_CORRECTION event",
            )
            return
        self._commit_lesson(failure_class, trigger, correction)

    def _record_user_correction_lesson(self, event: UserEvent) -> None:
        if event.kind != UserEventKind.CURRENT_CORRECTION:
            self.debug.emit(
                "lesson_suppressed",
                failure_class="user_correction",
                owner="harness",
                reason=f"incompatible typed event: {event.kind.value}",
            )
            return
        self._commit_lesson(
            "user_correction",
            event.text,
            "Apply the user's latest correction before any further tool call.",
        )

    def _commit_lesson(self, failure_class: str, trigger: str, correction: str) -> None:
        if not self.enable_lessons:
            return
        self.lessons.add(
            self.task_spec.domain,
            self.task_spec.facet,
            failure_class,
            trigger,
            correction,
        )
        evidence_sources = {
            "tool_error": TrajectoryEvidenceSource.TOOL_ERROR,
            "user_correction": TrajectoryEvidenceSource.USER_CORRECTION,
            "repeat_search": TrajectoryEvidenceSource.EXPLICIT_STATE_FAILURE,
            "unresolved_operation": TrajectoryEvidenceSource.EXPLICIT_STATE_FAILURE,
        }
        evidence_source = evidence_sources.get(failure_class)
        rule = (
            self.runtime_policies.observe(
                self.task_spec.domain,
                self.task_spec.facet,
                failure_class,
                evidence_source=evidence_source,
                tool_family=self.ledger.policy_tool_family(),
                entity_signature=self.ledger.policy_entity_signature(),
                observed_event_epoch=self._operation_journal().epoch,
            )
            if evidence_source is not None
            else None
        )
        self.debug.emit(
            "lesson_recorded",
            failure_class=failure_class,
            evidence_source=(evidence_source.value if evidence_source else "soft_only"),
            correction=correction,
        )
        if rule is not None:
            self.debug.emit(
                "runtime_policy_learned",
                capability_target=rule.capability_target,
                domain=rule.domain,
                facet=rule.facet,
                failure_class=rule.failure_class,
                evidence_source=rule.evidence_source,
                failure_cluster=rule.failure_cluster,
                proposed_change=rule.proposed_change,
                evidence_count=rule.evidence_count,
                confidence=rule.confidence,
                active_from_subtask=rule.active_from_subtask,
                forbidden_specificity=list(rule.forbidden_specificity),
            )

    def _apply_structural_runtime_policy(self) -> None:
        """Apply later-subtask rules only to the same observable tool shape."""
        if not self.enable_lessons:
            return
        tool_family = self.ledger.policy_tool_family()
        entity_signature = self.ledger.policy_entity_signature()
        if not tool_family or not entity_signature:
            return
        policy = self.runtime_policies.policy(
            self.task_spec.domain,
            self.task_spec.facet,
            tool_family=tool_family,
            entity_signature=entity_signature,
        )
        RuntimePolicyAdapter.apply(
            policy, self.runtime, self.ledger, self.tool_errors
        )
        from agent.runtime.ranking import CandidateRanker

        self.debug.emit(
            "runtime_policy_reapplied",
            domain=self.task_spec.domain,
            facet=self.task_spec.facet,
            tool_family=tool_family,
            entity_signature=entity_signature,
            reliable_task_grounding=CandidateRanker.has_reliable_task_grounding(
                self._candidate_shortlist(limit=8),
                self.decision_card,
            ),
            capabilities=list(policy.active_capabilities),
        )

    def dump_debug_trace(self, path, *, append: bool = True) -> None:
        """Persist only agent-visible structured events to a JSONL sidecar."""
        from pathlib import Path

        self.debug.dump_jsonl(Path(path), append=append)
