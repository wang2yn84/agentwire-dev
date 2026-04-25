"""Tests for the SDK-backed REPL — build_options, render_message, print mode.

Phase 1 PR 2. See docs/missions/agentwire-repl.md.
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentwire.repl.app import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    FULL_TOOLS,
    PERMISSION_MODE_MAP,
    RESTRICTED_TOOLS,
    build_options,
    render_message,
    _find_ancestor_file,
    _format_tool_input,
    _format_tool_result,
)


# A fake ClaudeAgentOptions that records kwargs rather than validating them,
# so we can assert on shape without depending on the real SDK at test time.
class FakeOptions:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


# --- build_options ---

class TestBuildOptions:
    @pytest.fixture(autouse=True)
    def _disable_mcp(self, monkeypatch):
        # These tests assert exact tool-list shape — turn off the auto-attached
        # agentwire MCP server so allowed_tools matches the static FULL_TOOLS /
        # RESTRICTED_TOOLS arrays. MCP attachment has its own dedicated tests.
        monkeypatch.setenv("AGENTWIRE_REPL_MCP", "0")
        # Same reasoning for damage control: separate dedicated tests.
        monkeypatch.setenv("AGENTWIRE_REPL_DAMAGE_CONTROL", "0")

    def test_bypass_mode(self, tmp_path):
        opts = build_options(FakeOptions, "bypass", None, None, cwd=tmp_path)
        assert opts.kwargs["permission_mode"] == "bypassPermissions"
        assert opts.kwargs["allowed_tools"] == FULL_TOOLS
        assert opts.kwargs["model"] == DEFAULT_MODEL
        assert opts.kwargs["effort"] == DEFAULT_EFFORT
        assert opts.kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
        assert opts.kwargs["setting_sources"] == ["user"]

    def test_prompted_mode(self, tmp_path):
        opts = build_options(FakeOptions, "prompted", None, None, cwd=tmp_path)
        assert opts.kwargs["permission_mode"] == "default"
        assert opts.kwargs["allowed_tools"] == FULL_TOOLS

    def test_restricted_mode(self, tmp_path):
        opts = build_options(FakeOptions, "restricted", None, None, cwd=tmp_path)
        assert opts.kwargs["permission_mode"] == "plan"
        assert opts.kwargs["allowed_tools"] == RESTRICTED_TOOLS
        # Restricted drops Write/Edit/Bash
        for tool in ("Write", "Edit", "Bash"):
            assert tool not in opts.kwargs["allowed_tools"]

    def test_model_override(self, tmp_path):
        opts = build_options(FakeOptions, "bypass", "claude-sonnet-4-6", None, cwd=tmp_path)
        assert opts.kwargs["model"] == "claude-sonnet-4-6"

    def test_unknown_mode_defaults_bypass(self, tmp_path):
        opts = build_options(FakeOptions, "whatever", None, None, cwd=tmp_path)
        assert opts.kwargs["permission_mode"] == "bypassPermissions"

    def test_cwd_passed_as_str(self, tmp_path):
        opts = build_options(FakeOptions, "bypass", None, None, cwd=tmp_path)
        assert opts.kwargs["cwd"] == str(tmp_path)

    def test_no_system_prompt_no_field(self, tmp_path):
        # Empty cwd dir, no system_prompt → no system_prompt kwarg emitted
        opts = build_options(FakeOptions, "bypass", None, None, cwd=tmp_path)
        assert "system_prompt" not in opts.kwargs

    def test_explicit_system_prompt_appended(self, tmp_path):
        opts = build_options(FakeOptions, "bypass", None, "custom role text", cwd=tmp_path)
        sp = opts.kwargs["system_prompt"]
        assert sp["type"] == "preset"
        assert sp["preset"] == "claude_code"
        assert "custom role text" in sp["append"]

    def test_claude_md_auto_discovery(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("Project CLAUDE.md content")
        opts = build_options(FakeOptions, "bypass", None, None, cwd=tmp_path)
        sp = opts.kwargs["system_prompt"]
        assert "Project CLAUDE.md content" in sp["append"]

    def test_agents_md_auto_discovery(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Agent context from AGENTS.md")
        opts = build_options(FakeOptions, "bypass", None, None, cwd=tmp_path)
        sp = opts.kwargs["system_prompt"]
        assert "Agent context from AGENTS.md" in sp["append"]

    def test_both_files_and_explicit_all_concatenated(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("AAA")
        (tmp_path / "AGENTS.md").write_text("BBB")
        opts = build_options(FakeOptions, "bypass", None, "CCC", cwd=tmp_path)
        append = opts.kwargs["system_prompt"]["append"]
        assert "AAA" in append and "BBB" in append and "CCC" in append

    def test_ancestor_walk(self, tmp_path):
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        (tmp_path / "CLAUDE.md").write_text("from parent")
        opts = build_options(FakeOptions, "bypass", None, None, cwd=nested)
        assert "from parent" in opts.kwargs["system_prompt"]["append"]


class TestFindAncestorFile:
    def test_finds_in_current_dir(self, tmp_path):
        f = tmp_path / "CLAUDE.md"
        f.write_text("x")
        assert _find_ancestor_file(tmp_path, "CLAUDE.md") == f

    def test_walks_up(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        f = tmp_path / "CLAUDE.md"
        f.write_text("x")
        assert _find_ancestor_file(nested, "CLAUDE.md") == f

    def test_missing_returns_none(self, tmp_path):
        assert _find_ancestor_file(tmp_path, "CLAUDE.md") is None


# --- render_message ---

# Fake SDK message types (structural). The renderer uses isinstance, so we
# pass the same fake classes both to render_message and to the messages.
class FakeAssistantMessage:
    def __init__(self, content, model=None):
        self.content = content
        self.model = model


class FakeUserMessage:
    def __init__(self, content):
        self.content = content


class FakeSystemMessage:
    def __init__(self, subtype, data=None):
        self.subtype = subtype
        self.data = data or {}


class FakeResultMessage:
    def __init__(self, usage=None, total_cost_usd=None, duration_ms=None, is_error=False, result=None):
        self.usage = usage or {}
        self.total_cost_usd = total_cost_usd
        self.duration_ms = duration_ms
        self.is_error = is_error
        self.result = result


RENDER_KWARGS = dict(
    AssistantMessage=FakeAssistantMessage,
    UserMessage=FakeUserMessage,
    SystemMessage=FakeSystemMessage,
    ResultMessage=FakeResultMessage,
)


def _render(msg) -> str:
    buf = io.StringIO()
    render_message(msg, out=buf, **RENDER_KWARGS)
    return buf.getvalue()


class TestRenderSystem:
    def test_init_shows_model_and_session(self):
        msg = FakeSystemMessage("init", {"model": "claude-opus-4-7", "session_id": "abc123xyz456"})
        out = _render(msg)
        assert "claude-opus-4-7" in out
        assert "abc123xy" in out  # 8-char truncation

    def test_non_init_silent(self):
        msg = FakeSystemMessage("whatever", {})
        assert _render(msg) == ""


class TestRenderAssistant:
    def test_text_block(self):
        msg = FakeAssistantMessage([{"type": "text", "text": "Hello, world!"}])
        assert "Hello, world!" in _render(msg)

    def test_tool_use_bash(self):
        msg = FakeAssistantMessage([{
            "type": "tool_use", "name": "Bash", "input": {"command": "ls -la"},
        }])
        out = _render(msg)
        assert "→ Bash ls -la" in out

    def test_tool_use_read_shows_file_path(self):
        msg = FakeAssistantMessage([{
            "type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/x.txt"},
        }])
        out = _render(msg)
        assert "/tmp/x.txt" in out

    def test_tool_use_grep(self):
        msg = FakeAssistantMessage([{
            "type": "tool_use", "name": "Grep", "input": {"pattern": "TODO"},
        }])
        assert "→ Grep TODO" in _render(msg)

    def test_tool_use_websearch(self):
        msg = FakeAssistantMessage([{
            "type": "tool_use", "name": "WebSearch", "input": {"query": "brave search API"},
        }])
        assert "brave search API" in _render(msg)

    def test_thinking_block_shows_preview(self):
        msg = FakeAssistantMessage([{
            "type": "thinking", "thinking": "First line of reasoning\nSecond line here",
        }])
        out = _render(msg)
        assert "First line of reasoning" in out
        assert "Second line" not in out  # only first line shown

    def test_bash_long_command_truncated(self):
        long_cmd = "echo " + "x" * 200
        msg = FakeAssistantMessage([{
            "type": "tool_use", "name": "Bash", "input": {"command": long_cmd},
        }])
        out = _render(msg)
        assert "..." in out


class TestRenderUser:
    def test_tool_result_shown(self):
        msg = FakeUserMessage([{
            "type": "tool_result", "tool_use_id": "tu_1", "content": "hello output",
        }])
        out = _render(msg)
        assert "← result: hello output" in out

    def test_tool_result_list_content(self):
        msg = FakeUserMessage([{
            "type": "tool_result",
            "tool_use_id": "tu_1",
            "content": [{"type": "text", "text": "from block list"}],
        }])
        assert "from block list" in _render(msg)

    def test_text_content_not_rendered_as_tool_result(self):
        msg = FakeUserMessage("plain user text")  # string-only content
        assert _render(msg) == ""


class TestRenderResult:
    def test_success_with_tokens_and_cost(self):
        msg = FakeResultMessage(
            usage={"input_tokens": 1000, "output_tokens": 200},
            total_cost_usd=0.0123,
            duration_ms=4500,
        )
        out = _render(msg)
        assert "done" in out
        assert "1000+200 tok" in out
        assert "$0.0123" in out
        assert "4.5s" in out

    def test_error_shows_error(self):
        msg = FakeResultMessage(is_error=True, result="rate limit hit")
        out = _render(msg)
        assert "[error" in out
        assert "rate limit hit" in out

    def test_minimal_result(self):
        msg = FakeResultMessage()
        out = _render(msg)
        assert "[done" in out


# --- format helpers ---

class TestFormatToolInput:
    def test_read_file_path(self):
        assert _format_tool_input("Read", {"file_path": "x.py"}) == "x.py"

    def test_bash_command(self):
        assert _format_tool_input("Bash", {"command": "pwd"}) == "pwd"

    def test_bash_long_truncated(self):
        long = "a" * 150
        out = _format_tool_input("Bash", {"command": long})
        assert out.endswith("...")
        assert len(out) == 80

    def test_unknown_tool_fallback(self):
        out = _format_tool_input("Mystery", {"key": "value"})
        assert "key" in out

    def test_non_dict_input(self):
        assert _format_tool_input("Bash", "not a dict") == ""


# --- print mode end-to-end (mocked SDK) ---

class FakeAsyncContextManager:
    """Minimal async-context fake for ClaudeSDKClient."""

    def __init__(self, messages, options=None):
        self._messages = messages
        self.options = options
        self.queried_prompts: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def query(self, prompt):
        self.queried_prompts.append(prompt)

    async def receive_response(self):
        for m in self._messages:
            yield m


class TestPrintModeIntegration:
    """End-to-end print-mode test with claude-agent-sdk mocked out.

    Installs a fake `claude_agent_sdk` module in sys.modules BEFORE `run_repl`
    imports it, so the real SDK is never touched.
    """

    def test_prints_and_returns_zero(self, monkeypatch, capsys):
        import sys as _sys
        import types

        # Build the fake module
        fake_sdk = types.ModuleType("claude_agent_sdk")

        fake_sdk.AssistantMessage = FakeAssistantMessage
        fake_sdk.UserMessage = FakeUserMessage
        fake_sdk.SystemMessage = FakeSystemMessage
        fake_sdk.ResultMessage = FakeResultMessage
        fake_sdk.ClaudeAgentOptions = FakeOptions

        messages = [
            FakeSystemMessage("init", {"model": "claude-opus-4-7", "session_id": "abc12345def"}),
            FakeAssistantMessage([
                {"type": "tool_use", "name": "Read", "input": {"file_path": "x.py"}},
            ]),
            FakeUserMessage([{"type": "tool_result", "tool_use_id": "t1", "content": "file contents here"}]),
            FakeAssistantMessage([{"type": "text", "text": "Here's the summary."}]),
            FakeResultMessage(
                usage={"input_tokens": 100, "output_tokens": 50},
                total_cost_usd=0.001,
                duration_ms=1500,
                is_error=False,
            ),
        ]

        captured_options = {}

        class MockClient:
            def __init__(self, options):
                captured_options["options"] = options

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def query(self, prompt):
                captured_options["prompt"] = prompt

            async def receive_response(self):
                for m in messages:
                    yield m

        fake_sdk.ClaudeSDKClient = MockClient
        monkeypatch.setitem(_sys.modules, "claude_agent_sdk", fake_sdk)

        from agentwire.repl.app import run_repl
        rc = run_repl(mode="bypass", print_prompt="summarize x.py")
        out = capsys.readouterr().out

        assert rc == 0
        assert captured_options["prompt"] == "summarize x.py"
        assert "agent started" in out
        assert "claude-opus-4-7" in out
        assert "→ Read x.py" in out
        assert "← result: file contents here" in out
        assert "Here's the summary." in out
        assert "[done" in out
        assert "100+50 tok" in out

    def test_error_result_returns_nonzero(self, monkeypatch, capsys):
        import sys as _sys
        import types
        fake_sdk = types.ModuleType("claude_agent_sdk")
        fake_sdk.AssistantMessage = FakeAssistantMessage
        fake_sdk.UserMessage = FakeUserMessage
        fake_sdk.SystemMessage = FakeSystemMessage
        fake_sdk.ResultMessage = FakeResultMessage
        fake_sdk.ClaudeAgentOptions = FakeOptions

        messages = [FakeResultMessage(is_error=True, result="rate limited")]

        class MockClient:
            def __init__(self, options): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def query(self, prompt): pass
            async def receive_response(self):
                for m in messages:
                    yield m

        fake_sdk.ClaudeSDKClient = MockClient
        monkeypatch.setitem(_sys.modules, "claude_agent_sdk", fake_sdk)

        from agentwire.repl.app import run_repl
        rc = run_repl(mode="bypass", print_prompt="x")
        out = capsys.readouterr().out
        assert rc == 1
        assert "[error" in out

    def test_missing_sdk_returns_one(self, monkeypatch, capsys):
        """If claude-agent-sdk isn't importable, print mode exits 1 with message."""
        import sys as _sys
        import builtins

        original_import = builtins.__import__

        def fail_import(name, *a, **kw):
            if name == "claude_agent_sdk":
                raise ImportError("No module named 'claude_agent_sdk'")
            return original_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fail_import)
        # Also ensure cached module is cleared
        monkeypatch.delitem(_sys.modules, "claude_agent_sdk", raising=False)

        from agentwire.repl.app import run_repl
        rc = run_repl(mode="bypass", print_prompt="x")
        err = capsys.readouterr().err
        assert rc == 1
        assert "claude-agent-sdk not installed" in err


