"""The memory core must not import the host framework.

``agent.memory.adapt_memory`` is the framework-free half of the preference
store: a deployment that only wants the data layer must be able to import it
without VitaBench on the path. ``agent/adapters/vitabench_memory.py`` is the
only module allowed to know about ``vita``.

The closure assertion runs in a **subprocess**, not in-process: the test session
has already imported ``vita`` for other tests, so only a fresh interpreter can
prove what the import actually pulls in. Same technique as
``test_data_layer_import_closure_excludes_retired_controller_code``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_PROBE = (
    "import sys, agent.memory.adapt_memory; "
    "print(sorted({n.split('.')[0] for n in sys.modules if n.startswith('vita')}))"
)


def test_memory_core_imports_without_pulling_in_vita():
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]", (
        "agent.memory.adapt_memory pulled in the host framework: "
        f"{result.stdout.strip()}"
    )


def test_core_class_carries_no_harness_surface():
    """``get_tools`` / the @is_tool registry belong to the adapter, not the core.

    If this ever starts passing with ``hasattr`` true, the adapter has leaked
    back into the core and the two can no longer be deployed separately.
    """
    from agent.memory.adapt_memory import ADAPTMemory

    assert not hasattr(ADAPTMemory, "get_tools")
    assert not hasattr(ADAPTMemory, "tools")


def test_adapter_restores_exactly_the_four_tools():
    """The harness-facing tool surface, by name.

    ``query_preference_memory`` is the E-042 tool: stock called it 30 times in
    one cohort while the hidden-tool arm called it 0. Losing it silently is the
    regression this asserts against.
    """
    import agent.vitabench_bootstrap as bootstrap

    bootstrap.enable_vitabench_utf8()
    from agent.adapters.vitabench_memory import VitaBenchADAPTMemory

    memory = VitaBenchADAPTMemory(language="chinese", user_id="probe")
    assert set(memory.get_tools()) == {
        "suggest_question_tool",
        "query_preference_memory",
        "read_preference_memory",
        "record_preference_answer",
    }


def test_adapter_tool_descriptions_match_the_core_docstrings():
    """Tool descriptions are built from docstrings, so a reworded copy changes
    the text the model sees. The adapter must stay byte-identical."""
    import agent.vitabench_bootstrap as bootstrap

    bootstrap.enable_vitabench_utf8()
    from agent.adapters.vitabench_memory import VitaBenchADAPTMemory
    from agent.memory.adapt_memory import ADAPTMemory

    for name in (
        "suggest_question_tool",
        "query_preference_memory",
        "read_preference_memory",
        "record_preference_answer",
    ):
        assert (getattr(ADAPTMemory, name).__doc__ or "").strip() == (
            getattr(VitaBenchADAPTMemory, name).__doc__ or ""
        ).strip(), f"tool description drifted for {name}"