# ADAPT Agent Harness Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a score-oriented `ADAPTAgent` that stays entirely behind VitaBench's `BaseAgent` interface, tracks entity IDs exposed through public messages, validates generated tool calls against the current tool schemas, and internally regenerates invalid tool-call drafts before they reach the frozen benchmark environment.

**Architecture:** `ADAPTAgent` subclasses VitaBench's `PersonalizationAgent` and overrides only Agent-side hooks: `get_init_state()`, `update_tools()`, and `generate_next_message()`. A focused `ToolGuard` owns the dynamic tool index and public-information entity ledger. A project-level launcher verifies an untouched official VitaBench checkout, registers the custom Agent for metadata, injects it at the existing personalization construction seam without editing VitaBench files, and then delegates to the official CLI.

**Tech Stack:** Python 3.11, Pydantic message/state models, VitaBench `PersonalizationAgent`/`Tool` interfaces, pytest, standard-library `argparse`, `importlib`, `json`, `re`, `subprocess`.

**Version-control note:** Commit steps are intentionally omitted because the user explicitly asked to defer commits and the outer ADAPT repository has no established commit history. Each task ends with a test checkpoint instead.

---

## Scope and File Map

- Create `agent/harness/__init__.py`: public exports for the Phase 1 harness.
- Create `agent/harness/tool_guard.py`: dynamic tool lookup, schema validation, and known-entity tracking from public messages only.
- Create `agent/adapt_agent.py`: Agent-side state and internal invalid-draft regeneration loop.
- Modify `agent/__init__.py`: export `ADAPTAgent` without performing registry side effects.
- Create `evaluation/run_adapt.py`: frozen-checkout verifier and CLI injection entrypoint.
- Create `evaluation/config/qwen36_vita.yaml`: one explicit, shared Qwen3.6 configuration for Agent, User, and Evaluator.
- Create `agent/tests/test_tool_guard.py`: unit coverage for schema checks, ID provenance, and error-message isolation.
- Create `agent/tests/test_adapt_agent.py`: unit coverage for dynamic tools, regeneration, state history, and usage accounting.
- Create `agent/tests/test_run_adapt.py`: unit coverage for frozen runtime verification and Agent injection.
- Do not modify any file under `evaluation/vitabench/src`, `evaluation/vitabench/tests`, or its benchmark data.

The phase deliberately excludes Reflection, Context Paging, learned planning, and benchmark execution. Those become separate plans after this Harness passes offline tests and a one-user frozen-runtime smoke test.

---

### Task 1: Public-Information Entity Ledger

**Files:**
- Create: `agent/harness/__init__.py`
- Create: `agent/harness/tool_guard.py`
- Test: `agent/tests/test_tool_guard.py`

- [ ] **Step 1: Write failing tests for public entity extraction**

Add tests that prove successful public tool output and user text can establish ID provenance, while failed tool output cannot:

```python
import json

from vita.data_model.message import ToolMessage, UserMessage

from agent.harness.tool_guard import EntityLedger


def test_ledger_learns_ids_from_successful_tool_json():
    ledger = EntityLedger()
    ledger.observe(
        ToolMessage(
            id="call-1",
            name="create_order",
            role="tool",
            content=json.dumps({"order_id": "ORD-12345", "product_id": "SKU-77"}),
            requestor="assistant",
            error=False,
        )
    )

    assert ledger.knows("ORD-12345")
    assert ledger.knows("SKU-77")


def test_ledger_ignores_ids_echoed_by_failed_tool_output():
    ledger = EntityLedger()
    ledger.observe(
        ToolMessage(
            id="call-2",
            name="pay_order",
            role="tool",
            content="Order ORD-FAKE-99 not found",
            requestor="assistant",
            error=True,
        )
    )

    assert not ledger.knows("ORD-FAKE-99")


def test_ledger_accepts_id_explicitly_supplied_by_user():
    ledger = EntityLedger()
    ledger.observe(
        UserMessage(role="user", content="请支付订单 ORD-USER-88")
    )

    assert ledger.knows("ORD-USER-88")
```

- [ ] **Step 2: Run the tests and verify the missing module failure**

Run:

```bash
DEEPSEEK_API_KEY=test-only \
PYTHONPATH=/home/ai/student/lx/ADAPT:/home/ai/student/lx/ADAPT/evaluation/vitabench/src \
/home/ai/student/dataset2/conda_envs/lcpy311/bin/python -m pytest \
  agent/tests/test_tool_guard.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'agent.harness'`.