# --- interactive loop (mocked prompt_toolkit + SDK) ---

def _install_fake_sdk(monkeypatch, messages_per_turn, tmp_path=None):
    """Install a fake claude_agent_sdk module. messages_per_turn is a list of
    lists; one inner list per .query() call, each yielded by receive_response.

    If tmp_path is provided, persistence.DEFAULT_REPL_HOME is redirected there
    so interactive-loop tests don't pollute ~/.agentwire/sessions/repl/.
    """
    import sys as _sys
    import types

    fake_sdk = types.ModuleType("claude_agent_sdk")
    fake_sdk.AssistantMessage = FakeAssistantMessage
    fake_sdk.UserMessage = FakeUserMessage
    fake_sdk.SystemMessage = FakeSystemMessage
    fake_sdk.ResultMessage = FakeResultMessage
    fake_sdk.ClaudeAgentOptions = FakeOptions

    state = {"queries": [], "turn_idx": 0}

    class MockClient:
        def __init__(self, options):
            state.setdefault("all_options", []).append(options)
            state["options"] = options  # last one
            state["closed"] = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            state["closed"] = True
            return False

        async def query(self, prompt):
            state["queries"].append(prompt)

        async def receive_response(self):
            idx = state["turn_idx"]
            state["turn_idx"] += 1
            msgs = messages_per_turn[idx] if idx < len(messages_per_turn) else []
            for m in msgs:
                yield m

    fake_sdk.ClaudeSDKClient = MockClient
    monkeypatch.setitem(_sys.modules, "claude_agent_sdk", fake_sdk)

    # Redirect persistence to tmp_path so tests don't create real session dirs.
    if tmp_path is not None:
        from agentwire.repl import persistence as _persistence
        monkeypatch.setattr(_persistence, "DEFAULT_REPL_HOME", tmp_path / "repl_home")

    return state


