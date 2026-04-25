"""Textual-based rendering layer for `agentwire repl` (Phase 2A — layout).

Layout (Phase 2A: proportional chat=6 / action=2 / input=3):

    ┌─ Header ──────────────────────────────────┐
    │                                           │
    ├─ ChatLog (RichLog, fr=6) ─────────────────┤
    │  > previous user turn                     │
    │  assistant text                           │
    │  [→ tool call]                            │
    │  [← result]                               │
    │  [done · ...]                             │
    ├─ CurrentAction (RichLog, fr=2, title) ────┤
    │  ╭─ Current action ─────────────────────╮ │
    │  │ [thinking: ...] live stream          │ │
    │  │ [writing X input · 4.2 KB live tick] │ │
    │  │ […still working · 5s]                │ │
    │  ╰──────────────────────────────────────╯ │
    ├─ Input (Input, dock=bottom) ──────────────┤
    │  > tell me about prompt caching           │
    └─ Footer ──────────────────────────────────┘

Event flow:
    user types → SubmittableTextArea posts Submitted → on_input_submitted
    → app.run_worker(_run_turn(text), exclusive=True)
    → worker iterates client.receive_response() wrapped in _heartbeat_iter
    → each message is post_message(SdkEvent(msg)) (thread-safe)
    → on_sdk_event renders via render_message(out=_RichLogSink) on UI thread

Workers MUST use `self.post_message(...)` — never call RichLog.write directly.

Phase 2A scope: split chat into chat + CurrentAction subpanes with
proportional weights. Partial-stream events (thinking, byte counter,
heartbeat) route to the action pane via a dedicated `_ActionSink` that
supports proper in-place line updates (clear-and-rewrite). The action
pane is cleared on every ResultMessage (turn complete) so the next
turn's partials start fresh. Finalized snapshot Messages stay in chat
via `_RichLogSink` as before.

The 120-second silent gap of the legacy REPL is fully solved here:
thinking and byte counter tick live in their own docked subpane, with
clean in-place updates (not the choppy multi-line emission of Phase
1B's single-sink approach).

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
from textual.widgets import Footer, Header, Input, RichLog, Static

from agentwire.repl import persistence
from agentwire.repl.app import (
    BANNER,
    DEFAULT_MODEL,
    FULL_TOOLS,
    RESTRICTED_TOOLS,
    _HEARTBEAT,
    _StreamRenderState,
    _format_tool_input,
    _heartbeat_iter,
    _mcp_enabled,
    _persist_sdk_message,
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
from agentwire.repl.mentions import expand_mentions
from agentwire.repl.state import (
    ReplState,
    reset_for_restart,
    track_result,
    track_system_init,
)


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


class _ActionSink:
    """Streaming sink for the CurrentAction subpane.

    Unlike `_RichLogSink` (chat — append-only, buffer-until-newline), the
    action pane shows live-updating content. We maintain our own list of
    "finalized" lines plus a buffer for the in-flight current line, and
    rewrite the entire RichLog on each update via clear()+write loop.

    For an action pane with O(20) lines and a turn with O(1000) deltas,
    the cost is ~20K writes — well within Textual's render budget.

    `clear()` is called from the app on each ResultMessage (turn complete)
    so the next turn's partials start with a clean pane.
    """

    _CR_CLEAR = "\r\x1b[K"

    def __init__(self, log: RichLog) -> None:
        self._log = log
        self._finalized: list[str] = []
        self._current = ""

    def write(self, s: str) -> None:
        if not s:
            return
        if self._CR_CLEAR in s:
            # \r\033[K resets the in-flight line — used by the byte counter
            # to refresh `[writing X · N KB` to a new value in place.
            s = s.rsplit(self._CR_CLEAR, 1)[1]
            self._current = ""
        self._current += s
        while "\n" in self._current:
            line, self._current = self._current.split("\n", 1)
            self._finalized.append(line)
        self._refresh()

    def flush(self) -> None:
        self._refresh()

    def isatty(self) -> bool:
        return True

    def clear(self) -> None:
        """Wipe the pane — called at end of every turn (ResultMessage)."""
        self._finalized.clear()
        self._current = ""
        try:
            self._log.clear()
        except AttributeError:
            pass

    def _refresh(self) -> None:
        try:
            self._log.clear()
        except AttributeError:
            return
        for line in self._finalized:
            if line == "":
                self._log.write("")
            else:
                self._log.write(Text.from_ansi(line))
        if self._current:
            self._log.write(Text.from_ansi(self._current))


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


# ----- StatusLine widget ----------------------------------------------------


class StatusLine(Static):
    """Single-line widget showing running totals + tunables.

    Renders one of two formats depending on whether any turns have completed:
    - pre-turn: `{mode} · {model} · effort={e} · thinking={t}`
    - post-turn: `{N turns} · {tok} tok · ${cost:.4f} · effort={e} · thinking={t}`

    `refresh_from_state(state)` is called by the App on every SdkEvent so
    the line stays current with cost/token totals + slash-command tunable
    changes (`/effort`, `/thinking`).
    """

    DEFAULT_CSS = """
    StatusLine {
        height: 1;
        padding: 0 1;
        color: $text 60%;
        background: $surface;
    }
    """

    def refresh_from_state(self, state: ReplState | None) -> None:
        if state is None:
            self.update("")
            return
        if state.turn_count == 0:
            self.update(
                f"{state.mode} · {state.model} · effort={state.effort} · "
                f"thinking={state.thinking_mode}"
            )
            return
        total = state.total_input_tokens + state.total_output_tokens
        plural = "s" if state.turn_count != 1 else ""
        self.update(
            f"{state.turn_count} turn{plural} · "
            f"{total} tok ({state.total_input_tokens} in / {state.total_output_tokens} out) · "
            f"${state.total_cost_usd:.4f} · effort={state.effort} · "
            f"thinking={state.thinking_mode}"
        )


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
        height: 6fr;
        border: tall $accent;
        padding: 0 1;
    }

    #action {
        height: 2fr;
        border: tall $warning;
        border-title-color: $warning;
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
        self._sink: _RichLogSink | None = None  # chat — finalized snapshot messages
        self._action_sink: _ActionSink | None = None  # action — live partials
        self._stream_state = _StreamRenderState()
        self._sdk_classes: dict[str, Any] | None = None
        self._saved_claudecode: str | None = None
        self._exit_code = 0
        self._transcript = None
        self._ctx = None
        # When sdk-prompted is asking for a tool decision, this holds the
        # asyncio.Future the can_use_tool callback awaits. The input handler
        # routes the next user submission as the answer instead of as a turn.
        self._pending_permission: asyncio.Future | None = None
        self._pending_tool_name: str | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield RichLog(id="chat", markup=False, highlight=False, wrap=True, auto_scroll=True)
        yield RichLog(id="action", markup=False, highlight=False, wrap=True, auto_scroll=True)
        yield StatusLine(id="status")
        yield Input(id="input", placeholder="> ")
        yield Footer()

    # ------ lifecycle ------

    async def on_mount(self) -> None:
        chat = self.query_one("#chat", RichLog)
        action = self.query_one("#action", RichLog)
        action.border_title = "Current action"
        self._sink = _RichLogSink(chat)
        self._action_sink = _ActionSink(action)
        self.query_one("#input", Input).focus()

        # Header content — set after we know the mode/model.
        self.title = "agentwire repl"
        model_short = (self._cfg["model"] or DEFAULT_MODEL).replace("claude-", "")
        self.sub_title = f"{self._cfg['mode']} · {model_short}"
        try:
            await self._open_session()
        except Exception as exc:
            self._sink.write(f"[startup error: {exc}]\n")
            self._sink.flush()
            return
        # Initial status line after state is built.
        self._refresh_status()

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
        if self._transcript is not None and self.state is not None:
            try:
                persistence.finalize(self._transcript, self.state)
                self._transcript.close()
            except Exception:
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

        # Resume lookup — find the prior session's last sdk_session_id.
        resume_sdk_session_id: str | None = None
        if self._cfg["resume"]:
            prior = persistence.load_session(self._cfg["resume"])
            if prior is None:
                raise RuntimeError(
                    f"no session named {self._cfg['resume']!r} under "
                    f"{persistence.DEFAULT_REPL_HOME}"
                )
            ids = prior.get("sdk_session_ids") or []
            if ids:
                resume_sdk_session_id = ids[-1]

        # Build state.
        mode = self._cfg["mode"]
        placeholder = (RESTRICTED_TOOLS if mode == "restricted" else FULL_TOOLS).copy()
        self.state = ReplState(
            mode=mode,
            model=self._cfg["model"] or DEFAULT_MODEL,
            allowed_tools=placeholder,
        )

        # Roles + voice.
        self._ctx = load_session_context(Path.cwd(), role_overrides=self._cfg["roles"])
        self.state.role_names = list(self._ctx.role_names)
        self.state.voice = self._ctx.voice

        # Build options + open client. can_use_tool only in sdk-prompted.
        can_use_tool = self._make_can_use_tool() if mode == "prompted" else None
        options = build_options(
            ClaudeAgentOptions,
            mode,
            self._cfg["model"],
            self._cfg["system_prompt"],
            cwd=Path.cwd(),
            resume_sdk_session_id=resume_sdk_session_id,
            effort=self.state.effort,
            thinking_mode=self.state.thinking_mode,
            can_use_tool=can_use_tool,
            session_context=self._ctx,
        )
        self.state.allowed_tools = (
            list(options.allowed_tools)
            if hasattr(options, "allowed_tools")
            else list(getattr(options, "kwargs", {}).get("allowed_tools", []))
        )

        # Transcript persistence (mirrors legacy REPL).
        self._transcript = persistence.create_session(
            mode=mode,
            model=self._cfg["model"] or DEFAULT_MODEL,
            allowed_tools=self.state.allowed_tools,
            name=self._cfg["session_name"],
        )
        self.state.session_dir = str(self._transcript.session_dir)
        self.state.transcript_name = self._transcript.name

        # Banner into chat.
        self._render_banner()
        if resume_sdk_session_id:
            self._sink.write(
                f"Resuming {self._cfg['resume']!r} "
                f"(sdk session {resume_sdk_session_id[:8]}…)\n"
            )
        self._sink.write(f"[transcript → {self._transcript.session_dir}]\n\n")
        self._sink.flush()

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

    def _make_can_use_tool(self):
        """Permission callback for `sdk-prompted` mode.

        Phase 1C placeholder — surfaces a `[? allow Tool args? (y/n/a)]`
        line in the chat log, parks an asyncio.Future, and routes the next
        user submission as the answer instead of as a turn. Phase 2C
        replaces this with a centered `ModalScreen`.
        """
        async def _can_use_tool(tool_name: str, tool_input: dict, ctx: Any):
            from claude_agent_sdk import (
                PermissionResultAllow,
                PermissionResultDeny,
            )

            if tool_name in self.state.always_allow_tools:
                return PermissionResultAllow()

            summary = _format_tool_input(tool_name, tool_input or {})
            line = f"[? allow {tool_name}{' ' + summary if summary else ''}? (y/n/a=always)]"
            self._sink.write(line + "\n")
            self._sink.flush()

            loop = asyncio.get_event_loop()
            future: asyncio.Future = loop.create_future()
            self._pending_permission = future
            self._pending_tool_name = tool_name
            try:
                answer = await future
            except asyncio.CancelledError:
                return PermissionResultDeny(message="user denied (cancelled)")
            finally:
                self._pending_permission = None
                self._pending_tool_name = None

            answer = (answer or "").strip().lower()
            if answer in ("a", "always"):
                self.state.always_allow_tools.add(tool_name)
                self._sink.write(
                    f"[allow · {tool_name} now always allowed this session]\n"
                )
                self._sink.flush()
                return PermissionResultAllow()
            if answer in ("", "y", "yes"):
                return PermissionResultAllow()
            self._sink.write(f"[deny · {tool_name}]\n")
            self._sink.flush()
            return PermissionResultDeny(message="user denied")

        return _can_use_tool

    async def _restart_session(self) -> None:
        """Close+reopen the SDK client for /clear, /resume, /effort, /thinking."""
        assert self.state is not None
        assert self._sdk_classes is not None

        next_resume_id = self.state.pending_resume_sdk_session_id
        self.state.pending_resume_sdk_session_id = None

        # Close current client first.
        try:
            if self._client_ctx is not None:
                await self._client_ctx.__aexit__(None, None, None)
        except Exception:
            pass
        self._client = None
        self._client_ctx = None

        # Rebuild options with possibly-new effort/thinking/resume.
        can_use_tool = (
            self._make_can_use_tool() if self._cfg["mode"] == "prompted" else None
        )
        options = build_options(
            self._sdk_classes["ClaudeAgentOptions"],
            self._cfg["mode"],
            self._cfg["model"],
            self._cfg["system_prompt"],
            cwd=Path.cwd(),
            resume_sdk_session_id=next_resume_id,
            effort=self.state.effort,
            thinking_mode=self.state.thinking_mode,
            can_use_tool=can_use_tool,
            session_context=self._ctx,
        )

        # Record the restart.
        if self._transcript is not None:
            self._transcript.write_event({
                "type": "restart",
                "resume_sdk_session_id": next_resume_id,
            })
        reset_for_restart(self.state)

        # Reopen.
        self._client_ctx = self._sdk_classes["ClaudeSDKClient"](options=options)
        self._client = await self._client_ctx.__aenter__()

    # ------ input handling ------

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = (event.value or "").strip()
        event.input.value = ""

        # Permission-pending branch: if sdk-prompted is awaiting a y/n/a
        # answer, the next submission is the answer (not a slash command,
        # not a turn).
        if self._pending_permission is not None and not self._pending_permission.done():
            self._pending_permission.set_result(text)
            return

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
            # /effort and /thinking mutate state — reflect immediately.
            self._refresh_status()
            if action == EXIT:
                self.exit(self._exit_code)
                return
            if action in (RESTART, RESUME):
                self.run_worker(
                    self._restart_session(),
                    exclusive=True,
                    name="sdk-restart",
                )
            return

        # Expand @path mentions before sending (mirrors legacy REPL).
        expanded_text, expansions = expand_mentions(text, cwd=Path.cwd())
        if expansions:
            count = len(expansions)
            self._sink.write(
                f"[expanded {count} mention"
                f"{'s' if count != 1 else ''}: "
                f"{', '.join(e.raw for e in expansions)}]\n"
            )
            self._sink.flush()

        # Persist the user turn before firing the SDK.
        if self._transcript is not None:
            event_data: dict = {"type": "user_input", "text": text}
            if expansions:
                event_data["expanded_text"] = expanded_text
                event_data["mentions"] = [
                    {"raw": e.raw, "target": e.target} for e in expansions
                ]
            self._transcript.write_event(event_data)

        self.run_worker(
            self._run_turn(expanded_text),
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
        assert self._action_sink is not None
        assert self._sdk_classes is not None
        msg = event.payload
        if msg is _HEARTBEAT:
            # Heartbeats live in the action pane (live indicator).
            self._stream_state.heartbeat(self._action_sink)
            self._action_sink.flush()
            return
        try:
            render_message(
                msg,
                AssistantMessage=self._sdk_classes["AssistantMessage"],
                UserMessage=self._sdk_classes["UserMessage"],
                SystemMessage=self._sdk_classes["SystemMessage"],
                ResultMessage=self._sdk_classes["ResultMessage"],
                out=self._sink,
                action_out=self._action_sink,
                stream_state=self._stream_state,
            )
            self._sink.flush()
            self._action_sink.flush()
            if self._transcript is not None:
                _persist_sdk_message(
                    msg,
                    self._transcript,
                    self._sdk_classes["AssistantMessage"],
                    self._sdk_classes["UserMessage"],
                    self._sdk_classes["SystemMessage"],
                    self._sdk_classes["ResultMessage"],
                )
            if isinstance(msg, self._sdk_classes["SystemMessage"]):
                track_system_init(self.state, msg)
                if self.state.session_id and self._transcript is not None:
                    persistence.record_session_id(
                        self._transcript, self.state.session_id
                    )
            elif isinstance(msg, self._sdk_classes["ResultMessage"]):
                track_result(self.state, msg)
                if getattr(msg, "is_error", False):
                    self._exit_code = 1
                # Turn complete — clear the action pane so the next turn's
                # partials start fresh.
                self._action_sink.clear()
            self._refresh_status()
        except Exception as exc:
            self._sink.write(f"[render error: {exc}]\n")
            self._sink.flush()

    def _refresh_status(self) -> None:
        try:
            self.query_one("#status", StatusLine).refresh_from_state(self.state)
        except Exception:
            pass

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