- [ ] **Step 3: Implement the minimal entity ledger**

Create `agent/harness/tool_guard.py` with a conservative extractor. It may parse JSON recursively and accept ID-shaped tokens from public user text, but it must not inspect environment objects, DB instances, task rubrics, or evaluator state.

```python
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from vita.data_model.message import MultiToolMessage, ToolMessage, UserMessage


_ID_TOKEN = re.compile(r"\b(?=[A-Za-z0-9#_-]{5,}\b)(?=[A-Za-z0-9#_-]*\d)[A-Za-z][A-Za-z0-9#_-]*\b")


def _is_id_field(name: str | None) -> bool:
    return bool(name) and (name == "id" or name.endswith("_id") or name.endswith("_ids"))


def _collect_structured_ids(value: Any, field_name: str | None = None) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            found.update(_collect_structured_ids(child, str(key)))
    elif isinstance(value, list):
        for child in value:
            found.update(_collect_structured_ids(child, field_name))
    elif isinstance(value, str) and _is_id_field(field_name):
        found.add(value)
    return found


@dataclass
class EntityLedger:
    known_ids: set[str] = field(default_factory=set)

    def observe(self, message: object) -> None:
        if isinstance(message, MultiToolMessage):
            for tool_message in message.tool_messages:
                self.observe(tool_message)
            return
        if isinstance(message, ToolMessage):
            if message.error or not message.content:
                return
            try:
                payload = json.loads(message.content)
            except (TypeError, json.JSONDecodeError):
                payload = None
            if payload is not None:
                self.known_ids.update(_collect_structured_ids(payload))
            self.known_ids.update(_ID_TOKEN.findall(message.content))
            return
        if isinstance(message, UserMessage) and message.content:
            self.known_ids.update(_ID_TOKEN.findall(message.content))

    def knows(self, value: object) -> bool:
        return isinstance(value, str) and value in self.known_ids
```

Create `agent/harness/__init__.py` for the Task 1 surface:

```python
from agent.harness.tool_guard import EntityLedger

__all__ = ["EntityLedger"]
```

`ToolGuard` and `ValidationIssue` are added in Task 2. Update this file then to import and export all three public types.

- [ ] **Step 4: Run the focused ledger tests**

Run the command from Step 2.

Expected: `3 passed`.

---

### Task 2: Dynamic Tool Schema and ID-Provenance Validation

**Files:**
- Modify: `agent/harness/tool_guard.py`
- Modify: `agent/harness/__init__.py`
- Modify: `agent/tests/test_tool_guard.py`

- [ ] **Step 1: Add failing validator tests**

Use small real VitaBench `Tool` objects so validation exercises the same Pydantic parameter models as the benchmark:

```python
from vita.data_model.message import ToolCall
from vita.environment.tool import as_tool

from agent.harness.tool_guard import EntityLedger, ToolGuard


def search_products(keyword: str) -> list[dict]:
    """Search products."""
    return []


def pay_order(order_id: str) -> str:
    """Pay an existing order."""
    return "done"


def test_guard_rejects_unknown_tool_and_schema_error():
    guard = ToolGuard([as_tool(search_products)])

    unknown = guard.validate(ToolCall(name="missing_tool", arguments={}), EntityLedger())
    wrong_type = guard.validate(
        ToolCall(name="search_products", arguments={"keyword": ["not", "a", "string"]}),
        EntityLedger(),
    )

    assert unknown[0].code == "unknown_tool"
    assert wrong_type[0].code == "invalid_arguments"


def test_guard_rejects_unseen_reference_id_but_accepts_public_id():
    guard = ToolGuard([as_tool(pay_order)])
    ledger = EntityLedger()
    call = ToolCall(name="pay_order", arguments={"order_id": "ORD-12345"})

    assert guard.validate(call, ledger)[0].code == "unknown_entity_id"

    ledger.known_ids.add("ORD-12345")
    assert guard.validate(call, ledger) == []


def test_guard_does_not_require_user_id_provenance():
    def read_profile(user_id: str) -> dict:
        """Read the current user's public profile."""
        return {}

    guard = ToolGuard([as_tool(read_profile)])
    call = ToolCall(name="read_profile", arguments={"user_id": "USER-1"})

    assert guard.validate(call, EntityLedger()) == []
```

