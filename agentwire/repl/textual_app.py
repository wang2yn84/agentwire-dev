"""Textual-based rendering layer for `agentwire repl` (Phase 1B skeleton).

Layout (Phase 1 — minimal; Phase 2 splits into chat + current-action with
proportional weights):

    ┌─ Header ─────────────────────────────┐
    │                                      │
    ├─ ChatLog (RichLog, fr=1) ────────────┤
    │  > previous user turn                │
    │  [thinking: ...]                     │
    │  assistant text                      │
    │  [→ tool call]                       │
    │  [← result]                          │
    ├─ Input (TextArea, dock=bottom) ──────┤
    │  > tell me about prompt caching      │
    └─ Footer ─────────────────────────────┘

Event flow:
    user types → SubmittableTextArea posts Submitted → on_input_submitted
    → app.run_worker(_run_turn(text), exclusive=True)
    → worker iterates client.receive_response() wrapped in _heartbeat_iter
    → each message is post_message(SdkEvent(msg)) (thread-safe)
    → on_sdk_event renders via render_message(out=_RichLogSink) on UI thread

Workers MUST use `self.post_message(...)` — never call RichLog.write directly.

Phase 1B scope: chat history + input + slash dispatch + SDK streaming. No
persistence, no @-mentions, no permission prompts (those land in Phase 1C).

See `docs/missions/agentwire-repl-textual.md` for the full plan.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widgets import Footer, Header, Input, RichLog

from agentwire.repl.app import (
    BANNER,
    DEFAULT_MODEL,
    FULL_TOOLS,
    RESTRICTED_TOOLS,
    _HEARTBEAT,
    _StreamRenderState,
    _heartbeat_iter,
    _mcp_enabled,
    build_options,
    render_message,
)
from agentwire.repl.commands import (
    CONTINUE,
    EXIT,
    RESTART,
    RESUME,
    dispatch_command,
)
from agentwire.repl.context import load_session_context
from agentwire.repl.state import ReplState, track_result, track_system_init


# ----- adapters -------------------------------------------------------------


class _RichLogSink:
    """Adapter so `render_message(out=...)` can target a `RichLog`.

    The renderer writes ANSI-bearing strings; we buffer until newline, then
    emit each line as `Text.from_ansi(line)` so Rich parses styles faithfully.

    Phase 1B trade-off — streaming partial messages (thinking, byte counter,
    heartbeat) reach this sink as a sequence of writes terminated by a flush()
    rather than a `\\n`. RichLog is append-only — there's no clean way to
    update an in-flight line in place without bypassing its public API. So
    each flush emits its current buffer as a discrete line, which means a
    streaming thinking block shows as multiple lines (one per delta) rather
    than one line growing in place.

    Phase 2A introduces a dedicated CurrentAction widget that handles live
    streaming properly. Until then, partial-stream output here is choppy but
    correct; tool calls, results, agent meta, and finalized messages render
    cleanly on their own lines.

    `\\r\\033[K` in the input is the byte-counter "discard previous in-flight
    line" signal — we honor it as "drop everything in the buffer up to and
    including the last reset", which collapses repeated counter ticks to a
    single visible line on close.
    """

    _CR_CLEAR = "\r\x1b[K"

    def __init__(self, log: RichLog) -> None:
        self._log = log
        self._buf = ""

    def write(self, s: str) -> None:
        if not s:
            return
        if self._CR_CLEAR in s:
            # Discard any in-flight buffer content + everything in `s` before
            # the last reset — the byte counter is announcing a fresh value.
            self._buf = ""
            s = s.rsplit(self._CR_CLEAR, 1)[1]
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._emit(line)

    def flush(self) -> None:
        # Emit any pending buffered content as a partial line. The choppy
        # multi-line streaming this produces is intentional Phase 1B trade-off.
        if self._buf:
            self._emit(self._buf)
            self._buf = ""

    def isatty(self) -> bool:
        # We want `_styled()` in app.py to emit ANSI; the sink renders it
        # through Rich. Returning True here is what enables the styling.
        return True

    def _emit(self, text: str) -> None:
        if text == "":
            self._log.write("")
        else:
            self._log.write(Text.from_ansi(text))


# ----- SDK event message ----------------------------------------------------


class SdkEvent(Message):
    """One SDK message (or _HEARTBEAT sentinel) handed off from the worker
    thread to the UI thread for rendering."""

    def __init__(self, payload: Any) -> None:
        self.payload = payload
        super().__init__()


class TurnFinished(Message):
    """Worker signals turn complete (or cancelled). UI re-enables input."""

    def __init__(self, error: str | None = None) -> None:
        self.error = error
        super().__init__()


# ----- the App --------------------------------------------------------------


class AgentwireREPL(App):
    """Textual REPL skeleton — Phase 1B.

    Phase 1B carries chat rendering + slash dispatch + SDK streaming.
    Phase 1C wires persistence, @-mentions, permission prompts, /clear+/resume
    lifecycle. Phase 2 splits the chat region into chat+CurrentAction.
    """

    CSS = """
    Screen {
        layout: vertical;
    }

    #chat {
        height: 1fr;
        border: tall $accent;
        padding: 0 1;
    }

    #input {
        dock: bottom;
        height: 3;
        border: tall $accent-lighten-1;
    }

    Header {
        dock: top;
    }

    Footer {
        dock: bottom;
    }
    """

    BINDINGS = [
        Binding("ctrl+d", "quit", "Exit"),
        Binding("ctrl+c", "cancel_turn", "Cancel turn"),
    ]

    def __init__(
        self,
        *,
        mode: str = "bypass",
        model: str | None = None,
        system_prompt: str | None = None,
        session_name: str | None = None,
        resume: str | None = None,
        roles: list[str] | None = None,
        seed_message: str | None = None,
    ) -> None:
        super().__init__()
        self._cfg = dict(
            mode=mode,
            model=model,
            system_prompt=system_prompt,
            session_name=session_name,
            resume=resume,
            roles=roles,
            seed_message=seed_message,
        )
        self.state: ReplState | None = None
        self._client = None
        self._client_ctx = None
        self._sink: _RichLogSink | None = None
        self._stream_state = _StreamRenderState()
        self._sdk_classes: dict[str, Any] | None = None
        self._saved_claudecode: str | None = None
        self._exit_code = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield RichLog(id="chat", markup=False, highlight=False, wrap=True, auto_scroll=True)
        yield Input(id="input", placeholder="> ")
        yield Footer()

    # ------ lifecycle ------

    async def on_mount(self) -> None:
        chat = self.query_one("#chat", RichLog)
        self._sink = _RichLogSink(chat)
        self.query_one("#input", Input).focus()
        try:
            await self._open_session()
        except Exception as exc:
            self._sink.write(f"[startup error: {exc}]\n")
            self._sink.flush()
            return
        if self._cfg["seed_message"]:
            self.run_worker(
                self._run_turn(self._cfg["seed_message"]),
                exclusive=True,
                name="sdk-turn",
            )

    async def on_unmount(self) -> None:
        try:
            if self._client_ctx is not None:
                await self._client_ctx.__aexit__(None, None, None)
        except Exception:
            # Never raise during shutdown.
            pass
        if self._saved_claudecode is not None:
            os.environ["CLAUDECODE"] = self._saved_claudecode

    async def _open_session(self) -> None:
        try:
            from claude_agent_sdk import (
                AssistantMessage,
                ClaudeAgentOptions,
                ClaudeSDKClient,
                ResultMessage,
                SystemMessage,
                UserMessage,
            )
        except ImportError as exc:
            raise RuntimeError(f"claude-agent-sdk not installed: {exc}")

        self._sdk_classes = {
            "ClaudeAgentOptions": ClaudeAgentOptions,
            "ClaudeSDKClient": ClaudeSDKClient,
            "AssistantMessage": AssistantMessage,
            "UserMessage": UserMessage,
            "SystemMessage": SystemMessage,
            "ResultMessage": ResultMessage,
        }

        # Build state.
        mode = self._cfg["mode"]
        placeholder = (RESTRICTED_TOOLS if mode == "restricted" else FULL_TOOLS).copy()
        self.state = ReplState(
            mode=mode,
            model=self._cfg["model"] or DEFAULT_MODEL,
            allowed_tools=placeholder,
        )

        # Roles + voice.
        ctx = load_session_context(Path.cwd(), role_overrides=self._cfg["roles"])
        self.state.role_names = list(ctx.role_names)
        self.state.voice = ctx.voice

        # Build options + open client. No can_use_tool yet (Phase 1C).
        options = build_options(
            ClaudeAgentOptions,
            mode,
            self._cfg["model"],
            self._cfg["system_prompt"],
            cwd=Path.cwd(),
            resume_sdk_session_id=None,
            effort=self.state.effort,
            thinking_mode=self.state.thinking_mode,
            can_use_tool=None,
            session_context=ctx,
        )
        self.state.allowed_tools = (
            list(options.allowed_tools)
            if hasattr(options, "allowed_tools")
            else list(getattr(options, "kwargs", {}).get("allowed_tools", []))
        )

        # Banner into chat.
        self._render_banner()

        # CLAUDECODE env nesting fix (mirrors _run_interactive).
        self._saved_claudecode = os.environ.pop("CLAUDECODE", None)

        # Open the SDK client.
        self._client_ctx = ClaudeSDKClient(options=options)
        self._client = await self._client_ctx.__aenter__()

    def _render_banner(self) -> None:
        assert self._sink is not None
        assert self.state is not None
        model_display = self._cfg["model"] or f"{DEFAULT_MODEL} (default)"
        self._sink.write(BANNER.format(mode=self._cfg["mode"], model=model_display))
        self._sink.write(
            "Interactive mode (Textual). Enter to send · Alt+Enter for newline · "
            "Ctrl+D to exit · Ctrl+C to cancel.\n"
            "Type /help for commands.\n"
        )
        if _mcp_enabled():
            self._sink.write(
                "agentwire MCP server attached — /tools to see what's wired in.\n"
            )
        if self.state.role_names:
            self._sink.write(f"Roles: {', '.join(self.state.role_names)}\n")
        if self.state.voice:
            self._sink.write(f"Voice: {self.state.voice}\n")
        self._sink.write("\n")
        self._sink.flush()

    # ------ input handling ------

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = (event.value or "").strip()
        # Clear input field for next turn.
        event.input.value = ""
        if not text:
            return
        assert self._sink is not None

        # Echo the user turn into chat for readability.
        for line in text.splitlines():
            self._sink.write(f"> {line}\n")
        self._sink.flush()

        if text.startswith("/"):
            action = dispatch_command(text, self.state, self._sink)
            self._sink.flush()
            if action == EXIT:
                self.exit(self._exit_code)
                return
            if action in (RESTART, RESUME):
                # Phase 1C wires the actual restart lifecycle. For now flag
                # that this isn't yet implemented so users aren't surprised.
                self._sink.write(
                    "[/clear and /resume restart the SDK session — wired in Phase 1C]\n"
                )
                self._sink.flush()
            return

        # Plain user turn — fire SDK in a worker.
        self.run_worker(
            self._run_turn(text),
            exclusive=True,
            name="sdk-turn",
        )

    def action_cancel_turn(self) -> None:
        # Cancel any in-flight worker. exclusive=True on the next run also
        # cancels, but Ctrl+C should be eager.
        worker = next(
            (w for w in self.workers if w.name == "sdk-turn" and w.is_running),
            None,
        )
        if worker is not None:
            worker.cancel()

    # ------ SDK turn worker ------

    async def _run_turn(self, text: str) -> None:
        assert self._client is not None
        assert self._sink is not None
        assert self._sdk_classes is not None
        try:
            await self._client.query(text)
            async for message in _heartbeat_iter(
                self._client.receive_response(), idle_timeout=5.0
            ):
                self.post_message(SdkEvent(message))
        except asyncio.CancelledError:
            self.post_message(TurnFinished(error="cancelled"))
            raise
        except Exception as exc:
            self.post_message(TurnFinished(error=f"{type(exc).__name__}: {exc}"))
            return
        self.post_message(TurnFinished())

    # ------ rendering on the UI thread ------

    def on_sdk_event(self, event: SdkEvent) -> None:
        assert self._sink is not None
        assert self._sdk_classes is not None
        msg = event.payload
        if msg is _HEARTBEAT:
            self._stream_state.heartbeat(self._sink)
            self._sink.flush()
            return
        try:
            render_message(
                msg,
                AssistantMessage=self._sdk_classes["AssistantMessage"],
                UserMessage=self._sdk_classes["UserMessage"],
                SystemMessage=self._sdk_classes["SystemMessage"],
                ResultMessage=self._sdk_classes["ResultMessage"],
                out=self._sink,
                stream_state=self._stream_state,
            )
            self._sink.flush()
            if isinstance(msg, self._sdk_classes["SystemMessage"]):
                track_system_init(self.state, msg)
            elif isinstance(msg, self._sdk_classes["ResultMessage"]):
                track_result(self.state, msg)
                if getattr(msg, "is_error", False):
                    self._exit_code = 1
        except Exception as exc:
            self._sink.write(f"[render error: {exc}]\n")
            self._sink.flush()

    def on_turn_finished(self, event: TurnFinished) -> None:
        assert self._sink is not None
        if event.error == "cancelled":
            self._sink.write("[turn cancelled]\n")
            self._sink.flush()
        elif event.error:
            self._sink.write(f"[turn error: {event.error}]\n")
            self._sink.flush()
        # Refocus the input so the next turn can start typing right away.
        try:
            self.query_one("#input", Input).focus()
        except Exception:
            pass


# ----- entry point ----------------------------------------------------------


async def run_textual_repl(
    *,
    mode: str = "bypass",
    model: str | None = None,
    system_prompt: str | None = None,
    session_name: str | None = None,
    resume: str | None = None,
    roles: list[str] | None = None,
    seed_message: str | None = None,
) -> int:
    app = AgentwireREPL(
        mode=mode,
        model=model,
        system_prompt=system_prompt,
        session_name=session_name,
        resume=resume,
        roles=roles,
        seed_message=seed_message,
    )
    await app.run_async()
    return app._exit_code
