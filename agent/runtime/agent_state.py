"""Serializable rollback boundary for the complete ADAPT agent."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from vita.agent.llm_agent import LLMAgentState


class ADAPTAgentState(LLMAgentState):
    """Conversation state plus a lossless snapshot of ADAPT's controller."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    framework_state: dict[str, Any] = Field(default_factory=dict)
    state_version: int = 1
