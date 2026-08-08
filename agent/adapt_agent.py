"""ADAPT Agent-side execution harness for VitaBench personalization tasks."""

from __future__ import annotations

from typing import Optional

from loguru import logger
from pydantic import Field

from vita.agent.llm_agent import LLMAgentState
from vita.agent.personalization_agent import PersonalizationAgent
from vita.data_model.message import (
    AssistantMessage,
    Message,
    MultiToolMessage,
    SystemMessage,
    ToolMessage,
)
from vita.utils.llm_utils import generate

from agent.harness.progress_guard import (
    is_write_tool,
    observe_result,
    record_calls,
    SEARCH_TOOLS,
    should_force_act,
    should_force_select,
    stall_correction_message,
    over_questioning_correction_message,
    validate_progress,
)
from agent.harness.tool_guard import EntityLedger, ToolGuard, ValidationIssue


class ADAPTAgentState(LLMAgentState):
    """Conversation state plus Agent-visible entity provenance."""

    known_entity_ids: set[str] = Field(default_factory=set)
    entity_groups: list[set[str]] = Field(default_factory=list)
    rejected_tool_drafts: int = 0
    tool_call_counts: dict[str, int] = Field(default_factory=dict)
    pending_tool_names: dict[str, str] = Field(default_factory=dict)
    created_order_ids: set[str] = Field(default_factory=set)
    last_order_result: str = ""
    goal_completed: bool = False
    disabled_tool_names: set[str] = Field(default_factory=set)


def _add_usage(total: dict, usage: Optional[dict]) -> dict:
    merged = dict(total)
    for key, value in (usage or {}).items():
        if isinstance(value, (int, float)):
            merged[key] = merged.get(key, 0) + value
    return merged


