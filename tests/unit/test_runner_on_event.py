"""on_event callback threading: run_workflow → _run_one_node_with_retries → runner.run.

Uses a fake runner that emits a couple of events during run(). Verifies that:
  - the callback receives every emitted event
  - each event carries a `node_id` stamp injected by the runner loop
  - NodeResult.runner is populated from node.runner
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from agentwire.workflows.context import Context
from agentwire.workflows.definitions import WorkflowDef
from agentwire.workflows.node import ActionNode, NodeResult
from agentwire.workflows.runner import run_workflow
from agentwire.workflows.runners import RUNNERS, register_runner


class _FakeRunner:
    name = "fake_event_runner"

    def run(
        self,
        node: ActionNode,
        workflow_cwd: str | None = None,
        event_log_path: Path | None = None,
        on_event: Callable[[dict], None] | None = None,
    ) -> NodeResult:
        if on_event is not None:
            on_event({"type": "agent_start"})
            on_event({"type": "message_end", "message": {"role": "assistant", "content": []}})
            on_event({"type": "agent_end", "duration_ms": 42})
        return NodeResult(
            node_id=node.id,
            status="success",
            final_text="ok",
            duration_ms=42,
        )


@pytest.fixture
def fake_runner_registered():
    register_runner(_FakeRunner())
    yield
    RUNNERS.pop(_FakeRunner.name, None)


class TestOnEventThreading:
    def test_callback_receives_events_with_node_id(self, tmp_path, fake_runner_registered):
        collected: list[dict] = []

        # Patch node.py validation: register a fake runner + use it.
        node = ActionNode(id="n1", prompt="hi", runner="fake_event_runner")
        wf = WorkflowDef(name="test-cb", nodes=[node])

        result = run_workflow(
            wf,
            runs_dir=tmp_path / "runs",
            inputs={},
            on_event=collected.append,
        )

        assert result.status == "success"
        types = [e["type"] for e in collected]
        assert types == ["agent_start", "message_end", "agent_end"]
        assert all(e.get("node_id") == "n1" for e in collected)

    def test_no_callback_when_none(self, tmp_path, fake_runner_registered):
        """Absence of callback must not crash the runner loop."""
        node = ActionNode(id="n1", prompt="hi", runner="fake_event_runner")
        wf = WorkflowDef(name="test-cb-none", nodes=[node])

        result = run_workflow(wf, runs_dir=tmp_path / "runs", inputs={})
        assert result.status == "success"

    def test_node_result_runner_populated(self, tmp_path, fake_runner_registered):
        """result.runner should be set from node.runner after the runner returns."""
        node = ActionNode(id="n1", prompt="hi", runner="fake_event_runner")
        wf = WorkflowDef(name="test-rnr-field", nodes=[node])

        result = run_workflow(wf, runs_dir=tmp_path / "runs", inputs={})
        assert result.node_results[0].runner == "fake_event_runner"
