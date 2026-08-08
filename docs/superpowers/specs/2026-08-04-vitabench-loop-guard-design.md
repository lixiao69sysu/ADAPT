# VitaBench Loop-Guard Repair Design

## Goal

Repair the two confirmed repetition paths without changing VitaBench scoring semantics, then resume the existing Qwen3.6-35B-A3B ADAPT run from its 11 successful checkpoints.

## Evidence and scope

Two deterministic failure patterns are in scope:

1. `ADAPTMemory.read(query)` mutates the proactive-question budget while VitaBench is only rendering prompt and memory previews. The budget is exhausted before the agent can call `suggest_question_tool`, which then returns an empty string. Qwen repeatedly calls the same tool because the empty response gives it no actionable terminal signal.
2. Domain tool exceptions become `ToolMessage(error=True)`, but `Orchestrator.num_errors` is never incremented. An invalid product ID can therefore be retried with identical arguments until `max_steps`, growing the context until the 131,072-token service limit is exceeded.

The repair will not add RAG, change benchmark tasks, alter rewards, repair hallucinated IDs, or silently count failed tasks as successful.

## Considered approaches

### A. State-purity repair plus bounded error guard — selected

Make memory reads side-effect free, consume the question budget only when a question is actually emitted, return an explicit no-question response, count tool errors, and terminate repeated identical failures after a small fixed threshold. This addresses both root causes while preserving the benchmark and tool behavior.

### B. Prompt-only mitigation

Tell the model not to repeat failed calls. This is low effort but not reliable: the observed Qwen trajectory already ignores identical error feedback, and an empty tool response contains nothing the prompt can reason over.

### C. Context truncation or lower `max_tokens` only

This prevents some 400 responses but leaves the loops intact, wastes compute, and still produces `max_steps` terminations. It is retained only as a launch-time safety margin, not as the repair.

## Design

### Proactive question state

`ProactiveEngine` will separate question detection from question consumption. Preview and prompt reads may compute and display a suggestion but must not increment `asked_this_subtask`. The agent-callable tool will be the only path that consumes the budget, exactly once when it returns a real question.

When the model passes a fully formed direct question (for example, text beginning with `请问` or ending in a question mark), `suggest_question_tool` will return that question verbatim and consume one budget unit. When there is no question or the budget is exhausted, it will return an explicit Chinese instruction stating that no further clarification question is available and that the agent must continue using existing information. It will not return `""`.

### Tool-error accounting and duplicate guard

After every environment tool call, `Orchestrator` will increment `num_errors` for `ToolMessage.error=True`, making the existing `max_errors` option effective.

The orchestrator will track each deterministic outcome signature as `(tool name, canonicalized arguments, returned content)`. Three occurrences of the same signature within one subtask terminate it with the existing `too_many_errors` reason, even when other calls are interleaved or the tool did not raise. This catches the observed search/question cycles that returned successfully while filling the context. Calls with changed arguments or changed results have distinct signatures and do not share a count.

### Context safety and resume operation

The resumed model configuration will use `max_tokens=1024` and vLLM's native `truncate_prompt_tokens=120000`. Exact and deterministic loops are stopped by the guards; varied long trajectories remain bounded by `max_steps`, while request-side truncation prevents one oversized subtask from aborting and retrying the entire user task.

The existing result file remains authoritative. VitaBench's built-in resume mechanism will load it, skip the 11 completed `(trial, task_id, seed)` tuples, and run the remaining 45 users. The resumed process will use `WARNING` console verbosity with stdout/stderr redirected to a file, eliminating managed-PTY backpressure while retaining diagnostics.

## Tests

Tests will be written before production changes and must fail for the observed reasons:

1. Repeated `read(query)` calls do not consume proactive-question budget.
2. `suggest_question_tool` consumes at most one unit per real returned question and never returns an empty string.
3. A fully formed direct question is returned verbatim and consumes one budget unit.
4. A tool message with `error=True` increments `num_errors`.
5. Three identical outcomes terminate the subtask even when successful calls are interleaved; changed arguments or results remain independent.
6. Existing ADAPT memory tests and relevant VitaBench orchestrator tests remain green.

Before the full resume, a single-user smoke run will verify that tools, memory, evaluation, checkpoint writing, error accounting, and the 1024-token output cap work together. The smoke result will use a separate file and will not modify the 11-run checkpoint.

## Success criteria

- No proactive preview consumes question budget.
- No memory tool response is empty.
- Identical failed calls cannot run to 50 steps.
- Existing tests and new regression tests pass.
- Smoke evaluation exits successfully with all of its subtasks evaluated.
- Resume reports 11 completed and 45 remaining before starting work.
- Detailed evaluation logs are written to a normal file rather than an undrained PTY.
