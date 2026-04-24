"""Tests for the REPL slash command registry + state tracking.

Phase 2 PR 1. See docs/missions/agentwire-repl.md.
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

from agentwire.repl.commands import (
    COMMANDS,
    CONTINUE,
    EXIT,
    RESTART,
    dispatch_command,
)
from agentwire.repl.state import (
    ReplState,
    reset_for_restart,
    track_result,
    track_system_init,
)


def _state(**overrides) -> ReplState:
    defaults = dict(
        mode="bypass",
        model="claude-opus-4-7",
        allowed_tools=["Read", "Bash", "Edit"],
    )
    defaults.update(overrides)
    return ReplState(**defaults)


# --- dispatch ---

class TestDispatch:
    def test_non_slash_returns_none(self):
        state = _state()
        out = io.StringIO()
        assert dispatch_command("hello world", state, out) is None

    def test_unknown_command_continues_with_notice(self):
        state = _state()
        out = io.StringIO()
        action = dispatch_command("/nonexistent", state, out)
        assert action == CONTINUE
        assert "unknown command" in out.getvalue()
        assert "/nonexistent" in out.getvalue()

    def test_help_continues(self):
        state = _state()
        out = io.StringIO()
        assert dispatch_command("/help", state, out) == CONTINUE

    def test_exit_returns_exit(self):
        out = io.StringIO()
        assert dispatch_command("/exit", _state(), out) == EXIT

    def test_quit_alias_returns_exit(self):
        out = io.StringIO()
        assert dispatch_command("/quit", _state(), out) == EXIT

    def test_clear_returns_restart(self):
        out = io.StringIO()
        assert dispatch_command("/clear", _state(), out) == RESTART

    def test_args_passed_through(self):
        # No current commands take args, but the dispatcher must not error when
        # a user types extra words. `/help foo bar` should still run /help.
        state = _state()
        out = io.StringIO()
        assert dispatch_command("/help some extra", state, out) == CONTINUE


# --- /help ---

class TestHelp:
    def test_lists_every_command_once(self):
        out = io.StringIO()
        dispatch_command("/help", _state(), out)
        rendered = out.getvalue()
        for name in ("/help", "/clear", "/cost", "/tools", "/model", "/exit"):
            assert name in rendered
        # /quit is an alias, not a row
        assert rendered.count("Exit the REPL") == 1

    def test_shows_aliases(self):
        out = io.StringIO()
        dispatch_command("/help", _state(), out)
        assert "/quit" in out.getvalue()


# --- /cost ---

class TestCost:
    def test_no_turns(self):
        out = io.StringIO()
        dispatch_command("/cost", _state(), out)
        assert "no turns yet" in out.getvalue()

    def test_with_turns(self):
        state = _state()
        state.total_input_tokens = 1000
        state.total_output_tokens = 500
        state.total_cost_usd = 0.025
        state.turn_count = 3
        out = io.StringIO()
        dispatch_command("/cost", state, out)
        rendered = out.getvalue()
        assert "3 turns" in rendered
        assert "1000+500=1500 tok" in rendered
        assert "$0.0250" in rendered

    def test_singular_turn(self):
        state = _state()
        state.turn_count = 1
        state.total_input_tokens = 10
        state.total_output_tokens = 5
        out = io.StringIO()
        dispatch_command("/cost", state, out)
        assert "1 turn " in out.getvalue()  # singular, with trailing space


# --- /tools ---

class TestTools:
    def test_shows_list(self):
        out = io.StringIO()
        dispatch_command("/tools", _state(allowed_tools=["Read", "Bash"]), out)
        rendered = out.getvalue()
        assert "Read" in rendered
        assert "Bash" in rendered
        assert "mode=bypass" in rendered

    def test_empty(self):
        out = io.StringIO()
        dispatch_command("/tools", _state(allowed_tools=[]), out)
        assert "no tools allowed" in out.getvalue()

    def test_restricted_mode(self):
        out = io.StringIO()
        state = _state(mode="restricted", allowed_tools=["Read", "Grep"])
        dispatch_command("/tools", state, out)
        assert "mode=restricted" in out.getvalue()


# --- /model ---

class TestModel:
    def test_shows_model_and_mode(self):
        out = io.StringIO()
        state = _state(model="claude-opus-4-7", mode="bypass")
        state.session_id = "abc12345def"
        dispatch_command("/model", state, out)
        rendered = out.getvalue()
        assert "claude-opus-4-7" in rendered
        assert "bypass" in rendered
        assert "abc12345" in rendered  # 8-char truncation

    def test_no_session_yet(self):
        out = io.StringIO()
        dispatch_command("/model", _state(), out)
        assert "not yet started" in out.getvalue()


# --- /clear state reset ---

class TestClearSemantics:
    def test_clear_returns_restart(self):
        out = io.StringIO()
        state = _state()
        state.total_input_tokens = 100
        state.turn_count = 5
        action = dispatch_command("/clear", state, out)
        assert action == RESTART
        # /clear itself doesn't zero the state — the outer loop does that
        # via reset_for_restart(). Separation keeps the handler pure.
        assert state.total_input_tokens == 100
        assert state.turn_count == 5


# --- ReplState trackers ---

class TestTrackSystemInit:
    def test_captures_session_id(self):
        state = _state()
        msg = SimpleNamespace(data={"session_id": "sess-xyz"})
        track_system_init(state, msg)
        assert state.session_id == "sess-xyz"

    def test_camelcase_session_id(self):
        state = _state()
        msg = SimpleNamespace(data={"sessionId": "camel-id"})
        track_system_init(state, msg)
        assert state.session_id == "camel-id"

    def test_missing_data_noop(self):
        state = _state()
        msg = SimpleNamespace(data=None)
        track_system_init(state, msg)
        assert state.session_id is None


class TestTrackResult:
    def test_accumulates_tokens(self):
        state = _state()
        m1 = SimpleNamespace(usage={"input_tokens": 10, "output_tokens": 5}, total_cost_usd=0.01)
        m2 = SimpleNamespace(usage={"input_tokens": 20, "output_tokens": 15}, total_cost_usd=0.02)
        track_result(state, m1)
        track_result(state, m2)
        assert state.total_input_tokens == 30
        assert state.total_output_tokens == 20
        assert state.total_cost_usd == pytest.approx(0.03)
        assert state.turn_count == 2

    def test_missing_cost_ignored(self):
        state = _state()
        m = SimpleNamespace(usage={"input_tokens": 10, "output_tokens": 5}, total_cost_usd=None)
        track_result(state, m)
        assert state.total_cost_usd == 0.0
        assert state.turn_count == 1

    def test_non_numeric_cost_ignored(self):
        state = _state()
        m = SimpleNamespace(usage={}, total_cost_usd="not a number")
        track_result(state, m)
        assert state.total_cost_usd == 0.0

    def test_missing_usage_still_increments_turn(self):
        state = _state()
        m = SimpleNamespace(usage=None, total_cost_usd=None)
        track_result(state, m)
        assert state.turn_count == 1


class TestResetForRestart:
    def test_zeros_counters_preserves_config(self):
        state = _state()
        state.total_input_tokens = 100
        state.total_output_tokens = 50
        state.total_cost_usd = 0.5
        state.turn_count = 4
        state.session_id = "old"
        reset_for_restart(state)
        assert state.total_input_tokens == 0
        assert state.total_output_tokens == 0
        assert state.total_cost_usd == 0.0
        assert state.turn_count == 0
        assert state.session_id is None
        assert state.restart_count == 1
        # Config preserved
        assert state.mode == "bypass"
        assert state.model == "claude-opus-4-7"
        assert state.allowed_tools == ["Read", "Bash", "Edit"]

    def test_multiple_restarts_increment(self):
        state = _state()
        reset_for_restart(state)
        reset_for_restart(state)
        reset_for_restart(state)
        assert state.restart_count == 3
