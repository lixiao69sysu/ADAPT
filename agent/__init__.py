"""ADAPT - Agent with Dynamic Adaptive Preferences Toward Sustained Consumption Goals.

A long-term personalized consumer agent. This package implements the ADAPT
memory system as an independent module that plugs into VitaBench 2.0 via the
--memory-class interface, without modifying VitaBench's original agent.

Usage:
    vita run --memory-class agent.memory.ADAPTMemory ...
"""
"""ADAPT agent package."""

from agent.adapt_agent import ADAPTAgent, ADAPTAgentState

__all__ = ["ADAPTAgent", "ADAPTAgentState"]
