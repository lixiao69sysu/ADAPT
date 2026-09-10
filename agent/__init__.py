"""Complete ADAPT consumer agent assembled against a read-only VitaBench."""

__all__ = ["ADAPTAgent", "ADAPTMemory", "ADAPTV2", "V2FeatureFlags"]


def __getattr__(name):
    if name == "ADAPTAgent":
        from agent.adapt_agent import ADAPTAgent
        return ADAPTAgent
    if name == "ADAPTMemory":
        from agent.memory import ADAPTMemory
        return ADAPTMemory
    if name == "ADAPTV2":
        from agent.v2 import ADAPTV2
        return ADAPTV2
    if name == "V2FeatureFlags":
        from agent.v2 import V2FeatureFlags
        return V2FeatureFlags
    raise AttributeError(name)