- [ ] **Step 2: Run validator tests and verify they fail**

Run:

```bash
DEEPSEEK_API_KEY=test-only \
PYTHONPATH=/home/ai/student/lx/ADAPT:/home/ai/student/lx/ADAPT/evaluation/vitabench/src \
/home/ai/student/dataset2/conda_envs/lcpy311/bin/python -m pytest \
  agent/tests/test_tool_guard.py -q
```

Expected: import or attribute failures for `ToolGuard` and `ValidationIssue`.

- [ ] **Step 3: Implement schema and provenance validation**

Append to `agent/harness/tool_guard.py`:

```python
from vita.data_model.message import ToolCall
from vita.environment.tool import Tool


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str


def _iter_id_arguments(value: Any, field_name: str | None = None):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _iter_id_arguments(child, str(key))
    elif isinstance(value, list):
        for child in value:
            yield from _iter_id_arguments(child, field_name)
    elif isinstance(value, str) and _is_id_field(field_name) and field_name != "user_id":
        yield field_name, value


class ToolGuard:
    def __init__(self, tools: list[Tool] | None = None):
        self.update_tools(tools or [])

    def update_tools(self, tools: list[Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def validate(self, call: ToolCall, ledger: EntityLedger) -> list[ValidationIssue]:
        tool = self._tools.get(call.name)
        if tool is None:
            return [ValidationIssue("unknown_tool", f"工具 {call.name} 不在当前领域工具列表中")]

        try:
            tool.params.model_validate(call.arguments)
        except Exception as exc:
            return [ValidationIssue("invalid_arguments", f"工具 {call.name} 参数不符合 schema: {exc}")]

        issues = []
        for field_name, value in _iter_id_arguments(call.arguments):
            if not ledger.knows(value):
                issues.append(
                    ValidationIssue(
                        "unknown_entity_id",
                        f"{field_name}={value!r} 未出现在用户消息或成功工具返回中",
                    )
                )
        return issues
```

Update `agent/harness/__init__.py` to export all three public types.

- [ ] **Step 4: Run all ToolGuard tests**

Run the command from Step 2.

Expected: `6 passed`.

---

### Task 3: ADAPTAgent State and Invalid-Draft Regeneration

**Files:**
- Create: `agent/adapt_agent.py`
- Modify: `agent/__init__.py`
- Create: `agent/tests/test_adapt_agent.py`

- [ ] **Step 1: Write failing tests for the Agent Harness**

The tests monkeypatch the imported `generate` function, never call a live model, and verify that an invalid tool draft is not exposed to VitaBench:

```python
import json

import pytest

from vita.data_model.message import AssistantMessage, ToolCall, ToolMessage, UserMessage
from vita.environment.tool import as_tool
from vita.memory.null_memory import NullMemory

import agent.adapt_agent as adapt_agent_module
from agent.adapt_agent import ADAPTAgent


def pay_order(order_id: str) -> str:
    """Pay an existing order."""
    return "done"


def make_agent():
    return ADAPTAgent(
        tools=[as_tool(pay_order)],
        domain_policy="time={time}",
        memory=NullMemory(language="chinese"),
        llm="fake-model",
        llm_args={},
        time="2026-08-06 10:00:00",
        language="chinese",
    )


def test_invalid_tool_draft_is_regenerated_before_return(monkeypatch):
    drafts = iter([
        AssistantMessage(
            role="assistant",
            tool_calls=[ToolCall(id="bad", name="pay_order", arguments={"order_id": "ORD-FAKE"})],
            usage={"completion_tokens": 10, "total_tokens": 30},
            cost=0.1,
        ),
        AssistantMessage(role="assistant", content="请提供需要支付的订单号。", usage={"completion_tokens": 8, "total_tokens": 20}, cost=0.05),
    ])
    monkeypatch.setattr(adapt_agent_module, "generate", lambda **_: next(drafts))
    agent = make_agent()
    state = agent.get_init_state()

    message, state = agent.generate_next_message(UserMessage(role="user", content="帮我支付订单"), state)

    assert message.content == "请提供需要支付的订单号。"
    assert all(not getattr(item, "tool_calls", None) for item in state.messages)
    assert state.rejected_tool_drafts == 1
    assert message.cost == pytest.approx(0.15)
    assert message.usage["completion_tokens"] == 18
    assert message.usage["total_tokens"] == 50


def test_public_tool_result_allows_follow_up_id(monkeypatch):
    draft = AssistantMessage(
        role="assistant",
        tool_calls=[ToolCall(id="pay", name="pay_order", arguments={"order_id": "ORD-12345"})],
    )
    monkeypatch.setattr(adapt_agent_module, "generate", lambda **_: draft)
    agent = make_agent()
    state = agent.get_init_state()

    inbound = ToolMessage(
        id="create",
        name="create_order",
        role="tool",
        content=json.dumps({"order_id": "ORD-12345"}),
        requestor="assistant",
        error=False,
    )
    message, state = agent.generate_next_message(inbound, state)

    assert message.tool_calls[0].arguments["order_id"] == "ORD-12345"
    assert state.rejected_tool_drafts == 0


def test_update_tools_refreshes_guard(monkeypatch):
    def search(keyword: str) -> list:
        """Search for products."""
        return []

    agent = make_agent()
    agent.update_tools([as_tool(search)])
    state = agent.get_init_state()
    draft = AssistantMessage(
        role="assistant",
        tool_calls=[ToolCall(id="search", name="search", arguments={"keyword": "低糖饮料"})],
    )
    monkeypatch.setattr(adapt_agent_module, "generate", lambda **_: draft)

    message, _ = agent.generate_next_message(UserMessage(role="user", content="找低糖饮料"), state)

    assert message.tool_calls[0].name == "search"
```

