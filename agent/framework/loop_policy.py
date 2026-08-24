"""Tool-error loop breaker.

The stock harness never increments ``Orchestrator.num_errors`` (dead variable),
so ``--max-errors`` never fired and a loop of failing tool calls burned the
whole 300-step budget while re-sending the full history each turn. This module
supplies the consecutive-error accounting the orchestrator calls.
"""


def update_consecutive_errors(current, tool_msgs):
    """Return the new consecutive tool-error count.

    +1 if any tool message in this turn errored, else reset to 0. The reset-on-
    success keeps the count to *consecutive* failures, matching the CLI help
    text for ``--max-errors`` ("in a row").
    """
    if any(getattr(tm, "error", False) for tm in tool_msgs):
        return current + 1
    return 0