def _install_fake_prompt_toolkit(monkeypatch, script):
    """Install a fake prompt_toolkit module. script is a list of inputs — each
    is either a string (returned by prompt_async) or an exception class (raised)."""
    import sys as _sys
    import types

    fake_pt = types.ModuleType("prompt_toolkit")
    fake_history = types.ModuleType("prompt_toolkit.history")
    fake_patch = types.ModuleType("prompt_toolkit.patch_stdout")
    fake_kb = types.ModuleType("prompt_toolkit.key_binding")

    class FakeInMemoryHistory:
        def __init__(self): pass

    class FakeKeyBindings:
        def __init__(self): pass
        def add(self, *a, **kw):
            # Returns a decorator that just returns the function unchanged.
            def deco(fn): return fn
            return deco

    class FakePromptSession:
        def __init__(self, history=None, multiline=False, key_bindings=None,
                     prompt_continuation=None, bottom_toolbar=None,
                     refresh_interval=None):
            self.idx = 0
            self.bottom_toolbar = bottom_toolbar

        async def prompt_async(self, text):
            if self.idx >= len(script):
                raise EOFError
            item = script[self.idx]
            self.idx += 1
            if isinstance(item, type) and issubclass(item, BaseException):
                raise item
            return item

    class _PatchStdout:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def patch_stdout():
        return _PatchStdout()

    fake_pt.PromptSession = FakePromptSession
    fake_history.InMemoryHistory = FakeInMemoryHistory
    fake_kb.KeyBindings = FakeKeyBindings
    fake_patch.patch_stdout = patch_stdout

    monkeypatch.setitem(_sys.modules, "prompt_toolkit", fake_pt)
    monkeypatch.setitem(_sys.modules, "prompt_toolkit.history", fake_history)
    monkeypatch.setitem(_sys.modules, "prompt_toolkit.key_binding", fake_kb)
    monkeypatch.setitem(_sys.modules, "prompt_toolkit.patch_stdout", fake_patch)


