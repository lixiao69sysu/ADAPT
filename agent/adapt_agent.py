"""Complete ADAPT agent assembled outside the read-only VitaBench package."""

from __future__ import annotations

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
    DecisionCard,
    TaskSpec,
    is_search_tool,
    profile_address,
)
from agent.framework.context import compact_messages
from agent.lessons import ExecutionLessonStore
from agent.memory.adapt_memory import ADAPTMemory
from agent.runtime import (
    DebugEventStore,
    QuestionDecision,
    QuestionGate,
    RuntimePhase,
    RuntimePolicyAdapter,
    RuntimePolicyStore,
    TaskRuntime,
    ToolErrorLedger,
    ToolMeta,
    ToolRegistry,
    ToolRole,
    requires_product_entity,
)
from agent.runtime.location import (
    candidate_rating,
    home_tokens,
    location_rank,
)
from agent.runtime.schedule import parse_agent_time, resolve_relative_date
from agent.runtime.tool_errors import attempt_signature

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
10. When PHASE=select with CHOICE_SETTLED=False, the concrete item is not decided yet: ask one question that separates the observed candidates (which flavour, which one, where, when), or search for the missing evidence first. An order request authorizes the purchase, not a particular item.
"""

_TOPPING_TERMS = ("布蕾", "珍珠", "芋泥", "芋圆", "波霸", "椰果", "仙草", "布丁", "红豆", "奶冻")
_TOPPING_NEGATIONS = ("不加小料", "无小料", "不要小料", "不放小料")
_BEVERAGE_PRODUCT_TERMS = ("奶茶", "奶绿", "烤奶", "饮品", "果茶")

# Last-resort wording for a decision-critical slot that is still open. Used by
# the framework speech path and by the fallback after every model attempt was
# rejected: a blocked question must never end the subtask in a false refusal
# (E-050).
_GAP_QUESTIONS = {
    "size": "请告诉我需要的尺码，例如 42-43 码。",
    "quantity": "请告诉我需要几人或几张票。",
    "departure": "请告诉我出发地。",
    "destination": "请告诉我目的地。",
    "date": "请告诉我具体日期。",
    "room_type": "请告诉我需要大床房还是双床房。",
    "time": "请告诉我希望安排在上午、下午还是晚上。",
    "caffeine": "这杯咖啡是上午喝还是下午喝？我会据此选高或低咖啡因。",
    "taste": "你这次的锅底更想要麻辣、菌汤、番茄还是清汤？",
    "dessert": "套餐里的甜品有明确偏好吗？我会按候选中的精确配套筛选。",
}


class ADAPTAgent(PersonalizationAgent):
    """PersonalizationAgent with task compilation and guarded execution."""

    def __init__(
        self,
        *args,
        enable_candidate_validation: bool = True,
        enable_lessons: bool = True,
        enable_adapt_prompt: bool = True,
        gate_phases: bool = True,
        focus_write_phase: bool = True,
        framework_speech: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        if not isinstance(self.memory, ADAPTMemory):
            raise TypeError("ADAPTAgent requires ADAPTMemory")
        self.enable_candidate_validation = enable_candidate_validation
        self.enable_lessons = enable_lessons
        # Isolation-rig switches (E-046): with the prompt blocks off and phase
        # gating open, only the stock prompt and the write-time validators remain
        # between the model and the environment.
        self.enable_adapt_prompt = enable_adapt_prompt
        self.gate_phases = gate_phases
        # Isolation rig (E-047): default behaviour keeps the focused write-phase
        # context; disabling it keeps the full transcript like the stock agent.
        self.focus_write_phase = focus_write_phase
        # Framework-authored user-facing turns (canned dimension questions and
        # the recommendation finaliser) are off by default: the trace shows them
        # pre-empting the model's own clarifying question or ending a unit with no
        # order at all (E-048). The model speaks; the framework only verifies.
        self.framework_speech = framework_speech
        self.task_spec = TaskSpec.compile("")
        self.decision_card = DecisionCard()
        self.ledger = CandidateLedger()
        # Observable proximity context: the administrative units of the user's
        # own registered address, used only to break ranking ties.
        self.home_tokens = home_tokens(self.user_profile)
        self.ledger.home_tokens = list(self.home_tokens)
        self.runtime = TaskRuntime.begin(self.task_spec)
        self.tool_registry = ToolRegistry()
        self.tool_errors = ToolErrorLedger()
        self.question_gate = QuestionGate()
        self._pending_question_decision = QuestionDecision(
            True, counts_against_budget=False
        )
        self.lessons = ExecutionLessonStore(str(self.user_profile.get("user_id", "")))
        self.runtime_policies = RuntimePolicyStore(
            str(self.user_profile.get("user_id", ""))
        )
        self.debug = DebugEventStore(
            {"user_id": str(self.user_profile.get("user_id", ""))}
        )
        self._replan_limit = 2
        self._recommendation_delivered = False
        self._gap_search_tool = ""
        self._date_grounded = False
        self._succeeded_writes: set[str] = set()
        self._select_turns = 0

    def _resolve_instruction_date(self, instruction: str) -> None:
        """Publish one grounded absolute date for a relative instruction.

        The environment already gives the agent its clock; converting "明天" or
        "周末" is pure date arithmetic and must not depend on the policy model.
        """
        resolution = resolve_relative_date(
            instruction, parse_agent_time(getattr(self, "time", "") or "")
        )
        if resolution is None:
            return
        self.runtime.resolved_date = resolution.date
        self.runtime.date_evidence = resolution.evidence
        self.runtime.date_time_hint = resolution.time_hint
        self.task_spec.resolved_slots.setdefault("date", resolution.date)
        self.debug.emit(
            "instruction_date_resolved",
            date=resolution.date,
            evidence=resolution.evidence,
            is_weekend=resolution.is_weekend,
            time_hint=resolution.time_hint,
        )

    def _framework_date_grounding(self) -> AssistantMessage | None:
        """Confirm the resolved calendar day with the environment once.

        A time-relative request must not lead straight to a time-sensitive
        write: the framework asks the environment which day it is, and the
        answer stays in the transcript for the model to use.
        """
        if self._date_grounded or not self.runtime.resolved_date:
            return None
        if "get_date_holiday_info" not in self.tool_registry.meta:
            return None
        if self.runtime.phase not in {
            RuntimePhase.START,
            RuntimePhase.SEARCH,
            RuntimePhase.SELECT,
        }:
            return None
        arguments = {"date": self.runtime.resolved_date}
        if self.tool_registry.validate_required("get_date_holiday_info", arguments):
            return None
        self._date_grounded = True
        call = ToolCall(
            id=f"adapt-date-grounding-{self.ledger._turn}",
            name="get_date_holiday_info",
            arguments=arguments,
        )
        self.debug.emit(
            "date_grounding_call",
            date=self.runtime.resolved_date,
            evidence=self.runtime.date_evidence,
        )
        return AssistantMessage(role="assistant", tool_calls=[call])

    def set_current_instruction(self, instruction: str):
        previous = self._current_instruction
        if previous != instruction:
            if previous:
                self._finalize_visible_trajectory()
            self.ledger.reset()
            self.ledger.home_tokens = list(self.home_tokens)
            self.tool_errors.reset()
            self._recommendation_delivered = False
            self._gap_search_tool = ""
            self._date_grounded = False
            self._succeeded_writes = set()
            self._select_turns = 0
            self.memory.begin_subtask(instruction)
            self.task_spec = TaskSpec.compile(instruction)
            self.task_spec.resolved_slots.update(
                self.memory.resolve_task_slots(instruction)
            )
            self.decision_card = self.memory.compile_task(instruction)
            self.runtime = TaskRuntime.begin(self.task_spec)
            self._resolve_instruction_date(instruction)
            current_user_id = str(self.user_profile.get("user_id", ""))
            if current_user_id != self.lessons.user_id:
                self.lessons.reset(current_user_id)
            self.lessons.begin_subtask()
            self.runtime_policies.begin_subtask(current_user_id)
            runtime_policy = self.runtime_policies.policy(
                self.task_spec.domain, self.task_spec.facet
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
        super().set_current_instruction(instruction)

    @property
    def system_prompt(self) -> str:
        base = super().system_prompt
        if not getattr(self, "enable_adapt_prompt", True):
            # Isolation rig: only the stock prompt (base + profile + memory
            # read) reaches the model, so the prompt tax is measurable.
            return base
        lessons = (
            self.lessons.render(self.task_spec.domain, self.task_spec.facet)
            if self.enable_lessons
            else ""
        )
        ledger = self.ledger.render(self.decision_card)
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
        self.tool_registry.rebuild(tools)
        super().update_tools(tools)

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
        self.decision_card = self.memory.compile_task(self._current_instruction)
        resolver = getattr(self.memory, "resolve_task_slots", None)
        memory_slots = resolver(self._current_instruction) if resolver else {}
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
            completion = AssistantMessage(
                role="assistant",
                content=(
                    "操作已成功完成。"
                    if self.runtime.write_succeeded
                    else "当前需求已处理完毕，如需继续操作请告诉我。"
                ),
            )
            state.messages.append(completion)
            self.debug.emit("runtime_completed", facet=self.task_spec.facet)
            return completion, state

        date_grounding = self._framework_date_grounding()
        if date_grounding:
            state.messages.append(date_grounding)
            self._observe_assistant(date_grounding)
            return date_grounding, state

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

        gap_search = self._framework_entity_gap_search()
        if gap_search:
            state.messages.append(gap_search)
            self._observe_assistant(gap_search)
            return gap_search, state

        if getattr(self, "framework_speech", False):
            # Legacy governor path: the framework asks the clarifying question
            # and finalises the recommendation itself. Off by default (E-048).
            recommendation = self._framework_recommendation()
            if recommendation:
                state.messages.append(recommendation)
                self.runtime.phase = RuntimePhase.DONE
                self.debug.emit(
                    "recommendation_finalized",
                    candidates=len(self.ledger.shortlist(self.decision_card, limit=3)),
                    facet=self.task_spec.facet,
                )
                return recommendation, state

            question = self._framework_question()
            if question:
                assistant = AssistantMessage(role="assistant", content=question)
                state.messages.append(assistant)
                return assistant, state
        elif self.runtime.phase == RuntimePhase.SELECT:
            # Without the framework recommendation, a recommendation task still
            # has to end somewhere: the model decides, and this counter only
            # feeds the debug stream. It also arms the bounded escape that keeps
            # an unsettled choice from dead-ending the subtask (E-049).
            self._select_turns += 1
            self.runtime.note_select_turn()

        for attempt in range(self._replan_limit + 1):
            self._refresh_system_message(state)
            compact_messages(state.messages)
            allowed_tools = self.tool_registry.allowed_tools(
                self.runtime,
                self.ledger,
                gate_phases=getattr(self, "gate_phases", True),
            )
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
            if (
                problems
                and all(problem.startswith("question gate:") for problem in problems)
                and self.runtime.phase
                in {RuntimePhase.SELECT, RuntimePhase.SEARCH, RuntimePhase.NEED_INFO}
                and self.runtime.candidates_seen
                and self.runtime.authorization.create_authorized
                and self.runtime.choice_settled()[0]
            ):
                # The model tried to ask about a choice the gate may no longer
                # allow, and SELECT exposes no write either, so spending the
                # replan budget here would end the subtask in the refusal
                # fallback (E-049). Settle the phase and let it act instead.
                self.runtime.promote_if_settled()
                self.debug.emit(
                    "question_blocked_promoted",
                    phase=self.runtime.phase.value,
                    reason=self.runtime.choice_source,
                )
                continue
            correction = "ADAPT preflight rejected this action:\n- " + "\n- ".join(
                problems
            )
            logger.warning(correction)
            self.debug.emit("preflight_rejected", attempt=attempt, problems=problems)
            self._record_lesson(
                "candidate_validation",
                correction,
                "Before committing, select only an observed candidate satisfying every MUST and AVOID constraint.",
            )
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

        fallback_content = self._fallback_message()
        fallback = AssistantMessage(role="assistant", content=fallback_content)
        state.messages.append(fallback)
        return fallback, state

    def _fallback_message(self) -> str:
        """The text sent when every model attempt in one turn was rejected.

        It must never repeat a false refusal: a subtask whose only blockers were
        derived constraints, or whose decision-critical slot is still open, has
        an honest answer that is not "no compliant candidate exists" (E-050,
        E-052).
        """
        if self.runtime.phase == RuntimePhase.READY_TO_PAY:
            return "订单已创建，目前尚未支付。"
        if self.runtime.phase == RuntimePhase.NEED_INFO:
            dimension = (
                self.runtime.pending_question_dimension
                or self.runtime.next_question_dimension()
            )
            if dimension:
                self.runtime.commit_question(dimension)
                self.debug.emit(
                    "question_committed", dimension=dimension, source="fallback"
                )
                return _GAP_QUESTIONS.get(dimension, f"请补充{dimension}。")
            self.runtime.phase = RuntimePhase.SEARCH
            return "我需要更多信息才能继续，请补充你的需求。"
        if self.runtime.constraint_veto_counts:
            # Only derived constraints blocked the write and they are now
            # capped; the choice may well be settled and executable.
            self.runtime.phase = RuntimePhase.SELECT
            return "我再核对一下候选，马上给你确认。"
        self.runtime.phase = RuntimePhase.UNSATISFIABLE
        return "现有候选无法满足硬约束，我没有执行下单。"
        fallback = AssistantMessage(role="assistant", content=fallback_content)
        state.messages.append(fallback)
        return fallback, state

    def _generation_messages(self, state, allowed_tools, attempt: int):
        """Use a phase-focused context once observation is complete.

        Keeping the full search transcript in READY_TO_CREATE anchors smaller
        policy models to the earlier search call even though that tool is no
        longer exposed. The system prompt already contains the compact
        Candidate Ledger, so by default only the latest user turn and an explicit
        controller directive are retained during irreversible phases.

        ``focus_write_phase=False`` is the isolation rig (E-047): it keeps the
        whole transcript, as the stock agent does, and appends the same
        directive instead of replacing the history.
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
        directive = self._write_phase_directive(allowed_tools, attempt)
        if not getattr(self, "focus_write_phase", True):
            return [*state.system_messages, *state.messages, directive]
        # Some OpenAI-compatible Qwen servers accept only one system message,
        # even when multiple system messages are consecutive at the beginning.
        system_content = "\n\n".join(
            message.content or "" for message in state.system_messages
        )
        focused = [
            SystemMessage(role="system", content=f"{system_content}\n\n{directive.content}")
        ]
        if latest_user is not None:
            focused.append(latest_user)
        return focused

    def _write_phase_directive(self, allowed_tools, attempt: int = 0) -> SystemMessage:
        """The controller directive for an irreversible phase."""
        allowed_names = ", ".join(tool.name for tool in allowed_tools) or "none"
        chosen_candidate = None
        selection_source = ""
        if (
            self.runtime.phase == RuntimePhase.READY_TO_CREATE
            and hasattr(self, "ledger")
            and hasattr(self, "decision_card")
        ):
            if self.runtime.selected_candidate_id:
                chosen_candidate = self.ledger.candidates.get(
                    self.runtime.selected_candidate_id
                )
                selection_source = "explicit user selection"
            else:
                chosen_candidate = self.ledger.unique_evidence_leader(
                    self.decision_card
                )
                selection_source = "unique preference-evidence leader"
                if chosen_candidate is None and self.ledger.require_max_preference_coverage:
                    # Learned preference-grounding policy (E-049): instead of
                    # rejecting a lower-coverage write, the framework names the
                    # best-evidence candidate and lets the model keep the
                    # decision to write.
                    leaders = self.ledger.evidence_leaders(self.decision_card)
                    if leaders:
                        chosen_candidate = leaders[0]
                        selection_source = "learned preference-coverage policy"
        candidate_directive = ""
        if chosen_candidate is not None:
            parent_ids = ", ".join(chosen_candidate.parent_ids) or "none"
            candidate_directive = (
                " Framework-selected candidate ("
                f"{selection_source}): ID={chosen_candidate.candidate_id}; "
                f"exact name={chosen_candidate.name}; parent IDs={parent_ids}. "
                "Use this exact candidate and its observed parent IDs."
            )
        action = (
            "Call exactly one exposed CREATE tool now using the best compliant "
            "Candidate Ledger IDs and all required arguments. In the same message, "
            "state in one short sentence which candidate you chose and which "
            "observed preference of the user it satisfies - the user must be able "
            "to see the choice before the order exists."
            f"{candidate_directive}"
            if self.runtime.phase == RuntimePhase.READY_TO_CREATE
            else "Call the exposed PAY tool now using the pending observed order ID."
        )
        directive = (
            "## Runtime controller\n"
            f"Irreversible phase, replan attempt {attempt + 1}. "
            f"Allowed tools: {allowed_names}. {action} "
            "Do not call any search/read tool, ask a question, or answer "
            "with text only. The Candidate Ledger in the system prompt is "
            "the complete observation snapshot."
        )
        return SystemMessage(role="system", content=directive)

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

    def _write_signature(self, tool_name: str, arguments) -> str:
        """Signature of a write proposal, matching the tool-error ledger."""
        return attempt_signature(tool_name, arguments or {})

    def _normalize_address_call(self, call: ToolCall) -> None:
        """Fill a write's address argument from the task's own address contract.

        The validator used to *reject* a write whose address did not equal the
        registered address, which ended the subtask with nothing (the trace
        showed nine such rejections for one user, each followed by the terminal
        "no compliant candidate" refusal). An address the task already pins -
        the user's home/company alias or a literal address in the instruction -
        is known observable data, so the framework repairs the argument instead
        of blocking the action (E-042).
        """
        if self.tool_registry.role(call.name) != ToolRole.CREATE:
            return
        constraints = [
            constraint
            for constraint in getattr(self.decision_card, "constraints", [])
            if getattr(constraint, "kind", "") == "address"
        ]
        if not constraints:
            return
        for constraint in constraints:
            operator = getattr(getattr(constraint, "operator", None), "value", "")
            raw_value = str(constraint.value or "")
            # An alias-shaped contract value ("单位", "公司", "家") resolves to
            # the registered street address regardless of which operator the
            # card used: the environment can only geocode the registered one
            # (E-051).
            resolved = profile_address(
                getattr(self, "user_profile", None) or {}, raw_value
            )
            if operator == "resolves_profile":
                expected = resolved
            else:
                expected = resolved or raw_value
            if not expected:
                continue
            declared = set()
            meta = getattr(self, "tool_registry", None)
            if meta is not None:
                declared = meta.meta.get(call.name, ToolMeta(call.name, ToolRole.READ)).argument_names
            for key in ("address", "location", "destination", "delivery_address"):
                if key not in call.arguments:
                    # A delivery write that omits the address it must deliver to
                    # is repaired the same way as a wrong one, but only when the
                    # tool actually declares that argument (E-051).
                    if key not in declared:
                        continue
                    call.arguments[key] = expected
                    self.debug.emit(
                        "address_argument_repaired",
                        tool=call.name,
                        argument=key,
                        replaced="",
                    )
                    continue
                current = str(call.arguments.get(key) or "")
                if expected in current or current in expected:
                    continue
                call.arguments[key] = expected
                self.debug.emit(
                    "address_argument_repaired",
                    tool=call.name,
                    argument=key,
                    replaced=current[:60],
                )
                self._record_lesson(
                    "address_argument_repaired",
                    f"{key}: {current[:40]} -> {expected[:40]}",
                    "Use the address the task pinned (registered home or the address named in the instruction) verbatim.",
                )
            return

    def _record_succeeded_write(self, attempt, item: ToolMessage) -> None:
        """Remember the exact write that already succeeded in this subtask.

        The conversation looped a completion-style task into creating the same
        order 14 times; the phase gate alone cannot see that the *arguments*
        are identical, so the guard needs the signature of what already
        happened (E-042).
        """
        if item.error or attempt is None:
            return
        if getattr(attempt, "role", None) != ToolRole.CREATE:
            return
        self._succeeded_writes.add(attempt.signature)

    def _refresh_system_message(self, state: LLMAgentState) -> None:
        content = self.system_prompt
        if state.system_messages:
            state.system_messages[0].content = content
        else:
            state.system_messages.append(SystemMessage(role="system", content=content))

    def _note_choice_evidence(self) -> None:
        """Push the ledger-derived choice facts the runtime cannot compute (E-049).

        The runtime decides whether a write may be exposed; only the agent holds
        the candidate ledger and the Decision Card, so the settlement evidence
        (discriminating preference leader, number of compliant options) is
        supplied here from observable tool results alone.
        """
        leader = self.ledger.unique_evidence_leader(self.decision_card)
        compliant = self.ledger.shortlist(self.decision_card, limit=2)
        self.runtime.observe_choice_evidence(
            leader_id=leader.candidate_id if leader is not None else "",
            executable_count=len(compliant),
        )

    def _emit_choice_state(self, trigger: str) -> None:
        """Observable record of why the write phase is or is not open (E-049)."""
        settled, source = self.runtime.choice_settled()
        self.debug.emit(
            "choice_state",
            trigger=trigger,
            phase=self.runtime.phase.value,
            settled=settled,
            source=source,
            leader=self.runtime.evidence_leader_id,
            compliant_candidates=self.runtime.executable_candidate_count,
            candidates_seen=self.runtime.candidates_seen,
            select_turns=self.runtime.select_turns,
            asked_dimensions=sorted(self.runtime.asked_dimensions),
        )

    def _observe_input(self, message: ValidAgentInputMessage) -> None:
        messages = (
            message.tool_messages
            if isinstance(message, MultiToolMessage)
            else [message]
        )
        for item in messages:
            if isinstance(item, ToolMessage):
                self.ledger.observe(item.name, item.content)
                attempt = self.tool_errors.observe_result(
                    item.id, item.name, item.content or "", item.error
                )
                self.runtime.observe_tool_result(
                    item.name, item.content or "", item.error
                )
                self._record_succeeded_write(attempt, item)
                self.debug.emit(
                    "tool_result",
                    tool=item.name,
                    error=item.error,
                    phase=self.runtime.phase.value,
                    candidates=len(self.ledger.candidates),
                    tracked_attempt=attempt is not None,
                )
                if self.tool_registry.role(item.name) in {ToolRole.SEARCH, ToolRole.READ}:
                    grounding_stats = self.memory.apply_candidate_grounding(
                        self.decision_card,
                        [
                            candidate
                            for candidate in self.ledger.candidates.values()
                            if candidate.entity_type not in {"order", "unknown"}
                        ],
                    )
                    self.debug.emit("candidate_memory_retrieval", **grounding_stats)
                    self._note_choice_evidence()
                    self.runtime.observe_candidates(
                        len(self.ledger.candidates),
                        execution_ready=self.tool_registry.execution_ready(
                            self.ledger, self.decision_card
                        ),
                    )
                    self._emit_choice_state("observation")
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
                if (was_ready_to_pay or was_done) and self.runtime.revision_requested:
                    revised_instruction = (
                        f"{self._current_instruction or ''}\n"
                        f"当前用户明确补充：{text}"
                    )
                    self.task_spec = TaskSpec.compile(revised_instruction)
                    self.runtime.spec = self.task_spec
                    self.runtime.resolved_slots.update(self.task_spec.resolved_slots)
                    self.decision_card = self.memory.compile_task(revised_instruction)
                self._resolve_user_selection(text)
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
                        self._note_choice_evidence()
                        self.runtime.observe_candidates(
                            len(self.ledger.candidates),
                            execution_ready=self.tool_registry.execution_ready(
                                self.ledger, self.decision_card
                            ),
                        )
                        self._emit_choice_state("user_answer")
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
        self._pending_question_decision = self.question_gate.evaluate(
            assistant.content or "", self.runtime
        )
        has_question = self.question_gate.is_question(assistant.content or "")
        if has_question and calls:
            problems.append(
                "questions must be sent as a standalone assistant message, never attached to tool calls"
            )
            self._record_lesson(
                "mixed_question_tool",
                "question and tool proposal in one assistant message",
                "Emit exactly one question or one tool action in a turn.",
            )
        elif has_question and not self._pending_question_decision.allowed:
            problems.append(f"question gate: {self._pending_question_decision.reason}")
            settled, _source = self.runtime.choice_settled()
            if (
                self.runtime.authorization.candidate_choice_authorized
                and settled
            ):
                # Only a question asked *after* the choice was settled is a
                # re-ask worth learning from (E-049).
                self._record_lesson(
                    "candidate_choice_reask",
                    self._pending_question_decision.reason,
                    "After an authorized task has an executable candidate, transition to CREATE without candidate reconfirmation.",
                )
        allowed_names = {tool.name for tool in (allowed_tools or [])}
        for call in calls:
            self._normalize_search_call(call)
            self._normalize_address_call(call)
            if call.name not in allowed_names:
                problems.append(
                    f"tool {call.name} is not allowed in phase {self.runtime.phase.value}"
                )
                continue
            if self.tool_registry.role(call.name) == ToolRole.CREATE:
                signature = self._write_signature(call.name, call.arguments)
                if signature in getattr(self, "_succeeded_writes", ()):
                    problems.append(
                        "this exact order was already created in this subtask; "
                        "do not recreate it - report the existing order or ask "
                        "the user what to change"
                    )
                    self.debug.emit(
                        "duplicate_write_blocked", tool=call.name, signature=signature
                    )
                    self._record_lesson(
                        "duplicate_write",
                        f"{call.name} {call.arguments}",
                        "A write that already succeeded must not be repeated; continue with payment or ask what to change.",
                    )
                    continue
            problems.extend(
                self.tool_registry.validate_required(call.name, call.arguments)
            )
            role = self.tool_registry.role(call.name)
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
            if (
                role == ToolRole.READ
                and call.name.startswith("get_ota_")
                and any(key.endswith("_id") for key in call.arguments)
            ):
                count = self.ledger.register_enrichment_read(
                    call.name, call.arguments
                )
                if count > 1:
                    problems.append(
                        "this parent detail ID was already read; choose an unexpanded parent ID from the Candidate Ledger"
                    )
            if is_search_tool(call.name):
                count = self.ledger.register_search(call.name, call.arguments)
                if count > self.ledger.max_searches_per_family:
                    problems.append(
                        f"normalized search signature budget exhausted after {count - 1} attempts"
                    )
                    self._record_lesson(
                        "repeat_search",
                        f"{call.name} {call.arguments}",
                        "After two identical searches, decide from the Candidate Ledger or ask one focused question.",
                    )
                budget_reason = self.ledger.search_budget_rejection(
                    call.name, execution_ready=self.runtime.execution_ready
                )
                if budget_reason:
                    problems.append(budget_reason)
                    self.debug.emit(
                        "search_budget_blocked",
                        tool=call.name,
                        reason=budget_reason,
                        execution_ready=self.runtime.execution_ready,
                    )
                    self._record_lesson(
                        "search_after_sufficient"
                        if self.runtime.execution_ready
                        else "search_family_budget",
                        f"{call.name} {call.arguments}",
                        "When the ledger already holds a compliant candidate, select and act instead of searching again.",
                    )
            if self.enable_candidate_validation:
                problems.extend(
                    self.ledger.validate_write(
                        call.name,
                        call.arguments,
                        self.decision_card,
                        self.user_profile,
                    )
                )
                if role == ToolRole.CREATE:
                    # The evidence leader is advisory, not a gate. Trace
                    # comparison showed the blocking form costing replans and,
                    # in some units, a terminal refusal, because it forced a
                    # candidate the model had deliberately not chosen on noisy
                    # atom counts (E-045). Hard constraints and an explicit
                    # user selection are still enforced (E-049).
                    leader = self.ledger.unique_evidence_leader(self.decision_card)
                    chosen = self.ledger.constraint_candidates(call.arguments)
                    if (
                        leader is not None
                        and chosen
                        and chosen[0].candidate_id != leader.candidate_id
                    ):
                        self.debug.emit(
                            "preference_leader_diverged",
                            chosen=chosen[0].candidate_id,
                            leader=leader.candidate_id,
                        )
                    if chosen:
                        position = self.ledger.shortlist_position(
                            chosen[0].candidate_id, self.decision_card
                        )
                        if position == 0:
                            # Observed and compliant, but not in the rendered
                            # crop: recorded, never vetoed (E-049).
                            self.debug.emit(
                                "shortlist_position_diverged",
                                chosen=chosen[0].candidate_id,
                            )
                    problems.extend(
                        self.ledger.validate_ranked_choice(
                            call.arguments,
                            self.decision_card,
                            self.runtime.selected_candidate_id,
                        )
                    )
                    coverage_gap = self.ledger.preference_coverage_gap(
                        call.arguments, self.decision_card
                    )
                    if coverage_gap:
                        self._record_lesson(
                            "preference_undercoverage",
                            coverage_gap,
                            "Select from candidates with maximum observable preference-atom coverage; never learn a user or product-specific rule.",
                        )
                        self.debug.emit(
                            "preference_undercoverage_observed",
                            strict=self.ledger.require_max_preference_coverage,
                            enforced=False,
                        )
                        # E-049: the learned policy now changes *which candidate
                        # the framework names* in the write directive instead of
                        # vetoing the write. Six of the twenty-one preflight
                        # rejections in one two-user trace came from this veto,
                        # and each rejection costs a replan and can end the
                        # subtask in a refusal.
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

        if not problems:
            for call in calls:
                self.runtime.mark_proposal(self.tool_registry.role(call.name).value)
                self.debug.emit(
                    "tool_proposal",
                    tool=call.name,
                    role=self.tool_registry.role(call.name).value,
                    phase=self.runtime.phase.value,
                )
        if not calls and self.task_spec.action == "commit":
            content = assistant.content or ""
            asks = "?" in content or "？" in content
            if not asks and any(
                word in content for word in ("已下单", "已预订", "马上为您", "帮您下单")
            ):
                problems.append(
                    "text claims or promises execution but contains no WRITE tool call"
                )
        return list(dict.fromkeys(self._cap_repeated_vetoes(problems)))

    # Constraints the framework *derived* from text. They may be wrong, so they
    # must not veto forever: a single mis-parsed value produced 103 identical
    # rejections in one subtask until the step budget was gone (E-052).
    _CAPPED_VETO_PREFIXES = (
        "selected candidate does not satisfy required",
        "selected candidate does not show required value",
        "selected candidate has observable preference score",
    )
    _VETO_CAP = 3

    def _cap_repeated_vetoes(self, problems: list[str]) -> list[str]:
        """Stop repeating a derived constraint once it has rejected a few times.

        Invariants that are facts about the environment or the user - ID
        provenance, AVOID violations, authorization, duplicate writes, the
        tool-failure guard - are never capped.
        """
        kept: list[str] = []
        for problem in problems:
            if not problem.startswith(self._CAPPED_VETO_PREFIXES):
                kept.append(problem)
                continue
            allowed, capped = self._count_capped_veto(problem)
            if allowed:
                kept.append(problem)
            elif capped and capped == self._VETO_CAP + 1:
                self.debug.emit(
                    "constraint_veto_capped", constraint=problem[:120]
                )
                self._record_lesson(
                    "constraint_veto_capped",
                    problem,
                    "A derived constraint that no observed candidate satisfies is a representation problem: state it, do not veto forever.",
                )
        return kept

    def _count_capped_veto(self, problem: str) -> tuple[bool, int]:
        counts = self.runtime.constraint_veto_counts
        counts[problem] = counts.get(problem, 0) + 1
        return counts[problem] <= self._VETO_CAP, counts[problem]

    def _normalize_search_call(self, call: ToolCall) -> None:
        """Make retrieval honor a dominant exclusion before result truncation.

        VitaBench product searches can return more candidates than fit in the
        observable tool message. If an old product preference contradicts a
        newer exclusion, leaving it in the query can push every compliant item
        beyond that boundary. This rewrite uses only the visible Decision Card.
        """
        if not (
            is_search_tool(call.name)
            and "product" in call.name.lower()
            and isinstance(call.arguments, dict)
            and "小料" in self.decision_card.avoid
        ):
            return
        has_current_exception = any(
            any(
                topping in preference and preference.strip() != topping
                for topping in _TOPPING_TERMS
            )
            and any(term in preference for term in _BEVERAGE_PRODUCT_TERMS)
            and not any(negation in preference for negation in _TOPPING_NEGATIONS)
            for preference in self.decision_card.prefer
        )
        if has_current_exception:
            return
        category = next(
            (
                constraint.value
                for constraint in self.task_spec.must
                if constraint.kind == "category"
            ),
            "",
        )
        if not category:
            preference_text = " ".join(self.decision_card.prefer)
            category = next(
                (
                    candidate
                    for candidate in ("奶茶", "咖啡", "饮品")
                    if candidate in preference_text
                ),
                "饮品",
            )
        call.arguments["keywords"] = [category, "无小料", "原味"]

    def _framework_enrichment(self) -> AssistantMessage | None:
        """Deterministically expand bounded OTA parent candidates.

        Parent-detail discovery is bookkeeping, not a preference decision.
        Batching it here prevents the policy model from repeatedly reading the
        first parent or spending replans on confirmation questions.
        """
        if (
            self.runtime.phase != RuntimePhase.SELECT
            or self.runtime.execution_ready
            or not self.runtime.authorization.create_authorized
        ):
            return None
        mapping = {
            "hotel": ("hotel", "get_ota_hotel_info", "hotel_id", 6),
            "attraction": (
                "attraction",
                "get_ota_attraction_info",
                "attraction_id",
                3,
            ),
            "flight": ("flight", "get_ota_flight_info", "flight_id", 3),
            "train": ("train", "get_ota_train_info", "train_id", 3),
        }
        config = mapping.get(self.task_spec.facet)
        if not config:
            return None
        entity_type, tool_name, argument_name, coverage = config
        if tool_name not in self.tool_registry.meta:
            return None
        parents = [
            candidate
            for candidate in self.ledger.candidates.values()
            if candidate.entity_type == entity_type
        ]
        expanded = {
            parent_id
            for candidate in self.ledger.candidates.values()
            if candidate.entity_type == "product"
            for parent_id in candidate.parent_ids
        }
        required = min(coverage, len(parents))
        remaining = max(0, required - len(expanded.intersection(
            candidate.candidate_id for candidate in parents
        )))
        if not remaining:
            return None
        from agent.runtime.ranking import CandidateRanker

        unexpanded = [
            candidate
            for candidate in CandidateRanker().rank(
                parents, self.decision_card, max(coverage * 2, 8)
            )
            if candidate.candidate_id not in expanded
        ][:remaining]
        if not unexpanded:
            return None
        calls = []
        for index, candidate in enumerate(unexpanded):
            arguments = {argument_name: candidate.candidate_id}
            self.ledger.register_enrichment_read(tool_name, arguments)
            calls.append(
                ToolCall(
                    id=f"adapt-enrich-{self.ledger._turn}-{index}",
                    name=tool_name,
                    arguments=arguments,
                )
            )
        return AssistantMessage(role="assistant", tool_calls=calls)

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
        recovered = self.tool_errors.recovered_create_attempt()
        if recovered is None or recovered.tool_name not in self.tool_registry.meta:
            return None
        call = ToolCall(
            id=f"adapt-recovered-write-{len(self.runtime.events)}",
            name=recovered.tool_name,
            arguments=recovered.arguments,
        )
        assistant = AssistantMessage(role="assistant", tool_calls=[call])
        allowed_tools = self.tool_registry.allowed_tools(
            self.runtime,
            self.ledger,
            gate_phases=getattr(self, "gate_phases", True),
        )
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

    def _framework_entity_gap_search(self) -> AssistantMessage | None:
        """Search for the last entity kind a nearly-ready CREATE still misses.

        A shop-level search can leave ``product_id`` unobserved while the
        policy model invents a name from memory; the deterministic validator
        then rejects every attempt and the subtask ends with no order (E-035).

        The hook is deliberately narrow: it only fires for a create tool whose
        *other* required entities are already observed, so it never replaces
        the normal venue-level search and cannot cascade across every create
        tool in a domain. Keywords come from observable terms only: the
        instruction's task atoms and the agent's own previous search query.
        """
        if not self.runtime.authorization.create_authorized:
            return None
        if self.runtime.phase not in {
            RuntimePhase.SEARCH,
            RuntimePhase.SELECT,
            RuntimePhase.READY_TO_CREATE,
        }:
            return None
        observed = {
            candidate.entity_type for candidate in self.ledger.candidates.values()
        }
        if not observed:
            return None
        product_needed = requires_product_entity(
            self.task_spec.instruction, self.decision_card.must
        )
        for tool_name, kinds in sorted(
            self.tool_registry.create_gaps(self.ledger).items()
        ):
            missing = set(kinds)
            if len(missing) != 1:
                continue
            witnessed = self.tool_registry.required_entity_types(tool_name) & observed
            if not witnessed:
                continue
            kind = next(iter(missing))
            if kind == "product" and not product_needed:
                continue
            search_tool = self.tool_registry.search_tool_for(kind)
            if not search_tool or search_tool == self._gap_search_tool:
                continue
            if not self.ledger.search_allowed(search_tool):
                continue
            arguments = {"keywords": self._entity_gap_keywords()}
            if not arguments["keywords"]:
                continue
            if self.tool_registry.validate_required(search_tool, arguments):
                continue
            count = self.ledger.register_search(search_tool, arguments)
            if count > self.ledger.max_searches_per_family:
                continue
            self._gap_search_tool = search_tool
            call = ToolCall(
                id=f"adapt-gap-search-{self.ledger._turn}",
                name=search_tool,
                arguments=arguments,
            )
            self.debug.emit(
                "entity_gap_search",
                tool=search_tool,
                for_tool=tool_name,
                missing=sorted(missing),
            )
            return AssistantMessage(role="assistant", tool_calls=[call])
        return None

    def _entity_gap_keywords(self, limit: int = 4) -> list[str]:
        """Observable query terms for a missing-entity search."""
        terms: list[str] = []
        for arguments in self.ledger.last_search_arguments.values():
            value = arguments.get("keywords") or arguments.get("key_words")
            if isinstance(value, str):
                value = [value]
            for item in value or []:
                text = str(item).strip()
                if text and text not in terms:
                    terms.append(text)
        if not terms:
            for atom in (*self.decision_card.must, *self.decision_card.prefer):
                text = str(atom).strip()
                if 1 < len(text) <= 8 and text not in terms:
                    terms.append(text)
                if len(terms) >= limit:
                    break
        return terms[:limit]

    def _framework_recommendation(self) -> AssistantMessage | None:
        """Finish recommendation tasks directly from observed candidates.

        This prevents keyword-changing search loops and trailing confirmation
        questions, while ensuring every recommended name came from the current
        subtask's environment results.

        Emitted at most once per subtask: repeating an identical recommendation
        cannot make progress, and a repeated framework message livelocks the
        conversation until max_steps (E-032).
        """
        if self.task_spec.action != "recommend":
            return None
        if not getattr(self, "framework_speech", False):
            # The model speaks; the framework does not (E-048).
            return None
        if self.runtime.phase != RuntimePhase.SELECT:
            return None
        if self._recommendation_delivered:
            return None
        # Give the policy model its own turn first: the stock agent's advantage
        # on recommendation subtasks is the *reasoning* it puts in front of the
        # user (naming the preference it satisfied), which this fallback cannot
        # produce. The fallback only steps in when the model has had two turns
        # in SELECT without settling the subtask (E-042).
        if getattr(self, "_select_turns", 0) < 2:
            return None
        from agent.runtime.ranking import CandidateRanker

        ranker = CandidateRanker()
        ranked = [
            candidate
            for candidate in self.ledger.shortlist(self.decision_card, limit=8)
            if candidate.name
        ]
        evidence_counts = ranker.preference_match_counts(ranked, self.decision_card)
        decisive_counts = ranker.decisive_preference_scores(ranked, self.decision_card)
        alignment = ranker.preference_alignment(ranked, self.decision_card)
        best_decisive = max(decisive_counts.values(), default=0.0)
        if best_decisive > 0:
            shortlist = [
                candidate
                for candidate in ranked
                if decisive_counts.get(candidate.candidate_id, 0.0) == best_decisive
            ][:3]
        else:
            # No decisive preference separates these candidates. Present the
            # ranking order instead of narrowing on fuzzy coverage: the order
            # already prefers proximity to the user's registered address and
            # the published rating, while an incidental substring match must
            # not hide a nearer, better-rated option (E-038).
            shortlist = ranked[:3]
        if not shortlist:
            return None
        # Observable ranking diagnostics: which candidates were considered, on
        # what evidence, and how close each one is to the user's own address.
        self.debug.emit(
            "recommendation_ranking",
            considered=[
                {
                    "candidate_id": candidate.candidate_id,
                    "evidence": evidence_counts.get(candidate.candidate_id, 0),
                    "decisive": decisive_counts.get(candidate.candidate_id, 0.0),
                    "proximity": location_rank(candidate, self.home_tokens),
                    "rating": candidate_rating(candidate),
                }
                for candidate in ranked
            ],
            best_decisive=best_decisive,
            home_tokens=list(self.home_tokens),
        )
        lines = ["根据当前需求和你的偏好，我的推荐是："]
        for index, candidate in enumerate(shortlist, 1):
            details = []
            if candidate.price is not None:
                details.append(f"¥{candidate.price:g}")
            if candidate.inventory is not None:
                details.append(f"库存{candidate.inventory}")
            matched = [
                atom.value for atom in alignment.matches(candidate) if atom.value
            ][:2]
            suffix = f"（{'，'.join(details)}）" if details else ""
            reason = f"｜符合：{'、'.join(matched)}" if matched else ""
            lines.append(f"{index}. {candidate.name}{suffix}{reason}")
        lines.append("首选为第 1 项，以上名称均来自当前实际候选结果。")
        self._recommendation_delivered = True
        return AssistantMessage(role="assistant", content="\n".join(lines))

    def _observe_assistant(self, assistant: AssistantMessage) -> None:
        if assistant.tool_calls:
            for call in assistant.tool_calls:
                self.tool_errors.register_proposal(
                    call.id,
                    call.name,
                    call.arguments,
                    self.tool_registry.role(call.name),
                )
            return
        if self.question_gate.is_question(assistant.content or ""):
            self.question_gate.commit(self._pending_question_decision, self.runtime)
            self.debug.emit(
                "question_committed",
                dimension=self._pending_question_decision.dimension,
                phase=self.runtime.phase.value,
            )

    def _framework_question(self) -> str:
        if not getattr(self, "framework_speech", False):
            # The model asks its own clarifying questions (E-048).
            return ""
        dimension = self.runtime.next_question_dimension()
        if not dimension:
            dimension = self._decision_gap_question_dimension()
        if dimension and self.runtime.phase != RuntimePhase.NEED_INFO:
            self.runtime.phase = RuntimePhase.NEED_INFO
        if self.runtime.phase != RuntimePhase.NEED_INFO:
            return ""
        if not dimension:
            self.runtime.phase = RuntimePhase.SEARCH
            return ""
        question = _GAP_QUESTIONS.get(dimension, f"请补充{dimension}。")
        self.runtime.commit_question(dimension)
        self.memory.commit_question(question)
        self.debug.emit("question_committed", dimension=dimension, source="framework")
        return question

    def _decision_gap_question_dimension(self) -> str:
        """Find one observable, decision-critical ambiguity.

        TaskSpec covers syntactic omissions. This second layer covers semantic
        ambiguity that appears only after combining the current task, memory,
        and live candidates. It never reads evaluator-only fields.
        """
        runtime = self.runtime
        if (
            len(runtime.asked_dimensions) >= 2
            or runtime.authorization.choice_delegated
            or self.task_spec.facet != "restaurant"
        ):
            return ""

        if "taste" not in runtime.asked_dimensions and "taste" not in runtime.resolved_slots:
            # Only facts selected into the bounded Decision Card are active for
            # this task. Scanning the full internal fact lists can resurrect a
            # lower-priority conditional alternative that was intentionally
            # omitted from the model-visible card and cause a false question.
            visible = self.decision_card.render()
            families = {
                canonical
                for canonical, markers in (
                    ("麻辣", ("麻辣", "牛油", "红油", "辣锅")),
                    ("菌汤", ("菌汤", "菌菇", "竹荪")),
                    ("番茄", ("番茄",)),
                    ("清汤", ("清汤", "清淡", "养生")),
                )
                if any(marker in visible for marker in markers)
            }
            if len(families) >= 2:
                return "taste"

        if (
            self.ledger.candidates
            and "dessert" not in runtime.asked_dimensions
            and "dessert" not in runtime.resolved_slots
            and (
                "套餐" in self.task_spec.instruction
                or any("套餐" in candidate.raw for candidate in self.ledger.candidates.values())
            )
        ):
            dessert_markers = (
                "冰汤圆", "冰粉", "红糖糍粑", "苋圆", "龟苓膏",
                "绿豆沙", "双皮奶", "冰淇淋", "冰酸奶", "甜品",
            )
            variants = {
                marker
                for candidate in self.ledger.candidates.values()
                if candidate.entity_type == "product"
                for marker in dessert_markers
                if marker in candidate.raw
            }
            known = " ".join(
                [
                    *self.decision_card.must,
                    *self.decision_card.prefer,
                    *runtime.resolved_slots.values(),
                ]
            )
            if len(variants) >= 2 and not any(marker in known for marker in variants):
                return "dessert"
        return ""

    def _resolve_user_selection(self, text: str) -> None:
        import re

        match = re.search(r"第([一二三四五1-5])", text or "")
        if not match:
            return
        mapping = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}
        index = mapping.get(
            match.group(1), int(match.group(1)) if match.group(1).isdigit() else 0
        )
        shortlist = self.ledger.shortlist(self.decision_card)
        if 1 <= index <= len(shortlist):
            self.runtime.selected_candidate_id = shortlist[index - 1].candidate_id
            self.runtime.selection_made = True
            self._note_choice_evidence()
            self.runtime.observe_candidates(
                len(shortlist),
                execution_ready=self.tool_registry.execution_ready(
                    self.ledger, self.decision_card
                ),
            )
            self._emit_choice_state("explicit_selection")

    def _emit_preference_alignment(self) -> None:
        """Expose capability-level evidence without evaluator information."""
        candidates = self.ledger.shortlist(self.decision_card, limit=8)
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

    def _finalize_visible_trajectory(self) -> None:
        if self.runtime.has_unresolved_payment_failure(
            self.ledger.pending_payment_ids
        ):
            self._record_lesson(
                "unpaid_order",
                ",".join(sorted(self.ledger.pending_payment_ids)),
                "After CREATE returns an unpaid order, explicitly obtain payment authorization and complete or decline payment.",
            )
        has_executable_candidate = bool(
            self.ledger.shortlist(self.decision_card, limit=1)
        ) and self.tool_registry.execution_ready(self.ledger, self.decision_card)
        settled, _source = self.runtime.choice_settled()
        if (
            self.runtime.authorization.create_authorized
            and has_executable_candidate
            # E-049: an authorized write that merely had an open choice is not a
            # missed write. Learning "force create after any executable
            # candidate" from those subtasks is what armed the premature-write
            # behaviour in the first place.
            and settled
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

    def dump_debug_trace(self, path, *, append: bool = True) -> None:
        """Persist only agent-visible structured events to a JSONL sidecar."""
        from pathlib import Path

        self.debug.dump_jsonl(Path(path), append=append)
