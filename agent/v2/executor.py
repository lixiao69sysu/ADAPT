"""An executor that preserves the stock VitaBench request contract."""

from __future__ import annotations

from typing import Any

from vita.agent.base import ValidAgentInputMessage
from vita.agent.llm_agent import LLMAgentState
from vita.data_model.message import AssistantMessage, MultiToolMessage, SystemMessage
from vita.utils.llm_utils import generate


class StockCompatibleExecutor:
    """Append input and call VitaBench generate with the stock field ordering."""

    @staticmethod
    def append_input(message: ValidAgentInputMessage, state: LLMAgentState) -> None:
        if isinstance(message, MultiToolMessage):
            state.messages.extend(message.tool_messages)
        else:
            state.messages.append(message)

    def execute(
        self,
        *,
        message: ValidAgentInputMessage | None,
        state: LLMAgentState,
        model: str | None,
        tools: list[Any],
        enable_think: bool,
        llm_args: dict[str, Any],
        advisory_context: str = "",
    ) -> tuple[AssistantMessage | None, LLMAgentState]:
        if message is not None:
            self.append_input(message, state)
        messages = list(state.system_messages)
        if advisory_context:
            messages.append(
                SystemMessage(
                    role="system",
                    content=(
                        "ADAPT V2 advisory context follows. It is soft evidence; "
                        "the current user instruction and tool results take priority.\n\n"
                        + advisory_context
                    ),
                )
            )
        messages.extend(state.messages)
        assistant = generate(
            model=model,
            tools=tools,
            messages=messages,
            enable_think=enable_think,
            **llm_args,
        )
        if assistant is not None:
            state.messages.append(assistant)
        return assistant, state