class TestInteractiveLoop:
    @pytest.fixture(autouse=True)
    def _isolate_persistence(self, tmp_path, monkeypatch):
        # Every interactive-loop test spawns a real transcript on disk — point
        # persistence at a tmp_path so we don't pollute ~/.agentwire/sessions/.
        from agentwire.repl import persistence
        monkeypatch.setattr(persistence, "DEFAULT_REPL_HOME", tmp_path / "repl_home")
        self._repl_home = tmp_path / "repl_home"
        # The fake SDK doesn't actually spawn the MCP subprocess, but disable
        # the attachment so allowed_tools / banner / /tools assertions match
        # the pre-Phase-3-PR-1 shape. MCP wiring has its own dedicated tests.
        monkeypatch.setenv("AGENTWIRE_REPL_MCP", "0")
        # Same for damage control — fake SDK ignores hooks, but we don't want
        # the patterns file loaded under tests.
        monkeypatch.setenv("AGENTWIRE_REPL_DAMAGE_CONTROL", "0")

    def test_eof_exits_clean(self, monkeypatch, capsys):
        _install_fake_sdk(monkeypatch, [])
        _install_fake_prompt_toolkit(monkeypatch, [])  # empty → EOFError

        from agentwire.repl.app import run_repl
        rc = run_repl(mode="bypass")
        out = capsys.readouterr().out
        assert rc == 0
        assert "[exit]" in out
        assert "Interactive mode" in out

    def test_slash_exit_command(self, monkeypatch, capsys):
        _install_fake_sdk(monkeypatch, [])
        _install_fake_prompt_toolkit(monkeypatch, ["/exit"])

        from agentwire.repl.app import run_repl
        rc = run_repl(mode="bypass")
        out = capsys.readouterr().out
        assert rc == 0
        assert "[exit]" in out

    def test_slash_quit_command(self, monkeypatch, capsys):
        _install_fake_sdk(monkeypatch, [])
        _install_fake_prompt_toolkit(monkeypatch, ["/quit"])

        from agentwire.repl.app import run_repl
        rc = run_repl(mode="bypass")
        assert rc == 0

    def test_empty_input_skipped(self, monkeypatch, capsys):
        # Empty + whitespace-only lines should not call query, then EOF exits.
        state = _install_fake_sdk(monkeypatch, [])
        _install_fake_prompt_toolkit(monkeypatch, ["", "   ", "\t"])

        from agentwire.repl.app import run_repl
        rc = run_repl(mode="bypass")
        assert rc == 0
        assert state["queries"] == []

    def test_multi_turn_sends_each_prompt(self, monkeypatch, capsys):
        turn1 = [
            FakeSystemMessage("init", {"model": "claude-opus-4-7", "session_id": "s1"}),
            FakeAssistantMessage([{"type": "text", "text": "response one"}]),
            FakeResultMessage(usage={"input_tokens": 10, "output_tokens": 5}, duration_ms=100),
        ]
        turn2 = [
            FakeAssistantMessage([{"type": "text", "text": "response two"}]),
            FakeResultMessage(usage={"input_tokens": 12, "output_tokens": 7}, duration_ms=120),
        ]
        state = _install_fake_sdk(monkeypatch, [turn1, turn2])
        _install_fake_prompt_toolkit(monkeypatch, ["first prompt", "second prompt"])

        from agentwire.repl.app import run_repl
        rc = run_repl(mode="bypass")
        out = capsys.readouterr().out

        assert rc == 0
        assert state["queries"] == ["first prompt", "second prompt"]
        assert "response one" in out
        assert "response two" in out
        assert "agent started" in out
        assert state["closed"] is True

    def test_error_result_sets_exit_code_but_continues_loop(self, monkeypatch, capsys):
        # One bad turn followed by clean exit should exit with 1 (from the error).
        bad = [FakeResultMessage(is_error=True, result="oh no")]
        state = _install_fake_sdk(monkeypatch, [bad])
        _install_fake_prompt_toolkit(monkeypatch, ["trigger the bad turn"])

        from agentwire.repl.app import run_repl
        rc = run_repl(mode="bypass")
        out = capsys.readouterr().out
        assert rc == 1
        assert "[error" in out

    def test_keyboard_interrupt_at_prompt_continues(self, monkeypatch, capsys):
        # Ctrl+C at prompt → clear line, continue. Next EOF exits clean.
        state = _install_fake_sdk(monkeypatch, [])
        _install_fake_prompt_toolkit(monkeypatch, [KeyboardInterrupt])

        from agentwire.repl.app import run_repl
        rc = run_repl(mode="bypass")
        assert rc == 0
        assert state["queries"] == []

    def test_passes_mode_and_model_to_options(self, monkeypatch, capsys):
        state = _install_fake_sdk(monkeypatch, [])
        _install_fake_prompt_toolkit(monkeypatch, [])

        from agentwire.repl.app import run_repl
        run_repl(mode="restricted", model="claude-sonnet-4-6")

        opts = state["options"]
        assert opts.kwargs["permission_mode"] == "plan"
        assert opts.kwargs["model"] == "claude-sonnet-4-6"
        assert "Bash" not in opts.kwargs["allowed_tools"]

    def test_help_command(self, monkeypatch, capsys):
        _install_fake_sdk(monkeypatch, [])
        _install_fake_prompt_toolkit(monkeypatch, ["/help"])

        from agentwire.repl.app import run_repl
        rc = run_repl(mode="bypass")
        out = capsys.readouterr().out
        assert rc == 0
        assert "/help" in out
        assert "/clear" in out
        assert "/cost" in out

    def test_cost_command_after_turn(self, monkeypatch, capsys):
        turn = [
            FakeSystemMessage("init", {"model": "claude-opus-4-7", "session_id": "abc"}),
            FakeAssistantMessage([{"type": "text", "text": "hi"}]),
            FakeResultMessage(
                usage={"input_tokens": 100, "output_tokens": 50},
                total_cost_usd=0.003,
                duration_ms=100,
            ),
        ]
        _install_fake_sdk(monkeypatch, [turn])
        _install_fake_prompt_toolkit(monkeypatch, ["hello", "/cost"])

        from agentwire.repl.app import run_repl
        rc = run_repl(mode="bypass")
        out = capsys.readouterr().out
        assert rc == 0
        assert "100+50=150 tok" in out
        assert "$0.0030" in out
        assert "1 turn" in out

    def test_tools_command(self, monkeypatch, capsys):
        _install_fake_sdk(monkeypatch, [])
        _install_fake_prompt_toolkit(monkeypatch, ["/tools"])

        from agentwire.repl.app import run_repl
        run_repl(mode="bypass")
        out = capsys.readouterr().out
        # bypass gets the full tool set
        for tool in ("Read", "Write", "Edit", "Bash", "Grep", "Glob", "WebFetch", "WebSearch"):
            assert tool in out

    def test_tools_restricted(self, monkeypatch, capsys):
        _install_fake_sdk(monkeypatch, [])
        _install_fake_prompt_toolkit(monkeypatch, ["/tools"])

        from agentwire.repl.app import run_repl
        run_repl(mode="restricted")
        out = capsys.readouterr().out
        assert "mode=restricted" in out
        # Write/Edit/Bash NOT in restricted
        assert "Write" not in out.split("\n")[-3]  # check near the tools line

    def test_clear_restarts_session(self, monkeypatch, capsys):
        # Two turns, /clear between them should reopen the SDK client
        # (second "agent started" line), and cost reset.
        turn1 = [
            FakeSystemMessage("init", {"model": "claude-opus-4-7", "session_id": "sess1"}),
            FakeResultMessage(usage={"input_tokens": 10, "output_tokens": 5}, duration_ms=100),
        ]
        turn2 = [
            FakeSystemMessage("init", {"model": "claude-opus-4-7", "session_id": "sess2"}),
            FakeResultMessage(usage={"input_tokens": 20, "output_tokens": 10}, duration_ms=100),
        ]
        state = _install_fake_sdk(monkeypatch, [turn1, turn2])
        _install_fake_prompt_toolkit(monkeypatch, [
            "first turn",   # sent on client 1
            "/clear",       # close client 1, open client 2
            "/cost",        # should say no turns yet (reset)
            "second turn",  # sent on client 2
        ])

        from agentwire.repl.app import run_repl
        rc = run_repl(mode="bypass")
        out = capsys.readouterr().out
        assert rc == 0
        assert state["queries"] == ["first turn", "second turn"]
        assert out.count("agent started") == 2  # opened twice
        assert "restarting conversation" in out
        assert "no turns yet" in out  # /cost after /clear before any turn

    def test_unknown_command_continues(self, monkeypatch, capsys):
        # /foo is not a command — should print "unknown command" and continue
        # to EOF exit, never touching the SDK.
        state = _install_fake_sdk(monkeypatch, [])
        _install_fake_prompt_toolkit(monkeypatch, ["/foo"])

        from agentwire.repl.app import run_repl
        rc = run_repl(mode="bypass")
        out = capsys.readouterr().out
        assert rc == 0
        assert "unknown command" in out
        assert "/foo" in out
        assert state["queries"] == []

    def test_transcript_written_for_each_turn(self, monkeypatch, capsys):
        import json
        turn = [
            FakeSystemMessage("init", {"model": "claude-opus-4-7", "session_id": "sdk-abc"}),
            FakeAssistantMessage([{"type": "text", "text": "hi"}]),
            FakeResultMessage(usage={"input_tokens": 5, "output_tokens": 3}, duration_ms=100),
        ]
        _install_fake_sdk(monkeypatch, [turn])
        _install_fake_prompt_toolkit(monkeypatch, ["hello there"])

        from agentwire.repl.app import run_repl
        rc = run_repl(mode="bypass")
        assert rc == 0

        # A session dir was created under the patched home
        sessions = list(self._repl_home.iterdir())
        assert len(sessions) == 1
        sess = sessions[0]
        meta = json.loads((sess / "metadata.json").read_text())
        assert meta["mode"] == "bypass"
        assert meta["turn_count"] == 1
        assert meta["total_input_tokens"] == 5
        assert meta["total_output_tokens"] == 3
        assert "sdk-abc" in meta["sdk_session_ids"]

        lines = (sess / "transcript.jsonl").read_text().splitlines()
        events = [json.loads(line) for line in lines]
        types = [e["type"] for e in events]
        assert "user_input" in types
        assert "session" in types
        assert "agent_start" in types
        assert "message_end" in types
        assert "turn_end" in types
        assert "agent_end" in types
        # user_input has the literal text
        ui = next(e for e in events if e["type"] == "user_input")
        assert ui["text"] == "hello there"

    def test_explicit_session_name(self, monkeypatch, capsys):
        _install_fake_sdk(monkeypatch, [])
        _install_fake_prompt_toolkit(monkeypatch, [])

        from agentwire.repl.app import run_repl
        run_repl(mode="bypass", session_name="explicit-name")

        assert (self._repl_home / "explicit-name").is_dir()

    def test_save_command_reflects_transcript(self, monkeypatch, capsys):
        _install_fake_sdk(monkeypatch, [])
        _install_fake_prompt_toolkit(monkeypatch, ["/save"])

        from agentwire.repl.app import run_repl
        run_repl(mode="bypass", session_name="save-test")
        out = capsys.readouterr().out
        assert "save-test" in out
        assert str(self._repl_home / "save-test") in out

    def test_resume_fails_gracefully_when_session_absent(self, monkeypatch, capsys):
        _install_fake_sdk(monkeypatch, [])
        _install_fake_prompt_toolkit(monkeypatch, [])

        from agentwire.repl.app import run_repl
        rc = run_repl(mode="bypass", resume="missing")
        err = capsys.readouterr().err
        assert rc == 1
        assert "no session named 'missing'" in err

    def test_resume_passes_sdk_session_id_to_options(self, monkeypatch, capsys):
        # First run creates a session and records its sdk_session_id.
        first_turn = [
            FakeSystemMessage("init", {"model": "claude-opus-4-7", "session_id": "first-sdk-id"}),
            FakeResultMessage(usage={"input_tokens": 1, "output_tokens": 1}, duration_ms=10),
        ]
        state1 = _install_fake_sdk(monkeypatch, [first_turn])
        _install_fake_prompt_toolkit(monkeypatch, ["hi"])

        from agentwire.repl.app import run_repl
        rc = run_repl(mode="bypass", session_name="resumable")
        assert rc == 0

        # Second run resumes — fake SDK should see `resume=first-sdk-id`
        # in the options passed to ClaudeSDKClient.
        state2 = _install_fake_sdk(monkeypatch, [[]])
        _install_fake_prompt_toolkit(monkeypatch, [])

        rc = run_repl(mode="bypass", resume="resumable")
        assert rc == 0
        # New FakeOptions built for this run should carry the resume id.
        opts = state2["options"]
        assert opts.kwargs.get("resume") == "first-sdk-id"

    def test_in_repl_resume_reopens_sdk_with_resume_id(self, monkeypatch, capsys):
        # Pre-seed a saved session
        from agentwire.repl import persistence
        seed = persistence.create_session(
            mode="bypass", model="claude-opus-4-7",
            allowed_tools=["Read"], name="prior",
            home=self._repl_home,
        )
        persistence.record_session_id(seed, "prior-sdk-id")
        seed.close()

        # Now run with /resume prior → the second SDK client should have
        # resume="prior-sdk-id" in its options.
        turn1 = [FakeResultMessage(usage={"input_tokens": 1, "output_tokens": 1}, duration_ms=10)]
        turn2 = [FakeResultMessage(usage={"input_tokens": 1, "output_tokens": 1}, duration_ms=10)]
        state = _install_fake_sdk(monkeypatch, [turn1, turn2])
        _install_fake_prompt_toolkit(monkeypatch, [
            "first turn on fresh session",
            "/resume prior",
            "second turn on resumed session",
        ])

        from agentwire.repl.app import run_repl
        rc = run_repl(mode="bypass")
        assert rc == 0

        # Two FakeOptions were created (one per SDK client open).
        all_opts = state["all_options"]
        assert len(all_opts) == 2
        # First had no resume; second should.
        assert all_opts[0].kwargs.get("resume") is None
        assert all_opts[1].kwargs.get("resume") == "prior-sdk-id"

    def test_mention_expanded_before_sdk_query(self, monkeypatch, capsys, tmp_path):
        # File in CWD that the user mentions
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "note.txt").write_text("the secret is bananas")
        monkeypatch.chdir(proj)

        turn = [
            FakeResultMessage(usage={"input_tokens": 5, "output_tokens": 1}, duration_ms=10),
        ]
        state = _install_fake_sdk(monkeypatch, [turn])
        _install_fake_prompt_toolkit(monkeypatch, ["summarize @note.txt please"])

        from agentwire.repl.app import run_repl
        rc = run_repl(mode="bypass", session_name="mention-test")
        assert rc == 0
        # The string sent to the SDK includes the expanded file contents.
        assert len(state["queries"]) == 1
        sent = state["queries"][0]
        assert "the secret is bananas" in sent
        assert "@note.txt" not in sent  # original mention replaced
        # Notice line shown
        out = capsys.readouterr().out
        assert "expanded 1 mention" in out

    def test_mention_recorded_in_transcript(self, monkeypatch, capsys, tmp_path):
        import json
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "data.txt").write_text("payload-text")
        monkeypatch.chdir(proj)

        turn = [FakeResultMessage(usage={"input_tokens": 1, "output_tokens": 1}, duration_ms=10)]
        _install_fake_sdk(monkeypatch, [turn])
        _install_fake_prompt_toolkit(monkeypatch, ["check @data.txt"])

        from agentwire.repl.app import run_repl
        run_repl(mode="bypass", session_name="mention-record")

        events_path = self._repl_home / "mention-record" / "transcript.jsonl"
        events = [json.loads(line) for line in events_path.read_text().splitlines()]
        ui = next(e for e in events if e["type"] == "user_input")
        # Raw text preserved
        assert ui["text"] == "check @data.txt"
        # Expanded text + mentions metadata recorded
        assert "payload-text" in ui["expanded_text"]
        assert ui["mentions"] == [{"raw": "@data.txt", "target": "data.txt"}]

    def test_no_mention_no_expanded_field(self, monkeypatch, capsys):
        # Plain text → no expanded_text/mentions keys in user_input event.
        import json
        turn = [FakeResultMessage(usage={"input_tokens": 1, "output_tokens": 1}, duration_ms=10)]
        _install_fake_sdk(monkeypatch, [turn])
        _install_fake_prompt_toolkit(monkeypatch, ["plain hello"])

        from agentwire.repl.app import run_repl
        run_repl(mode="bypass", session_name="no-mention")

        events_path = self._repl_home / "no-mention" / "transcript.jsonl"
        events = [json.loads(line) for line in events_path.read_text().splitlines()]
        ui = next(e for e in events if e["type"] == "user_input")
        assert "expanded_text" not in ui
        assert "mentions" not in ui

    def test_seed_message_runs_first_then_eof_exits(self, monkeypatch, capsys):
        # Seed turn fires before prompt_async is consulted. Empty fake script
        # → prompt_async raises EOFError on the second iteration → clean exit.
        turn = [FakeResultMessage(usage={"input_tokens": 1, "output_tokens": 1}, duration_ms=10)]
        state = _install_fake_sdk(monkeypatch, [turn])
        _install_fake_prompt_toolkit(monkeypatch, [])  # next prompt → EOF

        from agentwire.repl.app import run_repl
        rc = run_repl(mode="bypass", seed_message="please review this plan")
        assert rc == 0
        assert state["queries"] == ["please review this plan"]

    def test_multiline_input_sent_as_one_turn(self, monkeypatch, capsys):
        # The fake PromptSession just returns a string — so a multi-line
        # string acts like the user typed Alt+Enter then hit Enter.
        turn = [FakeResultMessage(usage={"input_tokens": 1, "output_tokens": 1}, duration_ms=10)]
        state = _install_fake_sdk(monkeypatch, [turn])
        _install_fake_prompt_toolkit(monkeypatch, ["line one\nline two\nline three"])

        from agentwire.repl.app import run_repl
        rc = run_repl(mode="bypass")
        assert rc == 0
        assert state["queries"] == ["line one\nline two\nline three"]

    def test_missing_prompt_toolkit_returns_one(self, monkeypatch, capsys):
        _install_fake_sdk(monkeypatch, [])
        # Scrub prompt_toolkit from sys.modules AND block future imports.
        import sys as _sys
        for mod in list(_sys.modules):
            if mod.startswith("prompt_toolkit"):
                monkeypatch.delitem(_sys.modules, mod, raising=False)

        import builtins
        original_import = builtins.__import__

        def fail_import(name, *a, **kw):
            if name.startswith("prompt_toolkit"):
                raise ImportError(f"No module named {name!r}")
            return original_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fail_import)

        from agentwire.repl.app import run_repl
        rc = run_repl(mode="bypass")
        err = capsys.readouterr().err
        assert rc == 1
        assert "prompt_toolkit not installed" in err