- [ ] **Step 2: Run the Agent tests and verify the missing class failure**

Run:

```bash
DEEPSEEK_API_KEY=test-only \
PYTHONPATH=/home/ai/student/lx/ADAPT:/home/ai/student/lx/ADAPT/evaluation/vitabench/src \
/home/ai/student/dataset2/conda_envs/lcpy311/bin/python -m pytest \
  agent/tests/test_adapt_agent.py -q
```

Expected: collection fails because `agent.adapt_agent` does not exist.

- [ ] **Step 3: Implement ADAPTAgent with bounded internal regeneration**

Create `agent/adapt_agent.py`. The implementation must append each real inbound message once, keep rejected drafts out of the official trajectory, and aggregate rejected-draft usage into the returned message:

```python
from __future__ import annotations

from typing import Optional

from loguru import logger
from pydantic import Field
from vita.agent.llm_agent import LLMAgentState
from vita.agent.personalization_agent import PersonalizationAgent
from vita.data_model.message import (
    AssistantMessage,
    Message,
    MultiToolMessage,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from vita.utils.llm_utils import generate

from agent.harness.tool_guard import EntityLedger, ToolGuard, ValidationIssue


class ADAPTAgentState(LLMAgentState):
    known_entity_ids: set[str] = Field(default_factory=set)
    rejected_tool_drafts: int = 0


def _add_usage(total: dict, usage: Optional[dict]) -> dict:
    merged = dict(total)
    for key, value in (usage or {}).items():
        if isinstance(value, (int, float)):
            merged[key] = merged.get(key, 0) + value
    return merged


class ADAPTAgent(PersonalizationAgent):
    def __init__(self, *args, max_internal_regenerations: int = 2, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_internal_regenerations = max_internal_regenerations
        self._tool_guard = ToolGuard(self.tools)

    def update_tools(self, tools):
        super().update_tools(tools)
        self._tool_guard.update_tools(tools)

    def get_init_state(self, message_history: Optional[list[Message]] = None) -> ADAPTAgentState:
        base = super().get_init_state(message_history=message_history)
        ledger = EntityLedger()
        for message in base.messages:
            ledger.observe(message)
        return ADAPTAgentState(
            system_messages=base.system_messages,
            messages=base.messages,
            known_entity_ids=set(ledger.known_ids),
        )

    def _issues(self, message: AssistantMessage, ledger: EntityLedger) -> list[ValidationIssue]:
        issues = []
        for call in message.tool_calls or []:
            issues.extend(self._tool_guard.validate(call, ledger))
        return issues

    def generate_next_message(self, message, state: ADAPTAgentState):
        inbound = message.tool_messages if isinstance(message, MultiToolMessage) else [message]
        state.messages.extend(inbound)

        ledger = EntityLedger(set(state.known_entity_ids))
        for item in inbound:
            ledger.observe(item)
        state.known_entity_ids = set(ledger.known_ids)

        rejected_usage = {}
        rejected_cost = 0.0
        correction = None
        for _ in range(self.max_internal_regenerations + 1):
            internal_system = list(state.system_messages)
            if correction:
                internal_system.append(SystemMessage(role="system", content=correction))
            draft = generate(
                model=self.llm,
                tools=self.tools,
                messages=internal_system + state.messages,
                enable_think=self.enable_think,
                **self.llm_args,
            )
            issues = self._issues(draft, ledger)
            if not issues:
                draft.cost = (draft.cost or 0.0) + rejected_cost
                draft.usage = _add_usage(rejected_usage, draft.usage)
                state.messages.append(draft)
                return draft, state

            state.rejected_tool_drafts += 1
            rejected_cost += draft.cost or 0.0
            rejected_usage = _add_usage(rejected_usage, draft.usage)
            logger.warning(
                "ADAPT_TOOL_DRAFT_REJECTED attempt={} issues={}",
                state.rejected_tool_drafts,
                [issue.code for issue in issues],
            )
            correction = (
                "上一份工具调用草案未发送，因为它不符合当前公开工具信息：\n- "
                + "\n- ".join(issue.message for issue in issues)
                + "\n请重新规划。只能使用当前工具 schema，以及用户消息或成功工具返回中已经出现的实体 ID；"
                  "如果信息不足，请向用户提出一个具体问题，不要猜测 ID。"
            )

        fallback = AssistantMessage(
            role="assistant",
            content="我还缺少执行该操作所需的有效信息，请提供相关订单或商品记录。",
            cost=rejected_cost,
            usage=rejected_usage or None,
        )
        state.messages.append(fallback)
        return fallback, state
```

