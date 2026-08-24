"""Private utilities called only by the external :class:`ADAPTAgent`.

VitaBench never imports this package. The benchmark task data, tools,
orchestrator, simulator and evaluator stay pristine.

Env knobs (all optional):
    ADAPT_CONTEXT_BUDGET_CHARS   default 40000 — agent history budget before compaction
    ADAPT_MAX_TOOL_MSG_CHARS     default 4000  — max chars kept per tool result
"""
__version__ = "2.0.0"