class TestStreamRenderState:
    """Partial-message rendering — added 2026-04-25 to fix the silent gap."""

    def _state(self):
        from agentwire.repl.app import _StreamRenderState
        return _StreamRenderState()

    def test_text_delta_streams_inline(self):
        s = self._state()
        out = io.StringIO()
        s.handle_partial(
            {"type": "content_block_start", "content_block": {"type": "text"}},
            out,
        )
        s.handle_partial(
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello "}},
            out,
        )
        s.handle_partial(
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "world"}},
            out,
        )
        s.handle_partial({"type": "content_block_stop"}, out)
        rendered = out.getvalue()
        assert "Hello world" in rendered
        assert s.streamed_text is True
        assert s.open_block is None

    def test_thinking_delta_streams_inline(self):
        s = self._state()
        out = io.StringIO()
        s.handle_partial(
            {"type": "content_block_start", "content_block": {"type": "thinking"}},
            out,
        )
        s.handle_partial(
            {"type": "content_block_delta",
             "delta": {"type": "thinking_delta", "thinking": "let me\nplan this"}},
            out,
        )
        s.handle_partial({"type": "content_block_stop"}, out)
        rendered = out.getvalue()
        assert "[thinking: let me plan this]" in rendered
        assert s.streamed_thinking is True

    def test_input_json_delta_shows_byte_counter(self):
        # Tool input streaming via input_json_delta would dump raw JSON bytes
        # into the chat. Instead we show a live byte counter — the snapshot
        # AssistantMessage that follows gives the formatted [→ Write file.html]
        # summary with parsed args.
        s = self._state()
        out = io.StringIO()
        s.handle_partial(
            {"type": "content_block_start",
             "content_block": {"type": "tool_use", "name": "Write"}},
            out,
        )
        s.handle_partial(
            {"type": "content_block_delta",
             "delta": {"type": "input_json_delta", "partial_json": '{"file_path": "x"}'}},
            out,
        )
        rendered = out.getvalue()
        # Counter line is written, raw JSON is not.
        assert "writing Write input" in rendered
        assert '"file_path"' not in rendered
        assert s.open_block == "tool_use"
        assert s._tool_use_bytes == len('{"file_path": "x"}')

    def test_tool_use_close_finalizes_byte_counter(self):
        s = self._state()
        out = io.StringIO()
        s.handle_partial(
            {"type": "content_block_start",
             "content_block": {"type": "tool_use", "name": "Write"}},
            out,
        )
        # 2 KB of fake JSON across two deltas
        s.handle_partial(
            {"type": "content_block_delta",
             "delta": {"type": "input_json_delta", "partial_json": "x" * 1024}},
            out,
        )
        s.handle_partial(
            {"type": "content_block_delta",
             "delta": {"type": "input_json_delta", "partial_json": "x" * 1024}},
            out,
        )
        s.handle_partial({"type": "content_block_stop"}, out)
        rendered = out.getvalue()
        assert "wrote Write input" in rendered
        assert "2.0 KB" in rendered
        assert s.open_block is None
        assert s._tool_use_bytes == 0  # reset after close

    def test_streamevent_dataclass_detected_by_render_message(self, monkeypatch):
        # StreamEvent is a @dataclass, not a dict. Earlier code did
        # `isinstance(message, dict)` and silently dropped every partial event.
        # Verify duck-type detection works for dataclass-shaped objects.
        from dataclasses import dataclass
        from agentwire.repl.app import render_message, _StreamRenderState

        @dataclass
        class FakeStreamEvent:
            uuid: str
            session_id: str
            event: dict
            parent_tool_use_id: str | None = None

        s = _StreamRenderState()
        out = io.StringIO()
        evt = FakeStreamEvent(
            uuid="abc",
            session_id="s",
            event={"type": "content_block_start", "content_block": {"type": "text"}},
        )
        render_message(
            evt,
            AssistantMessage=FakeAssistantMessage,
            UserMessage=FakeUserMessage,
            SystemMessage=FakeSystemMessage,
            ResultMessage=FakeResultMessage,
            out=out,
            stream_state=s,
        )
        # text block opened — render_message routed the partial through
        # handle_partial. (Before the duck-type fix this was a no-op.)
        assert s.open_block == "text"
        assert s.streamed_text is True

    def test_no_ansi_codes_for_non_tty_output(self):
        # Tests run with StringIO as `out` — isatty()==False — so no ANSI
        # codes should ever leak into the captured output. Substring asserts
        # in the rest of the suite depend on this.
        s = self._state()
        out = io.StringIO()
        s.handle_partial(
            {"type": "content_block_start", "content_block": {"type": "thinking"}},
            out,
        )
        s.handle_partial(
            {"type": "content_block_delta",
             "delta": {"type": "thinking_delta", "thinking": "plan"}},
            out,
        )
        s.handle_partial({"type": "content_block_stop"}, out)
        rendered = out.getvalue()
        assert "\x1b[" not in rendered  # no ANSI escapes
        assert "[thinking: plan]" in rendered

    def test_ansi_codes_emitted_for_tty_output(self):
        # When out.isatty() is True, dim-style ANSI codes wrap the thinking
        # block. This is the visual hierarchy: thinking is secondary noise,
        # dim makes it recede.
        from agentwire.repl.app import _StreamRenderState

        class _TTYBuffer:
            def __init__(self):
                self.buf = io.StringIO()

            def write(self, s):
                self.buf.write(s)

            def flush(self):
                pass

            def isatty(self):
                return True

            def getvalue(self):
                return self.buf.getvalue()

        s = _StreamRenderState()
        out = _TTYBuffer()
        s.handle_partial(
            {"type": "content_block_start", "content_block": {"type": "thinking"}},
            out,
        )
        s.handle_partial({"type": "content_block_stop"}, out)
        rendered = out.getvalue()
        assert "\x1b[" in rendered  # ANSI present
        assert "[thinking: " in rendered  # raw text still recoverable

    def test_heartbeat_silent_during_tool_use(self):
        # The byte counter IS the liveness signal during tool_use — adding
        # `·` dots would corrupt the in-place CR rewrite.
        s = self._state()
        out = io.StringIO()
        s.handle_partial(
            {"type": "content_block_start",
             "content_block": {"type": "tool_use", "name": "Write"}},
            out,
        )
        before = out.getvalue()
        s.heartbeat(out)
        s.heartbeat(out)
        after = out.getvalue()
        # Heartbeat is a no-op during tool_use.
        assert before == after

    def test_assistant_skips_streamed_text(self, monkeypatch):
        # When partials already streamed text, the snapshot AssistantMessage
        # text block should NOT re-render.
        from agentwire.repl.app import render_message, _StreamRenderState
        s = _StreamRenderState()
        s.streamed_text = True
        out = io.StringIO()

        msg = FakeAssistantMessage(content=[{"type": "text", "text": "full reply"}])
        render_message(
            msg,
            AssistantMessage=FakeAssistantMessage,
            UserMessage=FakeUserMessage,
            SystemMessage=FakeSystemMessage,
            ResultMessage=FakeResultMessage,
            out=out,
            stream_state=s,
        )
        assert "full reply" not in out.getvalue()
        # state resets after snapshot
        assert s.streamed_text is False

    def test_assistant_renders_when_no_partials(self):
        from agentwire.repl.app import render_message, _StreamRenderState
        s = _StreamRenderState()
        out = io.StringIO()
        msg = FakeAssistantMessage(content=[{"type": "text", "text": "full reply"}])
        render_message(
            msg,
            AssistantMessage=FakeAssistantMessage,
            UserMessage=FakeUserMessage,
            SystemMessage=FakeSystemMessage,
            ResultMessage=FakeResultMessage,
            out=out,
            stream_state=s,
        )
        assert "full reply" in out.getvalue()

    def test_heartbeat_inline_when_open_block(self):
        s = self._state()
        out = io.StringIO()
        s.handle_partial(
            {"type": "content_block_start", "content_block": {"type": "thinking"}},
            out,
        )
        s.heartbeat(out)
        s.heartbeat(out)
        # Two dots appended to the open thinking line.
        rendered = out.getvalue()
        assert rendered.count("·") == 2

    def test_heartbeat_standalone_when_idle(self):
        s = self._state()
        out = io.StringIO()
        s.heartbeat(out)
        s.heartbeat(out)
        # Forms a single status line: "[…still working · 5s · 10s"
        # (no trailing ] until a real event consumes it)
        rendered = out.getvalue()
        assert "still working" in rendered
        assert "5s" in rendered and "10s" in rendered

    def test_heartbeat_consumed_by_real_event(self):
        s = self._state()
        out = io.StringIO()
        s.heartbeat(out)
        # Real event arrives → consumes the open heartbeat line
        s.handle_partial(
            {"type": "content_block_start", "content_block": {"type": "text"}},
            out,
        )
        rendered = out.getvalue()
        # Heartbeat line was closed (saw "]\n" before any content).
        assert "still working" in rendered
        assert rendered.index("]") < rendered.rindex("\n")


