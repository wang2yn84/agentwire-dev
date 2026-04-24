"""Tests for the SDK-backed REPL — build_options, render_message, print mode.

Phase 1 PR 2. See docs/missions/agentwire-repl.md.
"""

from __future__ import annotations

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
    def test_bypass_mode(self, tmp_path):
        opts = build_options(FakeOptions, "bypass", None, None, cwd=tmp_path)
        assert opts.kwargs["permission_mode"] == "bypassPermissions"
        assert opts.kwargs["allowed_tools"] == FULL_TOOLS
        assert opts.kwargs["model"] == DEFAULT_MODEL
        assert opts.kwargs["effort"] == DEFAULT_EFFORT
        assert opts.kwargs["thinking"] == {"type": "adaptive"}
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
