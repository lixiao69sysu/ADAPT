import sys
from types import SimpleNamespace

import pytest

from agent.adapt_agent import ADAPTAgent
import evaluation.run_adapt as run_adapt


class FakeRegistry:
    def __init__(self):
        self.agents = {}

    def get_agents(self):
        return list(self.agents)

    def register_agent(self, cls, name):
        self.agents[name] = cls


def test_verify_frozen_vita_accepts_expected_clean_source(monkeypatch, tmp_path):
    responses = iter(["official-commit\n", ""])
    monkeypatch.setattr(
        run_adapt.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=next(responses),
        ),
    )

    run_adapt.verify_frozen_vita(tmp_path, "official-commit")


def test_verify_frozen_vita_rejects_wrong_commit(monkeypatch, tmp_path):
    monkeypatch.setattr(
        run_adapt.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="different-commit\n",
        ),
    )

    with pytest.raises(RuntimeError, match="expected VitaBench commit"):
        run_adapt.verify_frozen_vita(tmp_path, "official-commit")


def test_verify_frozen_vita_rejects_tracked_source_changes(monkeypatch, tmp_path):
    responses = iter(["official-commit\n", "src/vita/run.py\n"])
    monkeypatch.setattr(
        run_adapt.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=next(responses),
        ),
    )

    with pytest.raises(RuntimeError, match="tracked VitaBench source changes"):
        run_adapt.verify_frozen_vita(tmp_path, "official-commit")


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
    monkeypatch.setattr(
        run_adapt.importlib,
        "import_module",
        lambda name: modules[name],
    )

    run_adapt.main(
        [
            "--vita-root",
            str(tmp_path),
            "run",
            "--domain",
            "personalization",
            "--agent",
            "adapt_agent",
        ]
    )

    assert registry.agents["adapt_agent"] is ADAPTAgent
    assert fake_run.PersonalizationAgent is ADAPTAgent
    assert observed["argv"][1:] == [
        "run",
        "--domain",
        "personalization",
        "--agent",
        "adapt_agent",
    ]


def test_main_restores_argv_when_official_cli_raises(monkeypatch, tmp_path):
    registry = FakeRegistry()
    modules = {
        "vita.run": SimpleNamespace(PersonalizationAgent=None),
        "vita.registry": SimpleNamespace(registry=registry),
        "vita.cli": SimpleNamespace(
            main=lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        ),
    }
    monkeypatch.setattr(run_adapt, "verify_frozen_vita", lambda *_: None)
    monkeypatch.setattr(
        run_adapt.importlib,
        "import_module",
        lambda name: modules[name],
    )
    original = list(sys.argv)

    with pytest.raises(RuntimeError, match="boom"):
        run_adapt.main(["--vita-root", str(tmp_path), "run", "--help"])

    assert sys.argv == original