class TestHeartbeatIter:
    def test_emits_heartbeat_on_idle(self):
        from agentwire.repl.app import _heartbeat_iter, _HEARTBEAT
        import asyncio

        async def slow_source():
            await asyncio.sleep(0.15)
            yield "done"

        async def collect():
            results = []
            async for x in _heartbeat_iter(slow_source(), idle_timeout=0.05):
                results.append(x)
                if x == "done":
                    break
            return results

        results = asyncio.run(collect())
        assert _HEARTBEAT in results
        assert "done" in results

    def test_no_heartbeat_when_fast(self):
        from agentwire.repl.app import _heartbeat_iter, _HEARTBEAT
        import asyncio

        async def fast_source():
            yield 1
            yield 2

        async def collect():
            results = []
            async for x in _heartbeat_iter(fast_source(), idle_timeout=1.0):
                results.append(x)
            return results

        results = asyncio.run(collect())
        assert results == [1, 2]
        assert _HEARTBEAT not in results


class TestThinkingConfig:
    def test_adaptive_default(self):
        # adaptive now defaults to display:summarized (Opus 4.7 hides
        # thinking by default; we always want it visible in the REPL).
        from agentwire.repl.app import _thinking_config
        assert _thinking_config("adaptive") == {"type": "adaptive", "display": "summarized"}

    def test_summarized_sets_display(self):
        from agentwire.repl.app import _thinking_config
        assert _thinking_config("summarized") == {"type": "adaptive", "display": "summarized"}

    def test_off_disabled(self):
        from agentwire.repl.app import _thinking_config
        assert _thinking_config("off") == {"type": "disabled"}

    def test_unknown_falls_back_adaptive(self):
        from agentwire.repl.app import _thinking_config
        assert _thinking_config("nonsense") == {"type": "adaptive", "display": "summarized"}


