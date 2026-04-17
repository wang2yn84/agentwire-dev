"""CLI output assertions for workflow show / history with runner fields."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from agentwire.workflows import cli as wf_cli
from agentwire.workflows.context import Context
from agentwire.workflows.node import NodeResult
from agentwire.workflows.runner import WorkflowRun
from agentwire.workflows.storage import write_run


def _make_run(run_id: str, runner_name: str, tokens: dict | None = None) -> WorkflowRun:
    return WorkflowRun(
        workflow="demo",
        run_id=run_id,
        status="success",
        started_at=time.time(),
        duration_ms=500,
        node_results=[
            NodeResult(
                node_id="a",
                status="success",
                final_text="",
                duration_ms=100,
                runner=runner_name,
                tokens_used=tokens or {},
            ),
        ],
    )


@pytest.fixture
def runs_dir(tmp_path, monkeypatch):
    d = tmp_path / "runs"
    d.mkdir()
    # cli.py reads RUNS_DIR module-level constant; point it at tmp.
    monkeypatch.setattr(wf_cli, "RUNS_DIR", d)
    return d


class TestShow:
    def test_show_renders_runner_and_totals(self, runs_dir, capsys):
        run = _make_run(
            "demo-r1",
            "anthropic",
            tokens={"input": 100, "output": 50, "cost": 0.0023},
        )
        write_run(runs_dir, run.run_id, run, Context())

        args = SimpleNamespace(run_id="demo-r1", node=None, events=False, json=False)
        rc = wf_cli.cmd_workflow_show(args)
        assert rc == 0

        out = capsys.readouterr().out
        assert "Runner:   anthropic" in out
        assert "(anthropic" in out   # per-node row
        assert "Totals: in=100 out=50 cost=$0.0023" in out

    def test_show_pi_run_no_totals_when_no_tokens(self, runs_dir, capsys):
        run = _make_run("demo-r2", "pi", tokens={})
        write_run(runs_dir, run.run_id, run, Context())

        args = SimpleNamespace(run_id="demo-r2", node=None, events=False, json=False)
        wf_cli.cmd_workflow_show(args)

        out = capsys.readouterr().out
        assert "Runner:   pi" in out
        # No Totals line when every node has empty tokens.
        assert "Totals:" not in out

    def test_show_json_includes_runner(self, runs_dir, capsys):
        run = _make_run("demo-r3", "anthropic", tokens={"input": 1, "output": 2})
        write_run(runs_dir, run.run_id, run, Context())

        args = SimpleNamespace(run_id="demo-r3", node=None, events=False, json=True)
        wf_cli.cmd_workflow_show(args)

        payload = json.loads(capsys.readouterr().out)
        assert payload["runner"] == "anthropic"
        assert payload["nodes"][0]["runner"] == "anthropic"


class TestHistory:
    def test_history_table_has_runner_column(self, runs_dir, capsys):
        run_a = _make_run("demo-r4", "pi")
        run_b = _make_run("demo-r5", "anthropic")
        write_run(runs_dir, run_a.run_id, run_a, Context())
        write_run(runs_dir, run_b.run_id, run_b, Context())

        args = SimpleNamespace(workflow=None, limit=20, json=False)
        rc = wf_cli.cmd_workflow_history(args)
        assert rc == 0

        out = capsys.readouterr().out
        assert "runner" in out       # header row
        assert "pi" in out
        assert "anthropic" in out

    def test_history_json_carries_runner(self, runs_dir, capsys):
        run = _make_run("demo-r6", "anthropic")
        write_run(runs_dir, run.run_id, run, Context())

        args = SimpleNamespace(workflow=None, limit=20, json=True)
        wf_cli.cmd_workflow_history(args)

        rows = json.loads(capsys.readouterr().out)
        assert rows[0]["runner"] == "anthropic"


class TestVerbosePrinter:
    """Directly exercise _make_verbose_printer — no live runner needed."""

    def test_prints_tool_use(self, capsys):
        printer = wf_cli._make_verbose_printer()
        printer({
            "type": "message_end",
            "node_id": "n1",
            "message": {
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "name": "Read",
                    "input": {"file_path": "/tmp/x.txt"},
                }],
            },
        })
        out = capsys.readouterr().out
        assert "[n1]" in out
        assert "tool_use Read" in out
        assert "file_path" in out

    def test_prints_tool_result_ok_and_err(self, capsys):
        printer = wf_cli._make_verbose_printer()
        printer({
            "type": "message_end",
            "node_id": "n1",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "content": "hello\nworld", "is_error": False}],
            },
        })
        printer({
            "type": "message_end",
            "node_id": "n1",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "content": "boom", "is_error": True}],
            },
        })
        out = capsys.readouterr().out
        assert "(ok)" in out
        assert "(err)" in out
        assert "hello world" in out    # newline collapsed

    def test_prints_turn_and_agent_end(self, capsys):
        printer = wf_cli._make_verbose_printer()
        printer({"type": "turn_end", "node_id": "n1", "usage": {"input": 100, "output": 42}})
        printer({"type": "agent_end", "node_id": "n1", "duration_ms": 3100})
        out = capsys.readouterr().out
        assert "turn 100+42 tok" in out
        assert "agent_end 3.1s" in out

    def test_silently_drops_unknown_events(self, capsys):
        printer = wf_cli._make_verbose_printer()
        printer({"type": "made_up_event", "node_id": "n1"})
        assert capsys.readouterr().out == ""
