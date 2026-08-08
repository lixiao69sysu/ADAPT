# VitaBench Loop-Guard Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the two observed VitaBench repetition loops, verify the repair, and resume the Qwen3.6 ADAPT evaluation from 11 successful checkpoints.

**Architecture:** Make ADAPT memory reads side-effect free and consume proactive-question budget only through an actual tool response. Add centralized failed-tool accounting and a three-identical-failures circuit breaker to the existing Orchestrator, then resume with a smaller output reservation and file-backed logging.

**Tech Stack:** Python 3.11, pytest, Pydantic message models, VitaBench Orchestrator, ADAPTMemory, vLLM OpenAI-compatible API.

---

### Task 1: Make proactive-question reads side-effect free

**Files:**
- Modify: `agent/tests/test_adapt_memory.py`
- Modify: `agent/memory/proactive.py:93-123`
- Modify: `agent/memory/adapt_memory.py:71-92,172-202`

- [ ] **Step 1: Write the failing tests**

Append these tests to `TestProactiveAsking`:

```python
    def test_repeated_reads_do_not_consume_question_budget(self):
        m = ADAPTMemory(language="chinese", max_questions=2)

        first = m.read("下周去上海，帮我买张票")
        second = m.read("下周去上海，帮我买张票")

        assert "建议先向用户询问" in first
        assert "建议先向用户询问" in second
        assert m.proactive.asked_this_subtask == 0

    def test_question_tool_consumes_once_and_never_returns_empty(self):
        m = ADAPTMemory(language="chinese", max_questions=1)

        question = m.suggest_question_tool("下周去上海，帮我买张票")
        exhausted = m.suggest_question_tool("下周去上海，帮我买张票")

        assert "飞机还是高铁" in question
        assert m.proactive.asked_this_subtask == 1
        assert exhausted
        assert "无需继续询问" in exhausted

    def test_question_tool_returns_direct_question_verbatim(self):
        m = ADAPTMemory(language="chinese", max_questions=1)
        direct = "请问您的头发长度是长发、中长发还是短发？"

        result = m.suggest_question_tool(direct)

        assert result == direct
        assert m.proactive.asked_this_subtask == 1
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
source /home/ai/anaconda3/etc/profile.d/conda.sh
conda activate lcpy311
PYTHONPATH=/home/ai/student/lx/ADAPT pytest agent/tests/test_adapt_memory.py::TestProactiveAsking -q
```

Expected: the first new test fails because `read()` increments the budget, and the second fails because the tool double-counts or returns `""`.

- [ ] **Step 3: Add non-consuming detection to `ProactiveEngine`**

Change `decide_to_ask` to accept `consume: bool = False`. Compute the selected question first, then increment only when `consume` is true:

```python
    def decide_to_ask(
        self,
        instruction: str,
        memory_text: str,
        domain: Optional[str],
        consume: bool = False,
    ) -> Optional[str]:
        if consume and self.asked_this_subtask >= self.max_questions:
            return None

        question = None
        if self._missing_transport(instruction):
            question = "这趟出行您是倾向飞机还是高铁呢？"
        else:
            domain = domain or "delivery"
            if self.is_vague(instruction) and not self._domain_covered(memory_text, domain):
                question = DOMAIN_QUESTIONS[domain][0][1]
            elif domain in ("delivery", "instore") and self._missing_taste(instruction):
                question = DOMAIN_QUESTIONS[domain][0][1]

        if question and consume:
            self.asked_this_subtask += 1
        return question
```

- [ ] **Step 4: Route reads and tool calls through the correct consumption mode**

Update the ADAPT helper and public tool:

```python
    def _suggest_question_inner(
        self,
        instruction: str,
        domain: Optional[str] = None,
        consume: bool = False,
    ) -> Optional[str]:
        if domain is None:
            domain = self.scorer.domain(instruction)
        memory_text = self._read_base(instruction)
        return self.proactive.decide_to_ask(
            instruction, memory_text, domain, consume=consume
        )

    def suggest_question(self, instruction: str, domain: Optional[str] = None) -> Optional[str]:
        return self._suggest_question_inner(instruction, domain, consume=True)

    @is_tool(ToolType.READ)
    def suggest_question_tool(self, instruction: str) -> str:
        direct = instruction.strip()
        if direct.startswith(("请问", "您希望", "您想")) or direct.endswith(("?", "？")):
            if self.proactive.asked_this_subtask < self.proactive.max_questions:
                self.proactive.asked_this_subtask += 1
                return direct
        q = self.suggest_question(instruction)
        if q:
            return q
        return "无需继续询问；请使用已有信息继续完成任务，不要再次调用本工具。"
```

Keep `read()` on the default `consume=False` path and remove the existing second increment.

- [ ] **Step 5: Run the focused tests and verify GREEN**