class TestBottomToolbar:
    def _state(self):
        from agentwire.repl.state import ReplState
        return ReplState(mode="bypass", model="claude-opus-4-7", allowed_tools=[])

    def test_pre_first_turn_shows_config(self):
        from agentwire.repl.app import _make_bottom_toolbar
        s = self._state()
        out = _make_bottom_toolbar(s)()
        assert "bypass" in out
        assert "claude-opus-4-7" in out
        assert "effort=high" in out
        assert "thinking=adaptive" in out
        # No tokens before first turn
        assert "tok" not in out

    def test_after_first_turn_shows_totals(self):
        from agentwire.repl.app import _make_bottom_toolbar
        s = self._state()
        s.turn_count = 2
        s.total_input_tokens = 100
        s.total_output_tokens = 50
        s.total_cost_usd = 0.012
        out = _make_bottom_toolbar(s)()
        assert "2 turns" in out
        assert "150 tok" in out
        assert "100 in" in out
        assert "50 out" in out
        assert "$0.0120" in out

    def test_singular_turn_pluralization(self):
        from agentwire.repl.app import _make_bottom_toolbar
        s = self._state()
        s.turn_count = 1
        out = _make_bottom_toolbar(s)()
        assert "1 turn " in out  # not "1 turns"


class TestPermissionCallback:
    def _state(self):
        from agentwire.repl.state import ReplState
        return ReplState(mode="prompted", model="m", allowed_tools=[])

    def _ctx(self):
        from types import SimpleNamespace
        return SimpleNamespace(signal=None, suggestions=[])

    def test_yes_allows(self, monkeypatch):
        import agentwire.repl.app as app
        monkeypatch.setattr(app, "_prompt_sync", lambda prompt: "y\n")

        s = self._state()
        cb = app._make_can_use_tool(s)
        result = asyncio.run(cb("Read", {"file_path": "/etc/hosts"}, self._ctx()))
        from claude_agent_sdk import PermissionResultAllow
        assert isinstance(result, PermissionResultAllow)
        assert s.always_allow_tools == set()

    def test_no_denies(self, monkeypatch):
        import agentwire.repl.app as app
        monkeypatch.setattr(app, "_prompt_sync", lambda prompt: "n\n")

        s = self._state()
        cb = app._make_can_use_tool(s)
        result = asyncio.run(cb("Bash", {"command": "ls"}, self._ctx()))
        from claude_agent_sdk import PermissionResultDeny
        assert isinstance(result, PermissionResultDeny)

    def test_always_remembers(self, monkeypatch):
        import agentwire.repl.app as app
        monkeypatch.setattr(app, "_prompt_sync", lambda prompt: "a\n")

        s = self._state()
        cb = app._make_can_use_tool(s)
        result = asyncio.run(cb("Read", {"file_path": "/x"}, self._ctx()))
        from claude_agent_sdk import PermissionResultAllow
        assert isinstance(result, PermissionResultAllow)
        assert "Read" in s.always_allow_tools

        # Subsequent call should bypass _prompt_sync
        def boom(prompt):
            raise AssertionError("prompt_sync should not be called")
        monkeypatch.setattr(app, "_prompt_sync", boom)
        result2 = asyncio.run(cb("Read", {"file_path": "/y"}, self._ctx()))
        assert isinstance(result2, PermissionResultAllow)

    def test_blank_defaults_to_allow(self, monkeypatch):
        import agentwire.repl.app as app
        monkeypatch.setattr(app, "_prompt_sync", lambda prompt: "\n")
        s = self._state()
        cb = app._make_can_use_tool(s)
        result = asyncio.run(cb("Read", {}, self._ctx()))
        from claude_agent_sdk import PermissionResultAllow
        assert isinstance(result, PermissionResultAllow)


class TestMcpBakedIn:
    def test_mcp_servers_attached_by_default(self, monkeypatch):
        from agentwire.repl.app import build_options, MCP_SERVER_NAME, MCP_TOOL_PREFIX
        monkeypatch.delenv("AGENTWIRE_REPL_MCP", raising=False)

        captured = {}

        class FakeOptions:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.allowed_tools = kwargs.get("allowed_tools", [])

        build_options(FakeOptions, mode="bypass", model="m", system_prompt=None, cwd=None)
        assert MCP_SERVER_NAME in captured["mcp_servers"]
        cfg = captured["mcp_servers"][MCP_SERVER_NAME]
        assert cfg["type"] == "stdio"
        assert cfg["args"] == ["-m", "agentwire", "mcp"]
        assert MCP_TOOL_PREFIX in captured["allowed_tools"]

    def test_mcp_disabled_via_env(self, monkeypatch):
        from agentwire.repl.app import build_options, MCP_TOOL_PREFIX
        monkeypatch.setenv("AGENTWIRE_REPL_MCP", "0")

        captured = {}

        class FakeOptions:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.allowed_tools = kwargs.get("allowed_tools", [])

        build_options(FakeOptions, mode="bypass", model="m", system_prompt=None, cwd=None)
        assert "mcp_servers" not in captured
        assert MCP_TOOL_PREFIX not in captured["allowed_tools"]

    def test_restricted_mode_keeps_mcp(self, monkeypatch):
        # MCP tools include lots of read-only inspection tools (sessions_list,
        # panes_list); blocking the entire server in restricted mode loses too
        # much. Plan-mode permission_mode still gates execution.
        from agentwire.repl.app import build_options, MCP_TOOL_PREFIX
        monkeypatch.delenv("AGENTWIRE_REPL_MCP", raising=False)

        captured = {}

        class FakeOptions:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.allowed_tools = kwargs.get("allowed_tools", [])

        build_options(FakeOptions, mode="restricted", model="m", system_prompt=None, cwd=None)
        assert MCP_TOOL_PREFIX in captured["allowed_tools"]


