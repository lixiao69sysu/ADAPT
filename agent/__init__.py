"""ADAPT data layer, assembled against a read-only VitaBench."""

__all__ = ["ADAPTMemory"]


def __getattr__(name):
    if name == "ADAPTMemory":
        from agent.memory import ADAPTMemory
        return ADAPTMemory
    raise AttributeError(name)