Add a test that creates two states and proves they do not share `known_entity_ids`.

Update `agent/__init__.py`:

```python
from agent.adapt_agent import ADAPTAgent, ADAPTAgentState

__all__ = ["ADAPTAgent", "ADAPTAgentState"]
```

- [ ] **Step 4: Run the Agent tests**

Run the command from Step 2.

Expected: all tests pass.

- [ ] **Step 5: Run existing ADAPT memory tests for regression coverage**

Run:

```bash
DEEPSEEK_API_KEY=test-only \
PYTHONPATH=/home/ai/student/lx/ADAPT:/home/ai/student/lx/ADAPT/evaluation/vitabench/src \
/home/ai/student/dataset2/conda_envs/lcpy311/bin/python -m pytest \
  agent/tests/test_adapt_memory.py agent/tests/test_tool_guard.py agent/tests/test_adapt_agent.py -q
```

Expected: all existing memory tests and all new Harness tests pass.

---

### Task 4: Frozen VitaBench Launcher and Agent Injection

**Files:**
- Create: `evaluation/run_adapt.py`
- Create: `evaluation/config/qwen36_vita.yaml`
- Create: `agent/tests/test_run_adapt.py`

- [ ] **Step 1: Write failing tests for the frozen-runtime verifier**

The verifier must reject a checkout at the wrong commit or with tracked changes under `src/vita`, but ignore untracked datasets and result files:

```python
from pathlib import Path
from types import SimpleNamespace

import pytest

import evaluation.run_adapt as run_adapt


def test_verify_frozen_vita_accepts_expected_clean_source(monkeypatch, tmp_path):
    responses = iter(["official-commit\n", ""])
    monkeypatch.setattr(run_adapt.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=next(responses)))

    run_adapt.verify_frozen_vita(tmp_path, "official-commit")


def test_verify_frozen_vita_rejects_wrong_commit(monkeypatch, tmp_path):
    monkeypatch.setattr(
        run_adapt.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="different-commit\n"),
    )

    with pytest.raises(RuntimeError, match="expected VitaBench commit"):
        run_adapt.verify_frozen_vita(tmp_path, "official-commit")


def test_verify_frozen_vita_rejects_tracked_source_changes(monkeypatch, tmp_path):
    responses = iter(["official-commit\n", "src/vita/run.py\n"])
    monkeypatch.setattr(run_adapt.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=next(responses)))

    with pytest.raises(RuntimeError, match="tracked VitaBench source changes"):
        run_adapt.verify_frozen_vita(tmp_path, "official-commit")
```