class ADAPTAgent(PersonalizationAgent):
    """PersonalizationAgent with bounded validation and draft recovery."""

    def __init__(
        self,
        *args,
        max_internal_regenerations: int = 2,
        max_prompt_message_chars: int = 60_000,
        max_tool_result_chars: int = 8_000,
        min_tool_result_chars: int = 1_000,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.max_internal_regenerations = max_internal_regenerations
        self.max_prompt_message_chars = max_prompt_message_chars
        self.max_tool_result_chars = max_tool_result_chars
        self.min_tool_result_chars = min_tool_result_chars
        self._tool_guard = ToolGuard(self.tools)

    def update_tools(self, tools):
        super().update_tools(tools)
        self._tool_guard.update_tools(tools)

    def get_init_state(
        self,
        message_history: Optional[list[Message]] = None,
    ) -> ADAPTAgentState:
        base = super().get_init_state(message_history=message_history)
        ledger = EntityLedger()
        for message in base.messages:
            ledger.observe(message)
        return ADAPTAgentState(
            system_messages=base.system_messages,
            messages=base.messages,
            known_entity_ids=set(ledger.known_ids),
            entity_groups=[set(group) for group in ledger.entity_groups],
        )

    def _issues(
        self,
        message: AssistantMessage,
        ledger: EntityLedger,
    ) -> list[ValidationIssue]:
        issues = []
        for call in message.tool_calls or []:
            issues.extend(self._tool_guard.validate(call, ledger))
        return issues

    @staticmethod
    def _page_content(message: Message, limit: int) -> Message:
        content = getattr(message, "content", None)
        if not isinstance(content, str) or len(content) <= limit:
            return message
        marker = (
            "\n...[ADAPT context page: must choose from the shown candidates; "
            "Do not repeat the same search.]"
        )
        keep = max(0, limit - len(marker))
        prefix = content[:keep]
        if "\n" in prefix:
            prefix = prefix.rsplit("\n", 1)[0]
        return message.model_copy(
            update={"content": prefix + marker}
        )

    def _page_messages(self, messages: list[Message]) -> list[Message]:
        """Build a bounded model-only view without mutating the public trace."""
        paged = []
        seen_results: dict[tuple[str, str], str] = {}
        for message in messages:
            if not isinstance(message, ToolMessage) or is_write_tool(message.name):
                paged.append(message)
                continue
            key = (message.name, message.content or "")
            if key in seen_results:
                paged.append(
                    message.model_copy(
                        update={
                            "content": (
                                "[ADAPT duplicate result: identical to tool result "
                                f"{seen_results[key]}; Do not repeat this lookup. "
                                "Use the earlier candidates and advance the task.]"
                            )
                        }
                    )
                )
                continue
            seen_results[key] = message.id
            paged.append(self._page_content(message, self.max_tool_result_chars))

        def size() -> int:
            return sum(
                len(message.content)
                for message in paged
                if isinstance(getattr(message, "content", None), str)
            )

        # Search results are normally relevance ordered. If the conversation is
        # still too large, shrink older tool pages first and keep the newest page
        # at the higher-fidelity limit.
        if size() > self.max_prompt_message_chars:
            tool_indexes = [
                index
                for index, message in enumerate(paged)
                if isinstance(message, ToolMessage)
            ]
            for index in tool_indexes[:-1]:
                if size() <= self.max_prompt_message_chars:
                    break
                paged[index] = self._page_content(
                    paged[index], self.min_tool_result_chars
                )

        # A pathological dialogue can also contain long prose. Preserve the six
        # newest messages verbatim and page older prose only as a final backstop.
        if size() > self.max_prompt_message_chars:
            for index in range(max(0, len(paged) - 6)):
                if size() <= self.max_prompt_message_chars:
                    break
                paged[index] = self._page_content(paged[index], 1_000)

        original_size = sum(
            len(message.content)
            for message in messages
            if isinstance(getattr(message, "content", None), str)
        )
        paged_size = size()
        if paged_size < original_size:
            logger.info(
                "ADAPT_CONTEXT_PAGED message_chars={} paged_chars={}",
                original_size,
                paged_size,
            )
        return paged

    def _disable_stalled_tool_family(
        self,
        tool_name: str,
        state: ADAPTAgentState,
    ) -> None:
        if "search" in tool_name:
            state.disabled_tool_names.update(
                tool.name
                for tool in self.tools
                if "search" in tool.name
            )
        else:
            state.disabled_tool_names.add(tool_name)

    @staticmethod
    def _latest_candidate_context(messages: list[Message]) -> str:
        for message in reversed(messages):
            if (
                isinstance(message, ToolMessage)
                and not message.error
                and not is_write_tool(message.name)
                and message.content
            ):
                return message.content[:3_000]
        return ""

    def generate_next_message(
        self,
        message,
        state: ADAPTAgentState,
    ) -> tuple[AssistantMessage, ADAPTAgentState]:
        inbound = (
            message.tool_messages
            if isinstance(message, MultiToolMessage)
            else [message]
        )
        state.messages.extend(inbound)

        ledger = EntityLedger(
            set(state.known_entity_ids),
            [set(group) for group in state.entity_groups],
        )
        for item in inbound:
            ledger.observe(item)
            if isinstance(item, ToolMessage):
                observe_result(item, state)
        state.known_entity_ids = set(ledger.known_ids)
        state.entity_groups = [set(group) for group in ledger.entity_groups]

        if state.goal_completed:
            order_detail = state.last_order_result[:2_000]
            detail = f"\n订单记录：{order_detail}" if order_detail else ""
            terminal = AssistantMessage(
                role="assistant",
                content=(
                    "支付成功，当前消费任务已经完成。"
                    f"{detail}\n###STOP###"
                ),
            )
            state.messages.append(terminal)
            return terminal, state

        rejected_usage = {}
        rejected_cost = 0.0
        correction = None
        force_progress = False
        _search_disabled = False
        for _ in range(self.max_internal_regenerations + 1):
            system_content = "\n\n".join(
                message.content for message in state.system_messages
            )
            if should_force_act(state) and not correction:
                correction = over_questioning_correction_message(state)
                force_progress = True
            if should_force_select(state) and not force_progress:
                correction = stall_correction_message(state)
                candidate_context = self._latest_candidate_context(state.messages)
                if candidate_context:
                    correction += f"\n已有候选结果：\n{candidate_context}"
                force_progress = True
            if correction:
                system_content += f"\n\n## ADAPT 内部纠错\n{correction}"
            internal_system = [
                SystemMessage(role="system", content=system_content)
            ]
            active_tools = [
                tool
                for tool in self.tools
                if tool.name not in state.disabled_tool_names
            ]
            draft = generate(
                model=self.llm,
                tools=active_tools,
                tool_choice=("required" if force_progress and active_tools else None),
                messages=internal_system + self._page_messages(state.messages),
                enable_think=self.enable_think,
                **self.llm_args,
            )
            if draft is None:
                raise RuntimeError("Agent model returned no message")

            issues = self._issues(draft, ledger)
            for call in draft.tool_calls or []:
                progress_issues = validate_progress(call, state)
                issues.extend(progress_issues)
                if any(
                    issue.code == "repeated_tool_call"
                    for issue in progress_issues
                ):
                    self._disable_stalled_tool_family(call.name, state)
                    force_progress = True
            if not issues:
                draft.cost = (draft.cost or 0.0) + rejected_cost
                draft.usage = _add_usage(rejected_usage, draft.usage)
                state.messages.append(draft)
                record_calls(draft.tool_calls or [], state)
                return draft, state

            state.rejected_tool_drafts += 1
            rejected_cost += draft.cost or 0.0
            rejected_usage = _add_usage(rejected_usage, draft.usage)
            logger.warning(
                "ADAPT_TOOL_DRAFT_REJECTED attempt={} issues={}",
                state.rejected_tool_drafts,
                [issue.code for issue in issues],
            )
            correction = (
                "上一份工具调用草案未发送，因为它不符合当前公开工具信息：\n- "
                + "\n- ".join(issue.message for issue in issues)
                + "\n请重新规划。只能使用当前工具 schema，以及用户消息或成功工具"
                "返回中已经出现的实体 ID；如果信息不足，请向用户提出一个具体问题，"
                "不要猜测 ID。"
            )
            if force_progress:
                candidate_context = self._latest_candidate_context(state.messages)
                correction += (
                    "\n这是无进展循环。重复搜索工具现已禁用；必须从已有候选中选择"
                    "满足用户约束的项目，并调用详情、创建订单或支付工具推进任务。"
                )
                if candidate_context:
                    correction += f"\n已有候选结果：\n{candidate_context}"

        fallback = AssistantMessage(
            role="assistant",
            content="我还缺少执行该操作所需的有效信息，请提供相关订单或商品记录。",
            cost=rejected_cost,
            usage=rejected_usage or None,
        )
        state.messages.append(fallback)
        return fallback, state