Run the command from Step 2. Expected: all `TestProactiveAsking` tests pass.

- [ ] **Step 6: Commit the isolated memory repair**

```bash
git add agent/tests/test_adapt_memory.py agent/memory/proactive.py agent/memory/adapt_memory.py
git commit -m "fix: keep ADAPT memory previews side-effect free"
```

### Task 2: Count tool errors and break identical failure loops

**Files:**
- Create: `evaluation/vitabench/tests/test_orchestrator_tool_errors.py`
- Modify: `evaluation/vitabench/src/vita/orchestrator/orchestrator.py:1-8,76-84,223-230,327-348`

- [ ] **Step 1: Write the failing orchestrator tests**

Create the test file:

```python
from vita.data_model.message import ToolCall, ToolMessage
from vita.data_model.simulation import TerminationReason
from vita.orchestrator.orchestrator import Orchestrator


def make_orchestrator():
    orchestrator = object.__new__(Orchestrator)
    orchestrator.num_errors = 0
    orchestrator.max_errors = 10
    orchestrator.done = False
    orchestrator.termination_reason = None
    orchestrator._last_failed_tool_signature = None
    orchestrator._consecutive_identical_tool_errors = 0
    orchestrator._tool_outcome_counts = {}
    orchestrator.max_identical_tool_errors = 3
    return orchestrator


def failed_message(call, content="Error: product not found"):
    return ToolMessage(
        id=call.id,
        name=call.name,
        role="tool",
        content=content,
        requestor="assistant",
        error=True,
    )


def test_failed_tool_message_increments_error_count():
    orchestrator = make_orchestrator()
    call = ToolCall(id="1", name="create_order", arguments={"product_id": "bad"})

    orchestrator._record_tool_outcome(call, failed_message(call))

    assert orchestrator.num_errors == 1
    assert not orchestrator.done


def test_three_identical_failures_terminate_subtask():
    orchestrator = make_orchestrator()
    call = ToolCall(id="1", name="create_order", arguments={"product_id": "bad"})

    for _ in range(3):
        orchestrator._record_tool_outcome(call, failed_message(call))

    assert orchestrator.num_errors == 3
    assert orchestrator.done
    assert orchestrator.termination_reason == TerminationReason.TOO_MANY_ERRORS


def test_changed_or_successful_call_resets_identical_failure_streak():
    orchestrator = make_orchestrator()
    first = ToolCall(id="1", name="create_order", arguments={"product_id": "bad-1"})
    second = ToolCall(id="2", name="create_order", arguments={"product_id": "bad-2"})

    orchestrator._record_tool_outcome(first, failed_message(first))
    orchestrator._record_tool_outcome(second, failed_message(second))
    success = ToolMessage(
        id=second.id,
        name=second.name,
        role="tool",
        content="created",
        requestor="assistant",
        error=False,
    )
    orchestrator._record_tool_outcome(second, success)

    assert orchestrator._consecutive_identical_tool_errors == 0
    assert not orchestrator.done


def test_three_identical_successful_outcomes_terminate_even_when_interleaved():
    orchestrator = make_orchestrator()
    repeated = ToolCall(id="1", name="search", arguments={"keywords": ["美甲"]})
    other = ToolCall(id="2", name="suggest", arguments={"instruction": "请问偏好？"})
    repeated_result = ToolMessage(
        id=repeated.id, name=repeated.name, role="tool",
        content="same catalog", requestor="assistant", error=False,
    )
    other_result = ToolMessage(
        id=other.id, name=other.name, role="tool",
        content="请问偏好？", requestor="assistant", error=False,
    )

    for _ in range(3):
        orchestrator._record_tool_outcome(repeated, repeated_result)
        if not orchestrator.done:
            orchestrator._record_tool_outcome(other, other_result)

    assert orchestrator.done
    assert orchestrator.termination_reason == TerminationReason.TOO_MANY_ERRORS
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
source /home/ai/anaconda3/etc/profile.d/conda.sh
conda activate lcpy311
cd /home/ai/student/lx/ADAPT/evaluation/vitabench
PYTHONPATH=/home/ai/student/lx/ADAPT pytest tests/test_orchestrator_tool_errors.py -q
```

Expected: the new successful-outcome test fails because successful outcomes are not counted.

- [ ] **Step 3: Implement the minimal failed-call accounting helper**

Import `json`, initialize three state fields, and add:

```python
    def _record_tool_outcome(self, tool_call, tool_message: ToolMessage) -> None:
        signature = (
            tool_call.name,
            json.dumps(tool_call.arguments, sort_keys=True, ensure_ascii=False, default=str),
            tool_message.content,
        )
        count = self._tool_outcome_counts.get(signature, 0) + 1
        self._tool_outcome_counts[signature] = count
        if count >= self.max_identical_tool_errors:
            self.done = True
            self.termination_reason = TerminationReason.TOO_MANY_ERRORS

        if not tool_message.error:
            self._last_failed_tool_signature = None
            self._consecutive_identical_tool_errors = 0
            return

        self.num_errors += 1
        if signature == self._last_failed_tool_signature:
            self._consecutive_identical_tool_errors += 1
        else:
            self._last_failed_tool_signature = signature
            self._consecutive_identical_tool_errors = 1

        if self._consecutive_identical_tool_errors >= self.max_identical_tool_errors:
            self.done = True
            self.termination_reason = TerminationReason.TOO_MANY_ERRORS
```

Initialize the fields in `__init__`:

```python
        self._last_failed_tool_signature = None
        self._consecutive_identical_tool_errors = 0
        self._tool_outcome_counts = {}
        self.max_identical_tool_errors = 3
```

- [ ] **Step 4: Record each environment result**

Immediately after `environment.get_response(tool_call)` in the tool-call branch, call:

```python
                self._record_tool_outcome(tool_call, tool_msg)
```

The existing run-loop check continues to enforce cumulative `max_errors`; the helper additionally sets the same termination reason at three identical failures.

- [ ] **Step 5: Run the focused tests and verify GREEN**

Run the command from Step 2. Expected: `4 passed`.

- [ ] **Step 6: Commit the isolated orchestrator repair**

```bash
git add evaluation/vitabench/tests/test_orchestrator_tool_errors.py evaluation/vitabench/src/vita/orchestrator/orchestrator.py
git commit -m "fix: stop repeated VitaBench tool failures"
```

### Task 3: Regression verification and smoke evaluation

**Files:**
- Read: `agent/tests/test_adapt_memory.py`
- Read: `evaluation/vitabench/tests/test_orchestrator_tool_errors.py`
- Create at runtime: `evaluation/vitabench/data/simulations/qwen36_adapt/smoke_loop_guard.json`

- [ ] **Step 1: Run all ADAPT memory tests**

```bash
source /home/ai/anaconda3/etc/profile.d/conda.sh
conda activate lcpy311
cd /home/ai/student/lx/ADAPT
PYTHONPATH=/home/ai/student/lx/ADAPT pytest agent/tests/test_adapt_memory.py -q
```

Expected: all tests pass with no failures.

- [ ] **Step 2: Run the relevant VitaBench test suite**

```bash
source /home/ai/anaconda3/etc/profile.d/conda.sh
conda activate lcpy311
cd /home/ai/student/lx/ADAPT/evaluation/vitabench
PYTHONPATH=/home/ai/student/lx/ADAPT pytest tests/test_orchestrator_tool_errors.py tests/test_memory_read_query.py -q
```

Expected: all selected tests pass.

- [ ] **Step 3: Run a one-user smoke evaluation with the repaired runtime configuration**

Use the same injected local model definition as the full run, but set `max_tokens=1024`, add `truncate_prompt_tokens=120000` to `extra_body`, use `--num-tasks 1`, `--max-concurrency 1`, and save to `qwen36_adapt/smoke_loop_guard_v3.json`. Redirect stdout and stderr to `evaluation/vitabench/data/simulations/qwen36_adapt/smoke_loop_guard_v3.log`.

- [ ] **Step 4: Validate the smoke checkpoint**

```bash
jq -e '
  (.simulations | length) == 1 and
  .simulations[0].reward_info.info.num_subtasks ==
    .simulations[0].reward_info.info.num_evaluated
' evaluation/vitabench/data/simulations/qwen36_adapt/smoke_loop_guard_v3.json
```

Expected: exit code 0 and output `true`.

### Task 4: Resume the 56-user run from checkpoint

**Files:**
- Preserve: `evaluation/vitabench/data/simulations/qwen36_adapt/full_56_trial1.json`
- Create at runtime: `evaluation/vitabench/data/simulations/qwen36_adapt/full_56_trial1.resume.log`

- [ ] **Step 1: Reconfirm the checkpoint before resume**

```bash
jq -e '(.simulations | length) == 11' \
  evaluation/vitabench/data/simulations/qwen36_adapt/full_56_trial1.json
```

Expected: exit code 0 and output `true`.

- [ ] **Step 2: Resume with the same benchmark identity**

Run the original 56-task, one-trial command with `max_tokens=1024`, `truncate_prompt_tokens=120000`, `--max-concurrency 2`, and `--log-level WARNING`. Feed `y` to the built-in resume prompt, which must print `Resuming run from 11 runs. 45 runs remaining.` Redirect all output to `full_56_trial1.resume.log`.

- [ ] **Step 3: Verify the resumed process and progress**

Confirm that the log contains the resume message, the result JSON still contains 11 simulations before the first new completion, and all four GPUs show active vLLM workers. Keep the model server alive and report the log and checkpoint paths to the user.
