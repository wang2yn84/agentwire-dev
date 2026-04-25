"""Tests for the human_gate workflow runner (Phase 4)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentwire.workflows.node import ActionNode
from agentwire.workflows.runners.human_gate import (
    HumanGateRunner,
    _extract_final_assistant_text,
    _extract_tokens,
)


class TestRegistration:
    def test_human_gate_in_available_runners(self):
        from agentwire.workflows.runners import available_runners
        assert "human_gate" in available_runners()


class TestRequiresTty:
    def test_non_tty_fails_fast(self, monkeypatch):
        # sys.stdin.isatty returns False under pytest by default
        runner = HumanGateRunner()
        node = ActionNode(id="n1", prompt="please review", runner="human_gate")
        result = runner.run(node)
        assert result.status == "failure"
        assert "TTY" in result.error
        assert "scheduler" in result.error


class TestSeedAndOutputExtraction:
    def test_run_invokes_run_repl_with_seed(self, monkeypatch, tmp_path):
        # Force isatty True
        import sys as _sys
        monkeypatch.setattr(_sys.stdin, "isatty", lambda: True)

        captured = {}

        def fake_run_repl(**kwargs):
            captured.update(kwargs)
            # Simulate the REPL writing its transcript on the way out.
            from agentwire.repl import persistence
            sd = persistence.DEFAULT_REPL_HOME / kwargs["session_name"]
            sd.mkdir(parents=True, exist_ok=True)
            (sd / "metadata.json").write_text(json.dumps({
                "name": kwargs["session_name"],
                "total_input_tokens": 12,
                "total_output_tokens": 34,
                "total_cost_usd": 0.0042,
            }))
            (sd / "events.jsonl").write_text(
                json.dumps({"type": "assistant", "text": "first response"}) + "\n"
                + json.dumps({"type": "assistant", "text": "FINAL APPROVAL"}) + "\n"
            )
            return 0

        # Redirect persistence and patch run_repl
        from agentwire.repl import persistence
        monkeypatch.setattr(persistence, "DEFAULT_REPL_HOME", tmp_path / "repl_home")
        from agentwire.repl import app as repl_app
        monkeypatch.setattr(repl_app, "run_repl", fake_run_repl)

        runner = HumanGateRunner()
        node = ActionNode(id="my-gate", prompt="approve this?", runner="human_gate")
        result = runner.run(node)

        assert result.status == "success"
        assert result.final_text == "FINAL APPROVAL"
        assert captured["seed_message"] == "approve this?"
        assert captured["mode"] == "bypass"
        assert captured["session_name"].startswith("workflow-my-gate-")
        assert result.tokens_used["input_tokens"] == 12
        assert result.tokens_used["output_tokens"] == 34
        assert result.tokens_used["total_cost_usd"] == pytest.approx(0.0042)

    def test_repl_nonzero_exit_marks_failure(self, monkeypatch, tmp_path):
        import sys as _sys
        monkeypatch.setattr(_sys.stdin, "isatty", lambda: True)
        from agentwire.repl import persistence
        monkeypatch.setattr(persistence, "DEFAULT_REPL_HOME", tmp_path / "repl_home")

        def fake_run_repl(**kwargs):
            return 1

        from agentwire.repl import app as repl_app
        monkeypatch.setattr(repl_app, "run_repl", fake_run_repl)

        runner = HumanGateRunner()
        result = runner.run(ActionNode(id="x", prompt="p", runner="human_gate"))
        assert result.status == "failure"
        assert "REPL exited with code 1" in result.error


class TestExtractFinalText:
    def test_missing_file(self, tmp_path):
        assert _extract_final_assistant_text(tmp_path / "nope.jsonl") == ""

    def test_returns_last_assistant(self, tmp_path):
        path = tmp_path / "events.jsonl"
        path.write_text(
            json.dumps({"type": "user", "text": "hi"}) + "\n"
            + json.dumps({"type": "assistant", "text": "first"}) + "\n"
            + json.dumps({"type": "system"}) + "\n"
            + json.dumps({"type": "assistant", "text": "second"}) + "\n"
        )
        assert _extract_final_assistant_text(path) == "second"

    def test_skips_garbage_lines(self, tmp_path):
        path = tmp_path / "events.jsonl"
        path.write_text(
            "{not valid json\n"
            + json.dumps({"type": "assistant", "text": "ok"}) + "\n"
        )
        assert _extract_final_assistant_text(path) == "ok"

    def test_falls_back_to_content_blocks(self, tmp_path):
        path = tmp_path / "events.jsonl"
        path.write_text(
            json.dumps({
                "type": "assistant",
                "content": [{"type": "tool_use"}, {"type": "text", "text": "hello"}],
            }) + "\n"
        )
        assert _extract_final_assistant_text(path) == "hello"


class TestExtractTokens:
    def test_none_meta(self):
        assert _extract_tokens(None) == {}

    def test_empty_meta(self):
        # Empty dict is treated like None — nothing to surface.
        assert _extract_tokens({}) == {}

    def test_only_some_fields(self):
        out = _extract_tokens({"total_input_tokens": 5})
        assert out["input_tokens"] == 5
        assert out["output_tokens"] == 0
        assert out["total_cost_usd"] == 0.0

    def test_populated(self):
        out = _extract_tokens({
            "total_input_tokens": 10, "total_output_tokens": 20,
            "total_cost_usd": 0.5,
        })
        assert out == {"input_tokens": 10, "output_tokens": 20, "total_cost_usd": 0.5}