- [ ] **Step 2: Run launcher tests and verify the missing module failure**

Run:

```bash
DEEPSEEK_API_KEY=test-only \
PYTHONPATH=/home/ai/student/lx/ADAPT:/home/ai/student/lx/ADAPT/evaluation/vitabench/src \
/home/ai/student/dataset2/conda_envs/lcpy311/bin/python -m pytest \
  agent/tests/test_run_adapt.py -q
```

Expected: collection fails because `evaluation.run_adapt` does not exist.

- [ ] **Step 3: Implement the frozen launcher**

Create `evaluation/run_adapt.py`:

```python
from __future__ import annotations

import argparse
import importlib
import subprocess
import sys
from pathlib import Path


DEFAULT_OFFICIAL_COMMIT = "f60169e89f30499cb7883f3dad76bd03facc908d"


def _git(vita_root: Path, *args: str):
    return subprocess.run(
        ["git", "-C", str(vita_root), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def verify_frozen_vita(vita_root: Path, expected_commit: str) -> None:
    head = _git(vita_root, "rev-parse", "HEAD")
    if head.returncode != 0 or head.stdout.strip() != expected_commit:
        raise RuntimeError(
            f"expected VitaBench commit {expected_commit}, got {head.stdout.strip() or 'unavailable'}"
        )
    changed = _git(vita_root, "diff", "--name-only", "HEAD", "--", "src/vita")
    if changed.returncode != 0:
        raise RuntimeError("could not verify frozen VitaBench source")
    if changed.stdout.strip():
        raise RuntimeError(f"tracked VitaBench source changes detected:\n{changed.stdout.strip()}")


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--vita-root", type=Path, required=True)
    parser.add_argument("--expected-vita-commit", default=DEFAULT_OFFICIAL_COMMIT)
    known, vita_args = parser.parse_known_args(argv)
    verify_frozen_vita(known.vita_root, known.expected_vita_commit)

    sys.path.insert(0, str(known.vita_root / "src"))
    vita_run = importlib.import_module("vita.run")
    vita_registry = importlib.import_module("vita.registry").registry
    from agent.adapt_agent import ADAPTAgent

    if "adapt_agent" not in vita_registry.get_agents():
        vita_registry.register_agent(ADAPTAgent, "adapt_agent")
    vita_run.PersonalizationAgent = ADAPTAgent

    vita_cli = importlib.import_module("vita.cli")
    original_argv = sys.argv
    try:
        sys.argv = ["vita", *vita_args]
        return vita_cli.main()
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    main()
```

The injection changes only the Agent class binding in the current process. It must not write to the VitaBench checkout. The official CLI still owns task loading, concurrency, orchestration, environment calls, scoring, checkpointing, and result serialization.

Create `evaluation/config/qwen36_vita.yaml` so all three benchmark roles resolve the same explicit generation settings through VitaBench's supported `VITA_MODEL_CONFIG_PATH` environment variable:

```yaml
default:
  base_url: http://127.0.0.1:8000/v1
  api_key: local
  temperature: 0.0
  max_tokens: 4096
  extra_body:
    chat_template_kwargs:
      enable_thinking: false
  cost_1m_token_dollar:
    prompt_price: 0.0
    completion_price: 0.0

models:
  - name: qwen3.6-35b-a3b
```

- [ ] **Step 4: Add an injection test without running a benchmark**

Extend `agent/tests/test_run_adapt.py` with fake import modules; do not import or modify the real global VitaBench registry:

```python
import sys
from types import SimpleNamespace

from agent.adapt_agent import ADAPTAgent


class FakeRegistry:
    def __init__(self):
        self.agents = {}

    def get_agents(self):
        return list(self.agents)

    def register_agent(self, cls, name):
        self.agents[name] = cls


def test_main_injects_agent_and_forwards_official_cli_args(monkeypatch, tmp_path):
    registry = FakeRegistry()
    fake_run = SimpleNamespace(PersonalizationAgent=None)
    observed = {}

    def fake_cli_main():
        observed["argv"] = list(sys.argv)

    modules = {
        "vita.run": fake_run,
        "vita.registry": SimpleNamespace(registry=registry),
        "vita.cli": SimpleNamespace(main=fake_cli_main),
    }
    monkeypatch.setattr(run_adapt, "verify_frozen_vita", lambda *_: None)
    monkeypatch.setattr(run_adapt.importlib, "import_module", lambda name: modules[name])

    run_adapt.main([
        "--vita-root", str(tmp_path),
        "run", "--domain", "personalization", "--agent", "adapt_agent",
    ])

    assert registry.agents["adapt_agent"] is ADAPTAgent
    assert fake_run.PersonalizationAgent is ADAPTAgent
    assert observed["argv"][1:] == [
        "run", "--domain", "personalization", "--agent", "adapt_agent",
    ]


def test_main_restores_argv_when_official_cli_raises(monkeypatch, tmp_path):
    registry = FakeRegistry()
    modules = {
        "vita.run": SimpleNamespace(PersonalizationAgent=None),
        "vita.registry": SimpleNamespace(registry=registry),
        "vita.cli": SimpleNamespace(main=lambda: (_ for _ in ()).throw(RuntimeError("boom"))),
    }
    monkeypatch.setattr(run_adapt, "verify_frozen_vita", lambda *_: None)
    monkeypatch.setattr(run_adapt.importlib, "import_module", lambda name: modules[name])
    original = list(sys.argv)

    with pytest.raises(RuntimeError, match="boom"):
        run_adapt.main(["--vita-root", str(tmp_path), "run", "--help"])

    assert sys.argv == original
```

- [ ] **Step 5: Run launcher and full offline Harness tests**

Run:

```bash
DEEPSEEK_API_KEY=test-only \
PYTHONPATH=/home/ai/student/lx/ADAPT:/home/ai/student/lx/ADAPT/evaluation/vitabench/src \
/home/ai/student/dataset2/conda_envs/lcpy311/bin/python -m pytest \
  agent/tests/test_tool_guard.py \
  agent/tests/test_adapt_agent.py \
  agent/tests/test_run_adapt.py \
  agent/tests/test_adapt_memory.py -q
```

Expected: all tests pass without network or model-server access.

---

### Task 5: Frozen-Checkout Smoke Gate

**Files:**
- Preserve: every file under `evaluation/vitabench/src`
- Runtime output only: a new smoke JSON and log under the frozen checkout's `data/simulations/adapt_agent/`

- [ ] **Step 1: Create an isolated official VitaBench worktree**

At execution time, invoke `superpowers:using-git-worktrees` first. Create `/home/ai/student/lx/ADAPT/.worktrees/vitabench-f60169e` from exact official commit `f60169e89f30499cb7883f3dad76bd03facc908d`, outside the dirty nested checkout. Copy only the required dataset directory and the local untracked model configuration; do not copy modified Python files:

```bash
git -C /home/ai/student/lx/ADAPT/evaluation/vitabench worktree add \
  /home/ai/student/lx/ADAPT/.worktrees/vitabench-f60169e \
  f60169e89f30499cb7883f3dad76bd03facc908d
mkdir -p /home/ai/student/lx/ADAPT/.worktrees/vitabench-f60169e/data
cp -a /home/ai/student/lx/ADAPT/evaluation/vitabench/data/vita \
  /home/ai/student/lx/ADAPT/.worktrees/vitabench-f60169e/data/
```

Expected invariant:

```bash
git -C /home/ai/student/lx/ADAPT/.worktrees/vitabench-f60169e rev-parse HEAD
git -C /home/ai/student/lx/ADAPT/.worktrees/vitabench-f60169e status --short -- src/vita
```

Expected output: the exact official commit on the first command and no output on the second.

- [ ] **Step 2: Run a no-network construction probe**

Use the launcher with `--help` first:

```bash
DEEPSEEK_API_KEY=test-only \
VITA_MODEL_CONFIG_PATH=/home/ai/student/lx/ADAPT/evaluation/config/qwen36_vita.yaml \
PYTHONPATH=/home/ai/student/lx/ADAPT \
/home/ai/student/dataset2/conda_envs/lcpy311/bin/python evaluation/run_adapt.py \
  --vita-root /home/ai/student/lx/ADAPT/.worktrees/vitabench-f60169e \
  run --help
```

Expected: official Vita CLI help exits successfully, lists the personalization and memory arguments, and does not report a frozen-source violation.

- [ ] **Step 3: Run one fixed user with the existing local Qwen service**

