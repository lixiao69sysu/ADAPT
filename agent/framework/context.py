"""Agent context guard.

The stock harness re-sends the full message list on every generate() call and
never truncates, so a long tool-heavy conversation grew to 129K input tokens and
the whole trial was lost to a 400. This guard compacts the history before each
call:

  1. truncate oversized tool-result contents (search dumps are the main bloat);
  2. if still over budget, drop the oldest ``[assistant tool-call + its tool
     responses]`` block — that information is re-fetchable.

The list is mutated in place so the compaction persists across turns and the
guard is monotonic (the LLM is never re-sent content that was already trimmed).
"""

from agent.framework.config import CONTEXT_BUDGET_CHARS, MAX_TOOL_MSG_CHARS

_TRUNC_TAIL = "\n…[ADAPT Agent: 结果过长已截断]"


def _len(msg):
    n = len(getattr(msg, "content", "") or "")
    for tc in getattr(msg, "tool_calls", None) or []:
        n += len(str(getattr(tc, "arguments", "")))
    return n


def _total_len(msgs):
    return sum(_len(m) for m in msgs)


def _truncate_long_tool_msgs(msgs, max_tool_chars):
    from vita.data_model.message import ToolMessage

    for m in msgs:
        if isinstance(m, ToolMessage) and m.content and len(m.content) > max_tool_chars:
            m.content = m.content[:max_tool_chars] + _TRUNC_TAIL


def _drop_oldest_tool_block(msgs):
    """Delete the oldest ``[assistant tool-call + its tool responses]`` block."""
    from vita.data_model.message import AssistantMessage, ToolMessage

    for i, m in enumerate(msgs):
        if isinstance(m, AssistantMessage) and m.is_tool_call():
            n_tools = len(m.tool_calls or [])
            end = i + 1 + n_tools
            if end > len(msgs) or not all(isinstance(x, ToolMessage) for x in msgs[i + 1:end]):
                continue
            del msgs[i:end]
            return True
    return False


def compact_messages(msgs, budget_chars=None, max_tool_chars=None):
    """Compact ``msgs`` in place so the total length fits the budget."""
    budget_chars = budget_chars if budget_chars is not None else CONTEXT_BUDGET_CHARS
    max_tool_chars = max_tool_chars if max_tool_chars is not None else MAX_TOOL_MSG_CHARS
    if not msgs:
        return
    _truncate_long_tool_msgs(msgs, max_tool_chars)
    while _total_len(msgs) > budget_chars and len(msgs) > 1:
        if not _drop_oldest_tool_block(msgs):
            break
