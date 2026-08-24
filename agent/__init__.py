"""Complete ADAPT consumer agent assembled against a read-only VitaBench."""

__all__ = ["ADAPTAgent", "ADAPTMemory"]


def __getattr__(name):
    if name == "ADAPTAgent":
        from agent.adapt_agent import ADAPTAgent
        return ADAPTAgent
    if name == "ADAPTMemory":
        from agent.memory import ADAPTMemory
        return ADAPTMemory
    raise AttributeError(name)