class TestSessionContextThreading:
    def test_role_instructions_appended_to_system_prompt(self, monkeypatch):
        from agentwire.repl.app import build_options
        from agentwire.repl.context import SessionContext
        monkeypatch.setenv("AGENTWIRE_REPL_MCP", "0")
        monkeypatch.setenv("AGENTWIRE_REPL_DAMAGE_CONTROL", "0")

        captured = {}

        class FakeOptions:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.allowed_tools = kwargs.get("allowed_tools", [])

        ctx = SessionContext(
            role_names=["tester"],
            role_instructions="Test rigorously.",
            voice="alice",
            missing_roles=[],
        )
        build_options(
            FakeOptions, mode="bypass", model="m", system_prompt=None,
            cwd=None, session_context=ctx,
        )
        sp = captured["system_prompt"]
        assert sp["type"] == "preset"
        assert "tester" in sp["append"]
        assert "Test rigorously" in sp["append"]

    def test_no_session_context_no_change(self, monkeypatch):
        from agentwire.repl.app import build_options
        monkeypatch.setenv("AGENTWIRE_REPL_MCP", "0")
        monkeypatch.setenv("AGENTWIRE_REPL_DAMAGE_CONTROL", "0")

        captured = {}

        class FakeOptions:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.allowed_tools = kwargs.get("allowed_tools", [])

        build_options(FakeOptions, mode="bypass", model="m", system_prompt=None, cwd=None)
        # No system_prompt key when nothing to append.
        assert "system_prompt" not in captured


class TestDamageControlAttachment:
    def test_hooks_attached_when_patterns_load(self, monkeypatch, tmp_path):
        # Point damage_control at a known patterns file
        from agentwire.repl import damage_control
        patterns_file = tmp_path / "patterns.yaml"
        patterns_file.write_text(
            "bashToolPatterns:\n  - pattern: '\\brm\\s+-rf'\n    reason: rm -rf\n"
        )
        monkeypatch.setattr(damage_control, "DEFAULT_PATTERNS_PATH", patterns_file)
        monkeypatch.delenv("AGENTWIRE_REPL_DAMAGE_CONTROL", raising=False)
        monkeypatch.setenv("AGENTWIRE_REPL_MCP", "0")

        from agentwire.repl.app import build_options

        captured = {}

        class FakeOptions:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.allowed_tools = kwargs.get("allowed_tools", [])

        build_options(FakeOptions, mode="bypass", model="m", system_prompt=None, cwd=None)
        assert "hooks" in captured
        assert "PreToolUse" in captured["hooks"]
        matchers = captured["hooks"]["PreToolUse"]
        assert len(matchers) == 1
        assert "Bash" in matchers[0].matcher

    def test_no_hooks_when_disabled(self, monkeypatch, tmp_path):
        from agentwire.repl import damage_control
        patterns_file = tmp_path / "patterns.yaml"
        patterns_file.write_text("bashToolPatterns: []\n")
        monkeypatch.setattr(damage_control, "DEFAULT_PATTERNS_PATH", patterns_file)
        monkeypatch.setenv("AGENTWIRE_REPL_DAMAGE_CONTROL", "0")
        monkeypatch.setenv("AGENTWIRE_REPL_MCP", "0")

        from agentwire.repl.app import build_options

        captured = {}

        class FakeOptions:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.allowed_tools = kwargs.get("allowed_tools", [])

        build_options(FakeOptions, mode="bypass", model="m", system_prompt=None, cwd=None)
        assert "hooks" not in captured

    def test_no_hooks_when_patterns_missing(self, monkeypatch, tmp_path):
        # Patterns file doesn't exist → make_pre_tool_hook returns None →
        # no hooks attached.
        from agentwire.repl import damage_control
        monkeypatch.setattr(damage_control, "DEFAULT_PATTERNS_PATH", tmp_path / "nope.yaml")
        monkeypatch.delenv("AGENTWIRE_REPL_DAMAGE_CONTROL", raising=False)
        monkeypatch.setenv("AGENTWIRE_REPL_MCP", "0")

        from agentwire.repl.app import build_options

        captured = {}

        class FakeOptions:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.allowed_tools = kwargs.get("allowed_tools", [])

        build_options(FakeOptions, mode="bypass", model="m", system_prompt=None, cwd=None)
        assert "hooks" not in captured


class TestSdkErrorClassify:
    def test_transient_429(self):
        from agentwire.workflows.runners.sdk_errors import classify
        assert classify("HTTPError", "got 429 rate_limit") == "transient"

    def test_auth_401(self):
        from agentwire.workflows.runners.sdk_errors import classify
        assert classify("AuthError", "401 unauthorized") == "permanent"

    def test_invalid_400(self):
        from agentwire.workflows.runners.sdk_errors import classify
        assert classify("ValidationError", "invalid_request: bad field") == "invalid"

    def test_generic(self):
        from agentwire.workflows.runners.sdk_errors import classify
        assert classify("RuntimeError", "something broke") == "error"


class TestBuildOptionsThreadsKnobs:
    def test_effort_and_thinking_passed_through(self):
        from agentwire.repl.app import build_options

        captured = {}

        class FakeOptions:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.allowed_tools = kwargs.get("allowed_tools", [])

        build_options(
            FakeOptions, mode="bypass", model="m", system_prompt=None,
            cwd=None, effort="max", thinking_mode="summarized",
        )
        assert captured["effort"] == "max"
        assert captured["thinking"] == {"type": "adaptive", "display": "summarized"}

    def test_can_use_tool_passed_through(self):
        from agentwire.repl.app import build_options

        captured = {}

        class FakeOptions:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.allowed_tools = kwargs.get("allowed_tools", [])

        async def cb(*args, **kwargs): pass
        build_options(
            FakeOptions, mode="prompted", model="m", system_prompt=None,
            cwd=None, can_use_tool=cb,
        )
        assert captured["can_use_tool"] is cb

    def test_can_use_tool_omitted_when_none(self):
        from agentwire.repl.app import build_options

        captured = {}

        class FakeOptions:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.allowed_tools = kwargs.get("allowed_tools", [])

        build_options(
            FakeOptions, mode="bypass", model="m", system_prompt=None,
            cwd=None,
        )
        assert "can_use_tool" not in captured


class TestFormatToolResult:
    def test_none(self):
        assert _format_tool_result(None) == "(no content)"

    def test_string(self):
        assert _format_tool_result("hello") == "hello"

    def test_newlines_flattened(self):
        assert _format_tool_result("a\nb\nc") == "a b c"

    def test_truncated(self):
        long = "x" * 200
        out = _format_tool_result(long)
        assert out.endswith("...")
        assert len(out) == 120

    def test_list_of_blocks(self):
        assert _format_tool_result([{"type": "text", "text": "from block"}]) == "from block"
