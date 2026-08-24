"""ADAPT framework tunables, overridable via environment variables."""

import os


def _int_env(name, default):
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


# Search-result trimming: the stock search tools returned the top-100 fuzzy
# matches with no relevance floor (27-38K chars per tool message). These knobs
# cap the list and drop near-irrelevant hits so the agent context stays small.
SEARCH_TOP_K = _int_env("ADAPT_SEARCH_TOP_K", 20)
SEARCH_MIN_SCORE = _int_env("ADAPT_SEARCH_MIN_SCORE", 45)

# Context guard: compact the agent history before each LLM call so a long
# tool-heavy conversation never overflows the model window (v17 trials died at
# 129K input tokens). Budget is in characters (a reasonable token proxy).
CONTEXT_BUDGET_CHARS = _int_env("ADAPT_CONTEXT_BUDGET_CHARS", 40000)
MAX_TOOL_MSG_CHARS = _int_env("ADAPT_MAX_TOOL_MSG_CHARS", 4000)
