"""Complete ADAPT agent assembled outside the read-only VitaBench package."""

from __future__ import annotations

from dataclasses import dataclass

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
from agent.intent import DesiredOutcome, selected_ordinal
from agent.lessons import ExecutionLessonStore
from agent.memory.adapt_memory import ADAPTMemory
from agent.runtime import (
    DebugEventStore,
    InformationGap,
    InformationGapContract,
    OperationJournal,
    QuestionGate,
    ResponseJournal,
    RuntimePhase,
    RuntimePolicyAdapter,
    RuntimePolicyStore,
    TaskRuntime,
    ToolErrorLedger,
    ToolRegistry,
    ToolRole,
    default_gap,
)

_ADAPT_POLICY = """

## ADAPT decision protocol
1. Treat the current user instruction and corrections as hard constraints. Memory preferences are soft unless marked AVOID.
2. Before a search, read MUST/AVOID/PREFER. After search, compare concrete candidate name, variant, date, location, inventory and IDs.
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

    def _operation_journal(self) -> OperationJournal:
        """Return the journal, including lightweight unit-test constructions."""
        journal = getattr(self, "operations", None)
        if journal is None:
            journal = OperationJournal()
            self.operations = journal
        return journal

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
        return candidates or active_registry.shortlist(
            self.ledger, self.decision_card, limit=limit
        )

    def _candidate_decision(self, registry: ToolRegistry | None = None):
        active_registry = registry or self.tool_registry
        return active_registry.candidate_decision(
            self.ledger,
            self.decision_card,
            runtime=self.runtime,
            instruction_epoch=getattr(self, "_instruction_epoch", 0),
        )

    def set_current_instruction(self, instruction: str):
        previous = self._current_instruction
        # The orchestrator invokes this hook exactly once per subtask. Text is
        # not a subtask identity: two consecutive tasks may legitimately have
        # identical instructions and must still receive fresh operation,
        # question, search and candidate state.
        if previous:
            active_registry = (
                self._active_context.tool_registry
                if self._active_context is not None
                else self.tool_registry
            )
            self._finalize_visible_trajectory(active_registry)
        if self._pending_tool_registry is not None:
            self.tool_registry = self._pending_tool_registry
            self._pending_tool_registry = None
        self.ledger.reset()
        self.tool_errors.reset()
        self.operations.reset()
        self.responses.reset()
        self.memory.begin_subtask(instruction)
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
            RuntimePolicyAdapter.apply(runtime_policy, self.runtime, self.ledger)
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
        base = super().system_prompt
        lessons = (
            self.lessons.render(self.task_spec.domain, self.task_spec.facet)
            if self.enable_lessons
            else ""
        )
        ledger = self.ledger.render(
            self.decision_card,
            entity_types=self.tool_registry.candidate_entity_types(self.ledger),
        )
        additions = [_ADAPT_POLICY]
        additions.append(
            "## Current instruction\n" + (self.task_spec.instruction or "none")
        )
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
            RuntimePolicyAdapter.apply(runtime_policy, self.runtime, self.ledger)
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
    ) -> tuple[AssistantMessage, LLMAgentState]:
        self._observe_input(message)
        if isinstance(message, MultiToolMessage):
            state.messages.extend(message.tool_messages)
        else:
            state.messages.append(message)

        if self.runtime.phase == RuntimePhase.DONE:
            completion = self._terminal_response()
            state.messages.append(completion)
            self.debug.emit("runtime_completed", facet=self.task_spec.facet)
            return completion, state

        payment_question = self._framework_payment_question()
        if payment_question:
            assistant = AssistantMessage(role="assistant", content=payment_question)
            state.messages.append(assistant)
            self.debug.emit("payment_question", facet=self.task_spec.facet)
            return assistant, state

        parameter_probe = self._framework_parameter_probe()
        if parameter_probe:
            state.messages.append(parameter_probe)
            self._observe_assistant(parameter_probe)
            return parameter_probe, state

        recovered_write = self._framework_recovered_write()
        if recovered_write:
            state.messages.append(recovered_write)
            self._observe_assistant(recovered_write)
            return recovered_write, state

        enrichment = self._framework_enrichment()
        if enrichment:
            state.messages.append(enrichment)
            self._observe_assistant(enrichment)
            self.debug.emit(
                "hierarchical_enrichment",
                count=len(enrichment.tool_calls or []),
                facet=self.task_spec.facet,
            )
            return enrichment, state

        recommendation = self._framework_recommendation()
        if recommendation:
            state.messages.append(recommendation)
            self.runtime.phase = RuntimePhase.DONE
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
            state.messages.append(completion)
            return completion, state

        question = self._framework_question()
        if question:
            assistant = AssistantMessage(role="assistant", content=question)
            state.messages.append(assistant)
            return assistant, state

        for attempt in range(self._replan_limit + 1):
            self._refresh_system_message(state)
            compact_messages(state.messages)
            allowed_tools = self.tool_registry.allowed_tools(self.runtime, self.ledger)
            generation_messages = self._generation_messages(
                state, allowed_tools, attempt
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

            problems = self._preflight(assistant, allowed_tools)
            if not problems:
                state.messages.append(assistant)
                self._observe_assistant(assistant)
                return assistant, state

            state.messages.append(assistant)
            correction = "ADAPT preflight rejected this action:\n- " + "\n- ".join(
                problems
            )
            logger.warning(correction)
            self.debug.emit("preflight_rejected", attempt=attempt, problems=problems)
            calls = assistant.tool_calls or []
            if calls:
                for call in calls:
                    state.messages.append(
                        ToolMessage(
                            id=call.id or f"adapt-preflight-{attempt}",
                            name=call.name,
                            role="tool",
                            content=correction,
                            requestor="assistant",
                            error=True,
                        )
                    )
            else:
                state.messages.append(UserMessage(role="user", content=correction))

        if self.runtime.phase == RuntimePhase.READY_TO_PAY:
            fallback_content = "订单已创建，目前尚未支付。"
        else:
            self.runtime.phase = RuntimePhase.UNSATISFIABLE
            fallback_content = "现有候选无法满足硬约束，我没有执行下单。"
        fallback = AssistantMessage(role="assistant", content=fallback_content)
        state.messages.append(fallback)
        return fallback, state

    def _generation_messages(self, state, allowed_tools, attempt: int):
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
        }:
            return state.system_messages + state.messages
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
        action = (
            "Call exactly one exposed CREATE tool now using the best compliant "
            f"Candidate Ledger IDs and all required arguments.{candidate_directive}"
            if self.runtime.phase == RuntimePhase.READY_TO_CREATE
            else "Call the exposed PAY tool now using the pending observed order ID."
        )
        directive = (
            "## Runtime controller\n"
            f"Focused irreversible phase, replan attempt {attempt + 1}. "
            f"Allowed tools: {allowed_names}. {action} "
            "Do not call any search/read tool, ask a question, or answer "
            "with text only. The Candidate Ledger in the system prompt is "
            "the complete observation snapshot."
        )
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

    def _framework_payment_question(self) -> str:
        if self.runtime.phase != RuntimePhase.READY_TO_PAY:
            return ""
        if (
            self.runtime.authorization.pay_authorized
            or self.runtime.authorization.pay_declined
            or self.runtime.payment_question_sent
        ):
            return ""
        self.runtime.payment_question_sent = True
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
                if not item.error and role in {
                    ToolRole.SEARCH, ToolRole.ENRICH, ToolRole.READ
                }:
                    self.ledger.observe(
                        item.name,
                        item.content,
                        self.tool_registry.result_schema(item.name),
                    )
                elif not item.error and role in {
                    ToolRole.CREATE,
                    ToolRole.PAY,
                    ToolRole.CANCEL,
                    ToolRole.MODIFY,
                    ToolRole.STATE_READ,
                }:
                    self.ledger.observe_state(item.name, item.content)
                attempt = self.tool_errors.observe_result(
                    item.id, item.name, item.content or "", item.error
                )
                self._operation_journal().observe_result(
                    item.id,
                    item.name,
                    self.tool_registry.role(item.name).value,
                    item.error,
                )
                self.runtime.observe_tool_result(
                    item.name, item.content or "", item.error, role.value
                )
                self.debug.emit(
                    "tool_result",
                    tool=item.name,
                    error=item.error,
                    phase=self.runtime.phase.value,
                    candidates=len(self.ledger.candidates),
                    tracked_attempt=attempt is not None,
                )
                if not item.error and role in {
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
                    self._emit_preference_alignment()
                if item.error:
                    self._record_lesson(
                        "tool_error",
                        item.content or item.name,
                        "Do not repeat the same invalid arguments; use exact IDs and constraints from the latest tool result.",
                    )
            elif isinstance(item, UserMessage):
                text = item.content or ""
                pending_dimension = self.runtime.pending_question_dimension
                was_ready_to_pay = self.runtime.phase == RuntimePhase.READY_TO_PAY
                was_done = self.runtime.phase == RuntimePhase.DONE
                self.runtime.observe_user(text)
                # Resolve presentation references while the recommendation's
                # operation epoch is still current. Selecting a displayed item
                # may open a new transaction epoch immediately afterwards.
                self._resolve_user_selection(text)
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
                if (was_ready_to_pay or was_done) and self.runtime.revision_requested:
                    revised_instruction = (
                        f"{self._current_instruction or ''}\n"
                        f"当前用户明确补充：{text}"
                    )
                    self.task_spec = TaskSpec.compile(revised_instruction)
                    self.runtime.spec = self.task_spec
                    self.runtime.resolved_slots.update(self.task_spec.resolved_slots)
                    self.decision_card = self.memory.compile_task(revised_instruction)
                self.debug.emit(
                    "user_observation",
                    phase=self.runtime.phase.value,
                    delegated=self.runtime.authorization.choice_delegated,
                    pending_answer_dimension=pending_dimension,
                )
                resolved_answer = self.runtime.resolved_slots.get(
                    pending_dimension, text
                )
                if (
                    self.memory.proactive.pending_question
                    and resolved_answer != "__delegated__"
                ):
                    self.memory.record_user_answer(
                        resolved_answer, dimension=pending_dimension
                    )
                    self.decision_card = self.memory.compile_task(
                        self._current_instruction or ""
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
                if any(
                    marker in text
                    for marker in ("不是", "不对", "错了", "我说的是", "不要", "不用")
                ):
                    self._record_lesson(
                        "user_correction",
                        text,
                        "Apply the user's latest correction as a hard constraint before any further tool call.",
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
        for call in calls:
            self._normalize_search_call(call)
            if call.name not in allowed_names:
                problems.append(
                    f"tool {call.name} is not allowed in phase {self.runtime.phase.value}"
                )
                continue
            role = self.tool_registry.role(call.name)
            if role in {ToolRole.CREATE, ToolRole.MODIFY}:
                self._normalize_profile_arguments(call)
            problems.extend(
                self.tool_registry.validate_required(call.name, call.arguments)
            )
            problems.extend(self._operation_journal().validate(role.value))
            recovery = self.tool_errors.recover(
                call.name, call.arguments, role
            )
            if recovery is not None:
                self.debug.emit(
                    "tool_parameter_recovered",
                    tool=call.name,
                    argument=recovery.argument,
                    failed_value=recovery.failed_value,
                    recovered_value=recovery.recovered_value,
                    evidence_tool=recovery.evidence_tool,
                )
            failure_reason = self.tool_errors.rejection_reason(
                call.name, call.arguments, role
            )
            if failure_reason:
                problems.append(f"tool failure guard: {failure_reason}")
                self.debug.emit(
                    "repeated_tool_call_blocked",
                    tool=call.name,
                    role=role.value,
                    reason=failure_reason,
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
                    coverage_gap = self.ledger.preference_coverage_gap(
                        call.arguments,
                        self.decision_card,
                        tool_meta.id_arguments if tool_meta else None,
                    )
                    if coverage_gap:
                        self.debug.emit(
                            "preference_undercoverage_observed",
                            strict=False,
                        )
            if (
                role == ToolRole.CREATE
                and not self.runtime.authorization.create_authorized
            ):
                problems.append("CREATE is not authorized by the user")
            if role == ToolRole.PAY and (
                not self.runtime.authorization.pay_authorized
                or self.runtime.authorization.pay_declined
            ):
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
        response = AssistantMessage(
            role="assistant",
            content=(
                "当前任务已结束，没有重复执行。"
                if already_emitted
                else (
                    "操作已成功完成。"
                    if self.runtime.write_succeeded
                    else "当前任务已结束，未执行新的操作。"
                )
            ),
        )
        self.responses.commit(epoch, self.ledger.candidate_version, "completion")
        return response

    def _normalize_profile_arguments(self, call: ToolCall) -> None:
        """Bind account aliases to exact WRITE arguments deterministically."""
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
                (name for name in address_keys if name in call.arguments),
                constraint.argument_name or "address",
            )
            previous = call.arguments.get(key)
            if previous == expected:
                continue
            call.arguments[key] = expected
            self.debug.emit(
                "profile_argument_bound",
                tool=call.name,
                argument=key,
                alias=constraint.value,
                replaced=bool(previous),
            )

    def _normalize_search_call(self, call: ToolCall) -> None:
        """Normalize only schema shape; never rewrite task semantics."""
        if not is_search_tool(call.name) or not isinstance(call.arguments, dict):
            return
        for key in ("keywords", "key_words"):
            value = call.arguments.get(key)
            if isinstance(value, str):
                value = [value]
            if isinstance(value, list):
                call.arguments[key] = list(
                    dict.fromkeys(
                        str(item).strip() for item in value if str(item).strip()
                    )
                )

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
        self.responses.commit(
            *response_key,
            candidate_ids=tuple(
                candidate.candidate_id for candidate in shortlist
            ),
        )
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
        if gap and self.runtime.phase != RuntimePhase.NEED_INFO:
            self.runtime.phase = RuntimePhase.NEED_INFO
        if self.runtime.phase != RuntimePhase.NEED_INFO:
            return ""
        if not gap:
            self.runtime.phase = RuntimePhase.SEARCH
            return ""
        question = gap.question
        self.runtime.commit_question(gap.dimension)
        self.memory.commit_question(question)
        self.debug.emit(
            "question_committed", dimension=gap.dimension, source=gap.source
        )
        return question

    def _information_gap_contract(self) -> InformationGapContract:
        """Compile decision-critical questions from the typed task contract."""
        gaps: list[InformationGap] = [
            default_gap(dimension, "task_spec")
            for dimension in self.runtime.critical_gaps()
        ]
        if self.task_spec.action == "commit":
            registry = getattr(self, "tool_registry", None)
            for meta in (registry.meta.values() if registry else ()):
                if meta.role != ToolRole.CREATE:
                    continue
                gaps.extend(
                    InformationGap(argument, question, "tool_schema")
                    for argument, question in meta.question_arguments.items()
                    if argument in meta.required_arguments
                )
        unique: dict[str, InformationGap] = {}
        for gap in gaps:
            unique.setdefault(gap.dimension, gap)
        return InformationGapContract(tuple(unique.values()))

    def _resolve_user_selection(self, text: str) -> None:
        index = selected_ordinal(text)
        if not index:
            return
        snapshot = self.responses.latest_snapshot(
            self._operation_journal().epoch, "recommendation"
        )
        if snapshot is None:
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
        self.runtime.select_candidate(
            selected.candidate_id,
            execution_ready=self.tool_registry.execution_ready(
                self.ledger, self.decision_card
            ),
        )

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

    @staticmethod
    def _is_delegation(text: str) -> bool:
        return any(
            marker in (text or "") for marker in ("随便", "看着办", "不太清楚", "都行")
        )

    def _finalize_visible_trajectory(
        self, tool_registry: ToolRegistry | None = None
    ) -> None:
        registry = tool_registry or self.tool_registry
        if self.runtime.has_unresolved_payment_failure(
            self.ledger.pending_payment_ids
        ):
            self._record_lesson(
                "unpaid_order",
                ",".join(sorted(self.ledger.pending_payment_ids)),
                "After CREATE returns an unpaid order, explicitly obtain payment authorization and complete or decline payment.",
            )
        has_executable_candidate = bool(
            self._candidate_shortlist(limit=1, registry=registry)
        ) and registry.execution_ready(self.ledger, self.decision_card)
        if (
            self.runtime.authorization.create_authorized
            and has_executable_candidate
            and self.runtime.phase not in {
            RuntimePhase.WAIT_CREATE_RESULT,
            RuntimePhase.READY_TO_PAY,
            RuntimePhase.WAIT_PAY_RESULT,
            RuntimePhase.DONE,
            }
        ):
            self._record_lesson(
                "missed_write",
                self.task_spec.instruction,
                "When CREATE is authorized and a compliant candidate exists, transition to CREATE instead of asking again.",
            )

    def _record_lesson(self, failure_class: str, trigger: str, correction: str) -> None:
        if not self.enable_lessons:
            return
        self.lessons.add(
            self.task_spec.domain,
            self.task_spec.facet,
            failure_class,
            trigger,
            correction,
        )
        rule = self.runtime_policies.observe(
            self.task_spec.domain,
            self.task_spec.facet,
            failure_class,
            tool_family=self.ledger.policy_tool_family(),
            entity_signature=self.ledger.policy_entity_signature(),
        )
        self.debug.emit(
            "lesson_recorded", failure_class=failure_class, correction=correction
        )
        if rule is not None:
            self.debug.emit(
                "runtime_policy_learned",
                capability_target=rule.capability_target,
                domain=rule.domain,
                facet=rule.facet,
                failure_class=rule.failure_class,
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
        RuntimePolicyAdapter.apply(policy, self.runtime, self.ledger)
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
