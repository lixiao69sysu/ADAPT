# VitaBench Conservative Loop Guard Design

## Goal

Preserve VitaBench evaluation fidelity by limiting early termination to repeated
real tool errors. Successful tool responses must never be treated as errors,
even when the same read-only query or response appears multiple times.

## Evidence

The repaired 45-user continuation contained 620 evaluated subtasks. Of those,
155 terminated with `TOO_MANY_ERRORS`, and 123 terminated before 22 messages.
Those short trajectories could not have reached the configured ten-error limit.

The current `_record_tool_outcome` implementation counts every identical tool
outcome across the full subtask, including successful outcomes and outcomes
separated by other calls. Three matches terminate the subtask. This caused, for
example, repeated successful searches and the non-error
`suggest_question_tool` exhaustion response to be classified as
`TOO_MANY_ERRORS`.

The overall personalization result reports `TOO_MANY_ERRORS` whenever any one
of a user's subtasks has that termination reason. This explains why all 45
continued users received that overall label; it does not mean every subtask
failed.

## Selected Approach

Use a conservative, error-only circuit breaker:

1. A successful `ToolMessage` never increments an error counter and never
   terminates a subtask.
2. A successful `ToolMessage` resets the consecutive-identical-error streak.
3. A failed `ToolMessage` increments the existing cumulative `num_errors`.
4. Three consecutive failures with the same tool name, canonical arguments,
   and error content terminate the subtask with `TOO_MANY_ERRORS`.
5. A different failure signature starts a new consecutive streak at one.
6. The existing cumulative limit of ten real errors remains unchanged.
7. The existing `max_steps=50` remains the fallback for successful or varied
   no-progress loops.
8. Runtime prompt truncation at 120,000 tokens remains the context-overflow
   safety boundary.

The cumulative `_tool_outcome_counts` state and the rule that terminates after
three identical outcomes will be removed.

## Alternatives Rejected

### Consecutive successful-outcome breaker

Stopping after three consecutive identical successful read-only results would
shorten obvious search loops, but it can still terminate legitimate repeated
queries. Benchmark fidelity is more important than saving a few minutes on a
looping subtask.

### Upstream-only limits

Removing all custom protection would be closest to upstream VitaBench, but it
would allow a deterministic failing call to repeat until the ten-error limit.
Keeping the narrowly scoped three-identical-error breaker prevents that waste
without reclassifying successful behavior.

## Control Flow

For each environment tool response:

1. Build an exact failure signature from tool name, canonical JSON arguments,
   and returned error content.
2. If the response succeeded, clear the previous failure signature and streak,
   then return.
3. If the response failed, increment `num_errors`.
4. Increment the streak only when the failure signature matches the immediately
   preceding failed response; otherwise replace the signature and set the
   streak to one.
5. Terminate at a streak of three identical failures.
6. The run loop independently terminates when cumulative errors reach ten or
   total steps reach fifty.

## Tests

The focused orchestrator tests will demonstrate these behaviors:

- repeated identical successful outcomes, including interleaved outcomes, do
  not terminate the subtask;
- a successful outcome resets the identical-error streak;
- three consecutive identical failures terminate the subtask;
- different failure arguments increment cumulative errors but do not trigger
  the three-identical-error breaker;
- the existing cumulative error and maximum-step limits remain unchanged.

Tests must be written or updated first and observed failing for the expected
reason before production code changes.

## Verification

After the focused tests pass:

1. Run all ADAPT memory tests.
2. Run the offline VitaBench orchestrator and memory-query tests.
3. Run the known looping user as a smoke test with `max_tokens=1024`,
   `truncate_prompt_tokens=120000`, `max_steps=50`, and concurrency one.
4. Confirm successful repeated tool results no longer produce
   `TOO_MANY_ERRORS`, real identical failures still do, and no context overflow
   occurs.

## Result Preservation and Rerun Policy

The completed mixed-configuration result remains untouched as diagnostic
evidence. Any publishable score must use a new output path and rerun all 56
users from zero with one consistent model configuration and the conservative
guard.

## Non-Goals

- Do not change VitaBench reward calculation or evaluator prompts.
- Do not change ADAPT memory ranking or proactive-question policy.
- Do not lower `max_steps` or the cumulative ten-error limit.
- Do not infer semantic equivalence between different tool arguments or error
  messages.
