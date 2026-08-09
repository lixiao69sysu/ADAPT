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
    search_call_count,
    SEARCH_TOOLS,
    should_force_act,
    should_force_select,
    stall_correction_message,
    over_questioning_correction_message,
    question_call_count,
    validate_progress,
)
from agent.harness.tool_guard import EntityLedger, ToolGuard, ValidationIssue

DECISION_RULES = """
## 硬性决策规则
1. 搜索工具调用 ≥2 次后，必须从已有候选中选择一个，不得再次调用搜索工具
2. 用户表达"随便"/"你看着办"/"都可以"/"你推荐"时，必须基于已有信息直接做决定，不得继续询问
3. 找到 ≥1 个候选后，必须选择最接近用户需求的一个继续执行
4. 如果所有候选都不完美，选择最接近的一个并说明妥协原因
5. 禁止重复调用完全相同的工具+参数组合
"""

TASK_DECOMPOSITION = """
## 任务执行流程（严格遵循）
每个消费任务必须按以下4步执行：
1. SEARCH：使用搜索工具找到候选（最多2次搜索）
2. FILTER：根据用户偏好和需求筛选候选（在脑中完成，无需工具）
3. DECIDE：选择最匹配的候选（必须明确说明选择理由）
4. EXECUTE：调用创建订单/支付工具完成交易

关键约束：
- 步骤1和步骤2可以同时进行（搜索后立即筛选）
- 步骤3必须在步骤2之后（不能跳过筛选直接选择）
- 步骤4必须在步骤3之后（不能跳过选择直接执行）
- 如果步骤1没有找到候选，尝试换关键词重新搜索，但总搜索次数不超过2次
"""


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
    step_count: int = 0          # 当前子任务的步数计数
    last_reflection: int = 0     # 上次 reflection 的步数


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

        # 跨步持久化的工具禁用：在生成前检查
        _sc = search_call_count(state)
        _qc = question_call_count(state)
        logger.info("ADAPT_STEP step={} search_count={} question_count={} disabled={}", state.step_count, _sc, _qc, state.disabled_tool_names)
        if should_force_select(state):
            for tool in self.tools:
                if any(s in tool.name.lower() for s in SEARCH_TOOLS):
                    state.disabled_tool_names.add(tool.name)
            logger.info("ADAPT_SEARCH_DISABLED step={} count={}", state.step_count, _sc)
        if should_force_act(state):
            for tool in self.tools:
                if any(s in tool.name.lower() for s in {"suggest_question", "question"}):
                    state.disabled_tool_names.add(tool.name)
            logger.info("ADAPT_QUESTION_DISABLED step={} count={}", state.step_count, _qc)

        rejected_usage = {}
        rejected_cost = 0.0
        correction = None
        force_progress = False
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
            # Reflection: 每 10 步回顾进度
            if state.step_count > 0 and state.step_count % 10 == 0 and state.step_count != state.last_reflection:
                state.last_reflection = state.step_count
                reflection = f"""
## 进度反思（第 {state.step_count} 步）
请回顾当前进展：
- 已创建订单：{len(state.created_order_ids)} 个
- 已搜索次数：{sum(v for k,v in state.tool_call_counts.items() if any(s in k.lower() for s in SEARCH_TOOLS))}
- 距离目标还缺什么？

如果已找到候选但未下单，立即创建订单。
如果搜索无结果，换关键词或询问用户。
不要重复已完成的步骤。
"""
                system_content += f"\n{reflection}"
            if correction:
                system_content += f"\n\n## ADAPT 内部纠错\n{correction}\n{DECISION_RULES}"
            internal_system = [
                SystemMessage(role="system", content=system_content)
            ]
            active_tools = [
                tool
                for tool in self.tools
                if tool.name not in state.disabled_tool_names
            ]
            _llm_args = {k: v for k, v in self.llm_args.items() if k != "tool_choice"}
            draft = generate(
                model=self.llm,
                tools=active_tools,
                tool_choice=None,
                messages=internal_system + self._page_messages(state.messages),
                enable_think=self.enable_think,
                **_llm_args,
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
                state.step_count += 1
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
