"""Tests for the workflow runner registry and default cascade."""

from __future__ import annotations

import pytest
import yaml

from agentwire.workflows.definitions import load_workflow
from agentwire.workflows.node import ActionNode
from agentwire.workflows.runners import (
    RUNNERS,
    NodeRunner,
    available_runners,
    get_runner,
    register_runner,
)
from agentwire.workflows.runners.pi import PiRunner


class TestRegistry:
    def test_pi_registered_by_default(self):
        assert "pi" in available_runners()
        assert isinstance(get_runner("pi"), PiRunner)

    def test_unknown_runner_raises(self):
        with pytest.raises(KeyError):
            get_runner("no-such-runner")

    def test_register_and_replace(self):
        class _Fake:
            name = "fakerunner"
            def run(self, node, **kw):
                return None
        fake = _Fake()
        register_runner(fake)
        assert "fakerunner" in available_runners()
        assert get_runner("fakerunner") is fake
        # Cleanup — registry is module-global.
        del RUNNERS["fakerunner"]
        assert "fakerunner" not in available_runners()

    def test_pi_runner_conforms_to_protocol(self):
        # Structural protocol check — PiRunner must expose name + run().
        runner: NodeRunner = PiRunner()
        assert runner.name == "pi"
        assert callable(runner.run)


class TestDefaultCascade:
    def test_action_node_defaults_to_pi(self):
        n = ActionNode(id="a", prompt="p")
        assert n.runner == "pi"

    def test_node_level_runner_wins(self, tmp_path):
        yml = tmp_path / "wf.yaml"
        yml.write_text(yaml.safe_dump({
            "name": "wf",
            "runner": "pi",
            "nodes": {
                "a": {"prompt": "p"},                 # inherits workflow-level "pi"
                "b": {"prompt": "p", "runner": "pi"}, # explicit match
            },
        }))
        wf = load_workflow(yml)
        runners = {n.id: n.runner for n in wf.nodes}
        assert runners == {"a": "pi", "b": "pi"}

    def test_workflow_level_applies_when_no_node_runner(self, tmp_path):
        # Register a second runner so the workflow-level "test2" is valid.
        class _T2:
            name = "test2"
            def run(self, node, **kw):
                return None
        register_runner(_T2())
        try:
            yml = tmp_path / "wf.yaml"
            yml.write_text(yaml.safe_dump({
                "name": "wf",
                "runner": "test2",
                "nodes": {
                    "a": {"prompt": "p"},                  # inherits "test2"
                    "b": {"prompt": "p", "runner": "pi"},  # overrides to "pi"
                },
            }))
            wf = load_workflow(yml)
            runners = {n.id: n.runner for n in wf.nodes}
            assert runners == {"a": "test2", "b": "pi"}
        finally:
            del RUNNERS["test2"]

    def test_no_workflow_default_falls_back_to_pi(self, tmp_path):
        yml = tmp_path / "wf.yaml"
        yml.write_text(yaml.safe_dump({
            "name": "wf",
            "nodes": {"a": {"prompt": "p"}},
        }))
        wf = load_workflow(yml)
        assert wf.nodes[0].runner == "pi"

    def test_unknown_runner_flagged_by_validate(self, tmp_path):
        yml = tmp_path / "wf.yaml"
        yml.write_text(yaml.safe_dump({
            "name": "wf",
            "nodes": {"a": {"prompt": "p", "runner": "ghost"}},
        }))
        wf = load_workflow(yml)
        errors = wf.validate()
        assert any("runner='ghost'" in e for e in errors)
