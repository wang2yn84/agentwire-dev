"""Phase 1B tests — Textual REPL skeleton.

Covers:
- _RichLogSink ANSI parsing
- App boots with mocked SDK
- TextArea submission routes through to /help
- Plain user turn fires the worker, sink renders SDK events

Phase 1C tests for persistence, mentions, prompted-mode, /clear lifecycle
arrive in the next PR.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest


# ---- _RichLogSink ANSI parsing --------------------------------------------


class TestRichLogSink:
    def test_writes_plain_text(self):
        from agentwire.repl.textual_app import _RichLogSink

        captured: list[Any] = []

        class _FakeLog:
            lines: list = []

            def write(self, x):
                captured.append(x)

            def refresh(self):
                pass

        sink = _RichLogSink(_FakeLog())
        sink.write("hello world\n")
        assert len(captured) == 1
        # Text.from_ansi returns a Rich Text instance; its .plain matches.
        assert captured[0].plain == "hello world"

    def test_buffers_until_newline(self):
        # No in-place line updates in Phase 1B — the sink buffers until \n,
        # then emits one complete line. Phase 2A's CurrentAction widget will
        # handle live in-place streaming.
        from agentwire.repl.textual_app import _RichLogSink

        captured: list[Any] = []

        class _FakeLog:
            lines: list = []

            def write(self, x):
                captured.append(x)

        sink = _RichLogSink(_FakeLog())
        sink.write("hello ")
        assert captured == []  # no newline yet, no emit
        sink.write("world\n")
        assert len(captured) == 1
        assert captured[0].plain == "hello world"

    def test_flush_emits_partial_buffer(self):
        # The renderer calls flush() after each delta to make streaming
        # progress visible. In Phase 1B that emits the buffered content as
        # a line, leading to choppy multi-line streaming for partials —
        # an intentional trade-off until Phase 2A's CurrentAction widget.
        from agentwire.repl.textual_app import _RichLogSink

        captured: list[Any] = []

        class _FakeLog:
            lines: list = []

            def write(self, x):
                captured.append(x)

        sink = _RichLogSink(_FakeLog())
        sink.write("[thinking: ")
        sink.flush()
        assert len(captured) == 1
        sink.write("first delta")
        sink.flush()
        assert len(captured) == 2

    def test_parses_ansi_escapes(self):
        from agentwire.repl.textual_app import _RichLogSink

        captured: list[Any] = []

        class _FakeLog:
            lines: list = []

            def write(self, x):
                captured.append(x)

            def refresh(self):
                pass

        sink = _RichLogSink(_FakeLog())
        sink.write("\x1b[1mbold\x1b[0m text\n")
        assert len(captured) == 1
        text = captured[0]
        assert text.plain == "bold text"
        # Rich Text spans: first 4 chars styled bold.
        spans = text.spans
        assert any("bold" in str(span.style).lower() for span in spans)

    def test_cr_clear_collapses_buffer(self):
        # \r\033[K means "discard everything before this point on the current
        # line". With buffered emission, that translates to: drop whatever
        # is in the buffer, take only the content after the last reset.
        # The byte counter sequence ends with `]\n` which finalizes one
        # clean [wrote X · N KB] line.
        from agentwire.repl.textual_app import _RichLogSink

        captured: list[Any] = []

        class _FakeLog:
            lines: list = []

            def write(self, x):
                captured.append(x)

        sink = _RichLogSink(_FakeLog())
        # Simulate: "[writing X · 0 bytes" → "\r\033[K[writing X · 1.2 KB"
        # → "\r\033[K[wrote X · 1.2 KB]\n"
        sink.write("[writing X · 0 bytes")
        sink.write("\r\x1b[K[writing X · 1.2 KB")
        sink.write("\r\x1b[K[wrote X · 1.2 KB]\n")
        # Only the final closed line emits.
        assert len(captured) == 1
        assert captured[0].plain == "[wrote X · 1.2 KB]"

    def test_isatty_returns_true(self):
        # Required so _styled() in app.py emits ANSI codes that the sink parses.
        from agentwire.repl.textual_app import _RichLogSink

        class _FakeLog:
            lines: list = []

        sink = _RichLogSink(_FakeLog())
        assert sink.isatty() is True


# ---- Mock SDK ---------------------------------------------------------------


@dataclass
class _FakeOptions:
    """Mirrors the bits build_options inspects."""
    allowed_tools: list = field(default_factory=list)
    permission_mode: str = "bypassPermissions"


class _FakeAssistantMessage:
    def __init__(self, content):
        self.content = content


class _FakeUserMessage:
    def __init__(self, content):
        self.content = content


class _FakeSystemMessage:
    def __init__(self, subtype="init", data=None):
        self.subtype = subtype
        self.data = data or {}


class _FakeResultMessage:
    def __init__(self, *, total_cost_usd=0.0, duration_ms=0, usage=None, is_error=False, result=None):
        self.total_cost_usd = total_cost_usd
        self.duration_ms = duration_ms
        self.usage = usage or {}
        self.is_error = is_error
        self.result = result


class _FakeClient:
    """Mocks ClaudeSDKClient — async context manager + query/receive_response."""

    def __init__(self, options=None, **kwargs):
        self.options = options
        self._scripted: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def query(self, text: str) -> None:
        self._last_query = text

    def script(self, messages: list) -> None:
        self._scripted = list(messages)

    def receive_response(self):
        scripted = self._scripted
        self._scripted = []

        async def _gen():
            for msg in scripted:
                await asyncio.sleep(0)  # let other tasks see it
                yield msg

        return _gen()


@pytest.fixture
def patched_sdk(monkeypatch):
    """Patches `claude_agent_sdk` imports inside textual_app._open_session."""
    fakes = {
        "ClaudeAgentOptions": _FakeOptions,
        "ClaudeSDKClient": _FakeClient,
        "AssistantMessage": _FakeAssistantMessage,
        "UserMessage": _FakeUserMessage,
        "SystemMessage": _FakeSystemMessage,
        "ResultMessage": _FakeResultMessage,
    }

    # Build a fake module to satisfy `from claude_agent_sdk import ...`.
    import types
    fake_module = types.ModuleType("claude_agent_sdk")
    for name, cls in fakes.items():
        setattr(fake_module, name, cls)

    monkeypatch.setitem(__import__("sys").modules, "claude_agent_sdk", fake_module)

    # Also stub build_options since it imports from claude_agent_sdk and may
    # call methods on the real classes. We just need it to return an
    # object with an allowed_tools attribute.
    from agentwire.repl import textual_app

    def _fake_build_options(ClaudeAgentOptions, mode, model, system_prompt, **kwargs):
        return _FakeOptions(allowed_tools=["Read", "Bash", "Edit"])

    monkeypatch.setattr(textual_app, "build_options", _fake_build_options)
    return fakes


# ---- App boots --------------------------------------------------------------


@pytest.mark.asyncio
async def test_app_boots_and_renders_banner(patched_sdk):
    from agentwire.repl.textual_app import AgentwireREPL

    app = AgentwireREPL(mode="bypass", model="claude-opus-4-7")
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_lines = [
            line.text if hasattr(line, "text") else str(line)
            for line in app.query_one("#chat").lines
        ]
        all_text = " ".join(chat_lines)
        assert "agentwire repl" in all_text
        assert "claude-opus-4-7" in all_text


@pytest.mark.asyncio
async def test_help_command_writes_to_chat(patched_sdk):
    from agentwire.repl.textual_app import AgentwireREPL
    from textual.widgets import Input

    app = AgentwireREPL(mode="bypass")
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", Input)
        inp.value = "/help"
        await inp.action_submit()
        await pilot.pause()
        chat_lines = [
            line.text if hasattr(line, "text") else str(line)
            for line in app.query_one("#chat").lines
        ]
        all_text = " ".join(chat_lines)
        assert "Available commands" in all_text or "help" in all_text.lower()


@pytest.mark.asyncio
async def test_user_turn_fires_worker_and_renders_sdk_events(patched_sdk):
    from agentwire.repl.textual_app import AgentwireREPL
    from textual.widgets import Input

    app = AgentwireREPL(mode="bypass")
    async with app.run_test() as pilot:
        await pilot.pause()

        # Script the fake client to emit a SystemMessage(init) + ResultMessage.
        # Need to grab the actual instance the app opened.
        client = app._client
        assert isinstance(client, _FakeClient)
        client.script([
            _FakeSystemMessage(subtype="init", data={"model": "claude-opus-4-7", "session_id": "abc12345"}),
            _FakeResultMessage(total_cost_usd=0.0042, duration_ms=1500,
                               usage={"input_tokens": 10, "output_tokens": 5}),
        ])

        inp = app.query_one("#input", Input)
        inp.value = "hello"
        await inp.action_submit()

        # Let the worker run + post events back.
        for _ in range(20):
            await pilot.pause()

        chat_lines = [
            line.text if hasattr(line, "text") else str(line)
            for line in app.query_one("#chat").lines
        ]
        all_text = " ".join(chat_lines)
        assert "agent started" in all_text
        assert "claude-opus-4-7" in all_text
        # ResultMessage produces "[done · ...]"
        assert "done" in all_text


@pytest.mark.asyncio
async def test_exit_command_quits_app(patched_sdk):
    from agentwire.repl.textual_app import AgentwireREPL
    from textual.widgets import Input

    app = AgentwireREPL(mode="bypass")
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", Input)
        inp.value = "/exit"
        await inp.action_submit()
        await pilot.pause()
        # After /exit, the app exits — return_code is set.
        assert app.return_code == 0 or not app.is_running