Run one previously diagnosed user, one rollout, official max steps, and a fresh output filename. Use the same Qwen3.6 model definition for Agent, User, and Evaluator; do not lower `max_tokens` mid-run and do not resume an existing result file.

Use `evaluation/config/qwen36_vita.yaml` for Agent, User, and Evaluator so all three roles have identical explicit settings. Keep `max_tokens=4096`, disable thinking, and do not add `truncate_prompt_tokens`. Create the log directory before `tee` opens the output file:

```bash
mkdir -p /home/ai/student/lx/ADAPT/.worktrees/vitabench-f60169e/data/simulations/adapt_agent
DEEPSEEK_API_KEY=test-only \
VITA_MODEL_CONFIG_PATH=/home/ai/student/lx/ADAPT/evaluation/config/qwen36_vita.yaml \
PYTHONPATH=/home/ai/student/lx/ADAPT \
/home/ai/student/dataset2/conda_envs/lcpy311/bin/python evaluation/run_adapt.py \
  --vita-root /home/ai/student/lx/ADAPT/.worktrees/vitabench-f60169e \
  run \
  --domain personalization \
  --agent adapt_agent \
  --memory-class agent.memory.ADAPTMemory \
  --agent-llm qwen3.6-35b-a3b \
  --user-llm qwen3.6-35b-a3b \
  --evaluator-llm qwen3.6-35b-a3b \
  --task-ids U642088 \
  --num-trials 1 \
  --max-steps 50 \
  --max-errors 10 \
  --max-concurrency 1 \
  --save-to adapt_agent/phase1_smoke.json \
  --language chinese \
  --log-level INFO \
  2>&1 | tee /home/ai/student/lx/ADAPT/.worktrees/vitabench-f60169e/data/simulations/adapt_agent/phase1_smoke.log
```

Expected: one simulation is written, all of its subtasks are evaluated, and result metadata identifies `adapt_agent` while the benchmark commit remains the official frozen commit.

- [ ] **Step 4: Verify no benchmark source mutation and summarize Harness diagnostics**

Run:

```bash
git -C /home/ai/student/lx/ADAPT/.worktrees/vitabench-f60169e status --short -- src/vita
jq '{simulations: (.simulations|length), reward: .simulations[0].reward_info.reward, termination: .simulations[0].termination_reason, subtasks: .simulations[0].reward_info.info.num_subtasks, evaluated: .simulations[0].reward_info.info.num_evaluated}' /home/ai/student/lx/ADAPT/.worktrees/vitabench-f60169e/data/simulations/adapt_agent/phase1_smoke.json
```

Expected: no source changes; one complete simulation; `subtasks == evaluated`.

- [ ] **Step 5: Stop and review before Context Paging**

Report:

- rejected invalid tool drafts;
- live environment tool errors;
- max-step subtasks;
- score and proactive-subtask score;
- total Agent token usage, including internal rejected drafts;
- any false-positive ToolGuard rejection.

Count rejected drafts from `ADAPT_TOOL_DRAFT_REJECTED` log records. Because rejected-draft token usage is merged into the returned `AssistantMessage`, the official result's Agent usage/cost aggregation must include those internal attempts.

Do not begin Context Paging until this evidence shows the Harness does not block legitimate tool calls and does not reduce the smoke user's completed-subtask count.

---

## Plan Self-Review

- **Spec coverage:** The plan implements only the first approved delivery slice: Agent Harness, tool validation, entity provenance, bounded recovery, frozen-runtime injection, and smoke verification. Context Paging, Reflection, and full Tool Registry remain intentionally out of scope.
- **Boundary coverage:** No task edits VitaBench source, environment, tools, user simulator, orchestrator, evaluator, reward, or task data. The launcher verifies the frozen commit and tracked source before every run.
- **Type consistency:** `ADAPTAgentState` extends `LLMAgentState`; `ADAPTAgent` preserves the `PersonalizationAgent` constructor and public methods expected by the official personalization runner; `ToolGuard` consumes the real `Tool` and `ToolCall` types.
- **Failure behavior:** Invalid drafts are internal and bounded. Failed environment results never establish ID provenance. Exhausted retries produce a safe user-facing clarification rather than fabricating an ID or altering benchmark termination.
- **No placeholders:** The frozen worktree path, created code, tests, commands, and expected outcomes are explicit.
