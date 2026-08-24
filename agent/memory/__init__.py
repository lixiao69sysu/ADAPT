"""ADAPT memory system.

Public API:
    ADAPTMemory(BaseMemory) - pluggable into VitaBench --memory-class.

Sub-modules:
    stream      - Memory Stream: event storage with type/importance/timestamp
    signals     - Signal parser: raw interactions -> preference signals
    retrieval   - 3D retrieval: relevance x recency x importance
    reflection  - Insight synthesis + preference drift detection
    lifecycle   - Fact lifecycle + selective forgetting
"""
__all__ = ["ADAPTMemory"]


def __getattr__(name):
    if name == "ADAPTMemory":
        from agent.memory.adapt_memory import ADAPTMemory
        return ADAPTMemory
    raise AttributeError(name)
