"""Agentwire REPL application entry point.

- Print mode: one-shot SDK call, stream events, exit (single-shot stdout
  pipe — used by `agentwire workflow run`, scheduler tasks, `human_gate`
  seeds; never moves to Textual).
- Interactive: Textual TUI implemented in `agentwire/repl/textual_app.py`.
  This file holds the print-mode helper, the SDK options builder, the
  message renderer (used by both surfaces), and supporting utilities.

Slash commands live in `commands.py`; transcript persistence lives in
`persistence.py`.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from agentwire.repl import persistence
from agentwire.repl.context import SessionContext, load_session_context
from agentwire.repl.damage_control import make_pre_tool_hook
from agentwire.repl.state import ReplState
from agentwire.workflows.runners.sdk_errors import classify as _classify_sdk_error


BANNER = """\
╭─────────────────────────────────────────────────────────╮
│  agentwire repl — Anthropic SDK harness                 │
│  mode={mode} · model={model}
╰─────────────────────────────────────────────────────────╯
"""


PERMISSION_MODE_MAP = {
    "bypass": "bypassPermissions",
    "prompted": "default",
    "restricted": "plan",
}

# Tool surface per variant. Restricted is read-only (no Write/Edit/Bash).
FULL_TOOLS = ["Read", "Write", "Edit", "Bash", "Grep", "Glob", "WebFetch", "WebSearch"]
RESTRICTED_TOOLS = ["Read", "Grep", "Glob", "WebFetch", "WebSearch"]

DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_EFFORT = "high"
DEFAULT_THINKING_MODE = "adaptive"

# Agentwire MCP server, auto-attached to every REPL session. The differentiator
# we promise in docs/missions/agentwire-repl.md Phase 3: ~87 agentwire tools
# (panes, voice, scheduler, channels, etc.) available first-class without any
# per-session configuration. Set AGENTWIRE_REPL_MCP=0 to opt out.
MCP_SERVER_NAME = "agentwire"
MCP_TOOL_PREFIX = f"mcp__{MCP_SERVER_NAME}"


def _agentwire_mcp_config() -> dict:
    """Stdio MCP server config that re-invokes this same Python interpreter.

    Using sys.executable + `-m agentwire mcp` (rather than spawning the
    `agentwire` console script) keeps the REPL and the MCP server pinned to
    the exact same install. Avoids a second `agentwire` on PATH starting a
    different version mid-session.
    """
    return {
        "type": "stdio",
        "command": sys.executable,
        "args": ["-m", "agentwire", "mcp"],
    }


def _mcp_enabled() -> bool:
    return os.environ.get("AGENTWIRE_REPL_MCP", "1") != "0"


def _damage_control_enabled() -> bool:
    """`AGENTWIRE_REPL_DAMAGE_CONTROL=0` opts out (used by tests)."""
    return os.environ.get("AGENTWIRE_REPL_DAMAGE_CONTROL", "1") != "0"


def _thinking_config(mode: str) -> dict | None:
    """Translate the REPL's `/thinking` mode into an SDK thinking config.

    `adaptive` defaults to `display: "summarized"` so users see reasoning
    progress rather than a long silent pause — Opus 4.7 omits thinking
    content by default, which made the REPL look frozen during a long
    write-the-whole-html-file turn (real user report 2026-04-25). Set
    `/thinking off` to disable thinking entirely; there's no separate
    "adaptive but hidden" mode anymore — wasn't useful in practice.
    """
    if mode == "adaptive":
        return {"type": "adaptive", "display": "summarized"}
    if mode == "summarized":
        return {"type": "adaptive", "display": "summarized"}
    if mode == "off":
        return {"type": "disabled"}
    return {"type": "adaptive", "display": "summarized"}


def run_repl(
    mode: str = "bypass",
    model: str | None = None,
    print_prompt: str | None = None,
    system_prompt: str | None = None,
    session_name: str | None = None,
    resume: str | None = None,
    roles: list[str] | None = None,
    seed_message: str | None = None,
) -> int:
    """Run the REPL. Returns exit code.

    - Print mode (`-p PROMPT`): single-shot stdout pipe — one SDK call,
      stream events, exit. Used by `agentwire workflow run`, scheduler
      tasks, and `human_gate` seeds where a TUI would interfere.
    - Interactive: Textual TUI under `~/.agentwire/sessions/repl/<name>/`
      with full transcript persistence.

    `roles` overrides the role list from `.agentwire.yml` for this session;
    when None, project config wins.

    `seed_message` is sent as the first user turn before the prompt loop
    starts — used by the workflow human_gate runner to pre-load context.
    Has no effect in print mode (use `print_prompt` instead).
    """
    if print_prompt is not None:
        return asyncio.run(_run_print_mode(print_prompt, mode, model, system_prompt, roles))
    from agentwire.repl.textual_app import run_textual_repl
    return asyncio.run(run_textual_repl(
        mode=mode, model=model, system_prompt=system_prompt,
        session_name=session_name, resume=resume, roles=roles,
        seed_message=seed_message,
    ))


# -------- print mode (SDK-backed) --------


async def _run_print_mode(
    prompt: str,
    mode: str,
    model: str | None,
    system_prompt: str | None,
    roles: list[str] | None = None,
) -> int:
    try:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ClaudeSDKClient,
            ResultMessage,
            SystemMessage,
            UserMessage,
        )
    except ImportError as e:
        print(f"error: claude-agent-sdk not installed: {e}", file=sys.stderr)
        return 1

    ctx = load_session_context(Path.cwd(), role_overrides=roles)
    if ctx.missing_roles:
        sys.stderr.write(f"[repl: roles not found: {', '.join(ctx.missing_roles)}]\n")
    options = build_options(
        ClaudeAgentOptions, mode, model, system_prompt, cwd=Path.cwd(),
        session_context=ctx,
    )

    # claude-agent-sdk refuses to nest — same CLAUDECODE unset as the workflow runner.
    saved_claudecode = os.environ.pop("CLAUDECODE", None)
    exit_code = 0
    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            stream_state = _StreamRenderState()
            async for message in _heartbeat_iter(
                client.receive_response(), idle_timeout=5.0,
            ):
                if message is _HEARTBEAT:
                    stream_state.heartbeat(sys.stdout)
                    continue
                try:
                    render_message(
                        message,
                        AssistantMessage=AssistantMessage,
                        UserMessage=UserMessage,
                        SystemMessage=SystemMessage,
                        ResultMessage=ResultMessage,
                        out=sys.stdout,
                        stream_state=stream_state,
                    )
                    if isinstance(message, ResultMessage) and getattr(message, "is_error", False):
                        exit_code = 1
                except Exception as exc:
                    print(f"[repl: render error: {exc}]", file=sys.stderr)
    except Exception as exc:
        print(f"[repl: {type(exc).__name__}: {exc}]", file=sys.stderr)
        return 1
    finally:
        if saved_claudecode is not None:
            os.environ["CLAUDECODE"] = saved_claudecode
    return exit_code


def build_options(
    ClaudeAgentOptions: Any,
    mode: str,
    model: str | None,
    system_prompt: str | None,
    cwd: Path | None = None,
    resume_sdk_session_id: str | None = None,
    effort: str = DEFAULT_EFFORT,
    thinking_mode: str = DEFAULT_THINKING_MODE,
    can_use_tool: Any = None,
    session_context: SessionContext | None = None,
) -> Any:
    """Compose `ClaudeAgentOptions` for a REPL session.

    Permission mode + tool surface are derived from the session-type variant
    (`sdk-bypass`/`sdk-prompted`/`sdk-restricted`). System prompt layers project
    CLAUDE.md + AGENTS.md + an optional explicit append. `resume_sdk_session_id`
    passes through to the SDK's `resume` field to continue a prior conversation.
    """
    base_tools = RESTRICTED_TOOLS if mode == "restricted" else FULL_TOOLS
    allowed = list(base_tools)
    mcp_servers: dict[str, Any] = {}
    if _mcp_enabled():
        mcp_servers[MCP_SERVER_NAME] = _agentwire_mcp_config()
        # `mcp__<server>` allows all tools from that server in claude-agent-sdk's
        # tool gating (same convention Claude Code uses).
        allowed.append(MCP_TOOL_PREFIX)

    kwargs: dict[str, Any] = {
        "model": model or DEFAULT_MODEL,
        "permission_mode": PERMISSION_MODE_MAP.get(mode, "bypassPermissions"),
        "allowed_tools": allowed,
        "setting_sources": ["user"],       # load ~/.claude/hooks — damage-control
        # Partial messages stream incremental thinking text and tool input
        # as it's generated. Without this, an Opus 4.7 turn that writes a
        # 10KB HTML file inside a Write tool input shows nothing for ~120s
        # between [→ Write ...] and the final result. With it, the user
        # sees thinking summaries and tool input streaming in real time.
        "include_partial_messages": True,
        "effort": effort,
        "thinking": _thinking_config(thinking_mode),
    }
    if mcp_servers:
        kwargs["mcp_servers"] = mcp_servers
    if cwd is not None:
        kwargs["cwd"] = str(cwd)
    if resume_sdk_session_id:
        kwargs["resume"] = resume_sdk_session_id
    if can_use_tool is not None:
        kwargs["can_use_tool"] = can_use_tool

    # Phase 3 PR 2 — Python-side damage control. Mirrors the shell hooks at
    # ~/.agentwire/hooks/damage-control/*.py, but runs in-process via the
    # SDK's PreToolUse callback so direct SDK tool dispatch can't bypass it.
    if _damage_control_enabled():
        try:
            from claude_agent_sdk import HookMatcher
        except ImportError:
            HookMatcher = None  # SDK absent → render path will refuse anyway
        if HookMatcher is not None:
            hook = make_pre_tool_hook(mode=mode)
            if hook is not None:
                kwargs["hooks"] = {
                    "PreToolUse": [
                        HookMatcher(
                            matcher="Bash|Edit|MultiEdit|Write",
                            hooks=[hook],
                        )
                    ]
                }

    append_parts: list[str] = []
    # Roles first — they're identity and tool-permission posture, so they
    # frame everything that follows. Then CLAUDE.md / AGENTS.md (project
    # facts), then explicit override.
    if session_context is not None and session_context.role_instructions:
        append_parts.append(
            f"--- roles: {', '.join(session_context.role_names)} ---\n"
            f"{session_context.role_instructions}"
        )
    if cwd is not None:
        for name in ("CLAUDE.md", "AGENTS.md"):
            found = _find_ancestor_file(cwd, name)
            if found is not None:
                try:
                    append_parts.append(f"--- {found} ---\n{found.read_text()}")
                except Exception:
                    pass
    if system_prompt:
        append_parts.append(system_prompt)

    if append_parts:
        kwargs["system_prompt"] = {
            "type": "preset",
            "preset": "claude_code",
            "append": "\n\n".join(append_parts),
        }

    return ClaudeAgentOptions(**kwargs)


def _find_ancestor_file(start: Path, name: str) -> Path | None:
    for ancestor in [start, *start.parents]:
        candidate = ancestor / name
        if candidate.is_file():
            return candidate
    return None


def _persist_sdk_message(
    message: Any,
    transcript: Any,
    AssistantMessage: Any,
    UserMessage: Any,
    SystemMessage: Any,
    ResultMessage: Any,
) -> None:
    """Translate one SDK message to event shape and append to the transcript.

    Reuses `agentwire.workflows.runners.anthropic_events` so REPL events and
    workflow events share vocabulary (portal history window can render both
    without a separate codepath).
    """
    try:
        from agentwire.workflows.runners import anthropic_events as ev
    except ImportError:
        return

    if isinstance(message, SystemMessage) and getattr(message, "subtype", None) == "init":
        for event in ev.translate_system_init(message):
            transcript.write_event(event)
    elif isinstance(message, AssistantMessage):
        transcript.write_event(ev.translate_assistant(message))
    elif isinstance(message, UserMessage):
        transcript.write_event(ev.translate_user(message))
    elif isinstance(message, ResultMessage):
        for event in ev.translate_result(message):
            transcript.write_event(event)


# -------- event rendering --------


def render_message(
    message: Any,
    AssistantMessage: Any,
    UserMessage: Any,
    SystemMessage: Any,
    ResultMessage: Any,
    out: Any = sys.stdout,
    stream_state: "_StreamRenderState | None" = None,
    action_out: Any = None,
) -> None:
    """Render one SDK message to the terminal.

    Compact, human-readable output matching the spirit of pi's JSONL but for
    a terminal reader. Tools like `Read`/`Bash` show the target; tool results
    show a one-line preview.

    `stream_state` carries cross-message bookkeeping for the partial-message
    stream: when partial events have already streamed text/thinking content
    for an assistant turn, the final AssistantMessage that arrives at
    message_stop would otherwise re-render everything. We track which
    content indices were streamed and skip them.

    `action_out` (Phase 2A, 2026-04-25): optional separate sink for partial-
    stream events. The Textual REPL passes a dedicated CurrentAction RichLog
    here so live thinking, byte counters, and heartbeats render in their own
    docked subpane while finalized snapshot messages go to the chat pane via
    `out`. When `action_out` is None or the same object as `out`, the legacy
    behavior is preserved: everything goes to one sink (stdout in the line-
    mode REPL).
    """
    # If no separate action sink is provided, partials and snapshots share `out`.
    if action_out is None:
        action_out = out
    dual_panes = action_out is not out

    # StreamEvent is a `@dataclass` with fields {uuid, session_id, event,
    # parent_tool_use_id}, delivered when include_partial_messages=True. The
    # `event` payload mirrors Anthropic's streaming API. We render
    # thinking_delta + text_delta inline so users see reasoning + reply
    # assembling in real time. Other event types (input_json_delta,
    # message_delta, message_stop) are noise here.
    #
    # Detection by duck-type rather than isinstance — keeps render_message
    # decoupled from claude-agent-sdk's exact class (and matches the rest
    # of the renderer's pattern of accepting structurally-typed fakes).
    if hasattr(message, "event") and hasattr(message, "uuid") and not hasattr(message, "content"):
        if stream_state is not None:
            payload = getattr(message, "event", None)
            if isinstance(payload, dict):
                stream_state.handle_partial(payload, action_out)
        return

    # Any non-partial message arriving — close out a pending heartbeat line so
    # the upcoming render starts on a clean line. Heartbeats live in the
    # action sink; close them there.
    if stream_state is not None:
        stream_state._consume_heartbeat(action_out)

    if isinstance(message, SystemMessage):
        if getattr(message, "subtype", None) == "init":
            data = getattr(message, "data", {}) or {}
            model = data.get("model", "") or ""
            sid = (data.get("session_id") or data.get("sessionId") or "")[:8]
            parts = [p for p in [model, f"session {sid}" if sid else ""] if p]
            out.write(_styled(out, f"[agent started · {' · '.join(parts)}]", "dim cyan") + "\n")
        return

    if isinstance(message, AssistantMessage):
        # Close any open partial line so the action pane is left clean.
        # Partials live in `action_out`; close them there.
        if stream_state is not None and stream_state.partials_active:
            stream_state.close_open_block(action_out)
        for block in getattr(message, "content", []) or []:
            btype = _block_type(block)
            if btype == "text":
                # In single-pane mode, skip snapshot if we already streamed
                # the same content. In dual-pane mode, the snapshot lives in
                # chat (out) which is permanent — always render it there
                # regardless of what landed in the ephemeral action pane.
                if (
                    not dual_panes
                    and stream_state is not None
                    and stream_state.streamed_text
                ):
                    continue
                text = _block_attr(block, "text", "") or ""
                if text:
                    out.write(text)
                    if not text.endswith("\n"):
                        out.write("\n")
            elif btype == "tool_use":
                name = _block_attr(block, "name", "") or ""
                tool_id = _block_attr(block, "id", "") or ""
                inp = _block_attr(block, "input", {}) or {}
                summary = _format_tool_input(name, inp)
                # Tool-call collapse: when a stream_state is provided and we
                # have a tool_id, defer writing this line — the matching
                # tool_result will fold them into one `[Tool · args · preview]`.
                # No id (or no stream_state) → render as before.
                if stream_state is not None and tool_id:
                    stream_state.pending_tool_uses[tool_id] = {
                        "name": name,
                        "summary": summary,
                    }
                else:
                    line = f"[→ {name}{' ' + summary if summary else ''}]"
                    out.write(_styled(out, line, "bold cyan") + "\n")
            elif btype == "thinking":
                # Same dual-pane logic as text — chat keeps the permanent
                # snapshot even though the action pane streamed live.
                if (
                    not dual_panes
                    and stream_state is not None
                    and stream_state.streamed_thinking
                ):
                    continue
                thinking = _block_attr(block, "thinking", "") or ""
                first = thinking.split("\n", 1)[0].strip()
                if first:
                    preview = first if len(first) <= 80 else first[:77] + "..."
                    out.write(_styled(out, f"[thinking: {preview}]", "dim") + "\n")
        if stream_state is not None:
            stream_state.reset_for_next_assistant_turn()
        return

    if isinstance(message, UserMessage):
        content = getattr(message, "content", None)
        if isinstance(content, list):
            for block in content:
                btype = _block_type(block)
                if btype == "tool_result":
                    result_content = _block_attr(block, "content", None)
                    preview = _format_tool_result(result_content)
                    is_err = bool(_block_attr(block, "is_error", False))
                    tool_use_id = _block_attr(block, "tool_use_id", "") or ""

                    # Tool-call collapse: if we deferred the matching
                    # tool_use, fold it into this line.
                    pending = None
                    if stream_state is not None and tool_use_id:
                        pending = stream_state.pending_tool_uses.pop(
                            tool_use_id, None
                        )

                    if pending is not None and not is_err:
                        # Merged: [Tool · args · preview]
                        name = pending["name"]
                        summary = pending["summary"]
                        parts = [name]
                        if summary:
                            parts.append(summary)
                        if preview:
                            parts.append(preview)
                        line = f"[{' · '.join(parts)}]"
                        out.write(_styled(out, line, "cyan") + "\n")
                    elif pending is not None and is_err:
                        # Error result with deferred tool_use → emit both lines
                        # so the user sees the call and the error clearly.
                        name = pending["name"]
                        summary = pending["summary"]
                        call = f"[→ {name}{' ' + summary if summary else ''}]"
                        out.write(_styled(out, call, "bold cyan") + "\n")
                        out.write(_styled(out, f"[← error: {preview}]", "red") + "\n")
                    else:
                        # Standalone result (no matching deferred tool_use).
                        style = "red" if is_err else "green"
                        label = "error" if is_err else "result"
                        out.write(
                            _styled(out, f"[← {label}: {preview}]", style) + "\n"
                        )
        return

    if isinstance(message, ResultMessage):
        # Flush any tool_uses whose results never arrived this turn.
        if stream_state is not None and stream_state.pending_tool_uses:
            stream_state.flush_pending_tool_uses(out)
        usage = getattr(message, "usage", {}) or {}
        cost = getattr(message, "total_cost_usd", None)
        duration = getattr(message, "duration_ms", None)
        parts: list[str] = []
        if isinstance(usage, dict):
            in_tok = usage.get("input_tokens", 0) or 0
            out_tok = usage.get("output_tokens", 0) or 0
            if in_tok or out_tok:
                parts.append(f"{in_tok}+{out_tok} tok")
        if cost is not None:
            parts.append(f"${cost:.4f}")
        if duration is not None:
            parts.append(f"{duration / 1000:.1f}s")
        suffix = f" · {' · '.join(parts)}" if parts else ""
        if getattr(message, "is_error", False):
            err = getattr(message, "result", None) or "unknown error"
            category = _classify_sdk_error("ResultMessage", str(err))
            out.write(_styled(out, f"[error · {category}{suffix}] {err}", "bold red") + "\n")
        else:
            out.write(_styled(out, f"[done{suffix}]", "dim green") + "\n")
        return


def _block_type(block: Any) -> str:
    if hasattr(block, "type"):
        return getattr(block, "type") or ""
    if isinstance(block, dict):
        return block.get("type", "")
    return {
        "TextBlock": "text",
        "ToolUseBlock": "tool_use",
        "ThinkingBlock": "thinking",
        "ToolResultBlock": "tool_result",
    }.get(type(block).__name__, "")


def _block_attr(block: Any, name: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)


def _format_tool_input(name: str, inp: dict) -> str:
    if not isinstance(inp, dict):
        return ""
    if name in ("Read", "Write", "Edit"):
        fp = inp.get("file_path", "")
        if fp:
            return str(fp)
    if name == "Bash":
        cmd = inp.get("command", "") or ""
        if cmd:
            return cmd if len(cmd) <= 80 else cmd[:77] + "..."
    if name in ("Grep", "Glob"):
        return str(inp.get("pattern", "") or "")
    if name == "WebFetch":
        return str(inp.get("url", "") or "")
    if name == "WebSearch":
        return str(inp.get("query", "") or "")
    rendered = str(inp)
    return rendered if len(rendered) <= 80 else rendered[:77] + "..."


def _format_tool_result(content: Any) -> str:
    if content is None:
        return "(no content)"
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = ""
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                text = b.get("text", "") or ""
                break
            if hasattr(b, "text"):
                text = str(getattr(b, "text", "") or "")
                break
        if not text:
            text = str(content)
    else:
        text = str(content)
    text = text.replace("\n", " ")
    return text if len(text) <= 120 else text[:117] + "..."



# -------- streaming visibility (2026-04-25) --------

# Sentinel yielded by _heartbeat_iter when the inner async iter has stalled
# past idle_timeout. The render loop watches for this and prints a heartbeat.
_HEARTBEAT = object()


async def _heartbeat_iter(async_iter, idle_timeout: float):
    """Yield items from `async_iter`; yield `_HEARTBEAT` when idle.

    `client.receive_response()` is an async iterator that may sit silent for
    tens of seconds while the model thinks or writes a long tool input.
    On each idle timeout we yield `_HEARTBEAT` so the renderer can show
    progress, but we keep the underlying `__anext__` task pending — using
    `asyncio.wait_for` would cancel it and lose the pending event.
    """
    iterator = async_iter.__aiter__()
    pending: asyncio.Task | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(iterator.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=idle_timeout)
            if pending in done:
                try:
                    yield pending.result()
                except StopAsyncIteration:
                    pending = None
                    return
                pending = None
            else:
                yield _HEARTBEAT
    finally:
        if pending is not None and not pending.done():
            pending.cancel()


def _flush(out: Any) -> None:
    """Best-effort flush — StringIO has flush, real stdout has flush, fakes may not."""
    flush = getattr(out, "flush", None)
    if callable(flush):
        flush()


# --- Color / style ----------------------------------------------------------
#
# Visual hierarchy goal: the user's eye should land on the assistant's actual
# answer first; everything bracketed is metadata and should recede. We use
# Rich for styling because:
#   1. It's the rendering layer Textual is built on, so the markup we write
#      here translates 1:1 to RichLog content when we do the Textual rewrite
#      (mission: docs/missions/agentwire-repl-textual.md).
#   2. It handles TTY detection, NO_COLOR, color-system fallback, etc., so we
#      don't have to.
#
# Style scheme (Rich style strings — translate to Textual identically):
#   thinking      → dim                (secondary; reasoning noise)
#   tool_progress → dim yellow         (in-flight; "byte counter ticking")
#   tool_done     → cyan               (closed; "wrote N KB")
#   tool_call     → bold cyan          ([→ Tool args] — the action)
#   tool_result   → green              ([← result: ...])
#   heartbeat     → dim                ([…still working · 5s])
#   agent_meta    → dim cyan           ([agent started · ...])
#   done          → dim green          ([done · tok · cost · time])
#   error         → bold red           ([error · ...])
#
# Assistant text + thinking content stay uncolored — default fg is the
# brightest, which is what we want for the actual reading material.

from io import StringIO as _StyleStringIO

try:
    from rich.console import Console as _RichConsole

    _STYLE_BUF = _StyleStringIO()
    _STYLE_CONSOLE = _RichConsole(
        file=_STYLE_BUF,
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
        markup=True,
        emoji=False,
        soft_wrap=True,
    )
    _RICH_AVAILABLE = True
except ImportError:  # pragma: no cover — rich is a required dep but stay safe
    _RICH_AVAILABLE = False


def _ansi_pair(style: str) -> tuple[str, str]:
    """Return `(open, close)` ANSI sequences for a Rich style string.

    Cached implicitly via the dict below — the renderer hits the same handful
    of styles thousands of times per turn.
    """
    if not _RICH_AVAILABLE or not style:
        return ("", "")
    cached = _STYLE_CACHE.get(style)
    if cached is not None:
        return cached
    _STYLE_BUF.seek(0)
    _STYLE_BUF.truncate()
    # Sentinel \x00 splits open codes from close codes in Rich's output.
    _STYLE_CONSOLE.print(f"[{style}]\x00[/{style}]", end="")
    raw = _STYLE_BUF.getvalue()
    open_, _sep, close = raw.partition("\x00")
    pair = (open_, close)
    _STYLE_CACHE[style] = pair
    return pair


_STYLE_CACHE: dict[str, tuple[str, str]] = {}


def _styled(out: Any, text: str, style: str) -> str:
    """Wrap `text` in ANSI codes for `style` if `out` is a TTY, else plain."""
    if not style:
        return text
    if not getattr(out, "isatty", lambda: False)():
        return text
    open_, close = _ansi_pair(style)
    return f"{open_}{text}{close}"


def _open(out: Any, style: str) -> str:
    """Open ANSI for `style` if `out` is a TTY (used to span multiple writes)."""
    if not style or not getattr(out, "isatty", lambda: False)():
        return ""
    return _ansi_pair(style)[0]


def _close(out: Any, style: str) -> str:
    """Close ANSI for `style` (matches `_open`)."""
    if not style or not getattr(out, "isatty", lambda: False)():
        return ""
    return _ansi_pair(style)[1]


def _format_bytes(n: int) -> str:
    """Human-readable byte count: `42 bytes`, `1.2 KB`, `3.4 MB`."""
    if n < 1024:
        return f"{n} bytes"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


class _StreamRenderState:
    """Per-turn bookkeeping for partial-message rendering.

    The SDK delivers StreamEvents (TypedDict with a `uuid` + `event` payload)
    when `include_partial_messages=True`. We render thinking_delta and
    text_delta deltas inline so the user sees progress; the snapshot
    AssistantMessage at message_stop then skips re-rendering anything we
    already showed.
    """

    def __init__(self) -> None:
        self.streamed_text = False
        self.streamed_thinking = False
        self.open_block: str | None = None  # "thinking" | "text" | "tool_use" | None
        self._heartbeat_count = 0
        self._heartbeat_started_inline = False
        self._tool_use_name: str | None = None
        self._tool_use_bytes: int = 0
        # Tool-call collapse (Phase 2C): deferred [→ Tool args] writes,
        # keyed by tool_use_id. Folded into one line when the matching
        # tool_result arrives. Unmatched at end of turn → flushed as-is.
        self.pending_tool_uses: dict[str, dict] = {}

    @property
    def partials_active(self) -> bool:
        return self.streamed_text or self.streamed_thinking or self.open_block is not None

    def handle_partial(self, event: dict, out: Any) -> None:
        """Render one StreamEvent.event payload."""
        if not isinstance(event, dict):
            return
        etype = event.get("type")
        # Heartbeat may have left a `[…still working]` line that we want to
        # consume before showing real content.
        if etype in ("content_block_start", "content_block_delta", "message_delta"):
            self._consume_heartbeat(out)

        if etype == "content_block_start":
            block = event.get("content_block") or {}
            btype = block.get("type")
            if btype == "thinking":
                # Whole thinking block is dim — the bracket marker AND the
                # streamed reasoning text inside. ANSI styling is sticky
                # across writes until we emit the close on content_block_stop.
                out.write(_open(out, "dim") + "[thinking: ")
                self.open_block = "thinking"
                self.streamed_thinking = True
            elif btype == "text":
                # Newline before assistant text so it doesn't pile onto the
                # previous line (often a tool result preview). No style — the
                # answer is the brightest thing on screen by default.
                out.write("\n")
                self.open_block = "text"
                self.streamed_text = True
            elif btype == "tool_use":
                # Long tool inputs (e.g. Write with 10KB of HTML) used to
                # generate in silence for ~100s. Show a live byte counter so
                # the user knows the model is still working. We don't dump
                # the raw JSON bytes — the snapshot AssistantMessage that
                # follows gives the formatted [→ Write file.html] summary.
                name = block.get("name") or "tool"
                self._tool_use_name = name
                self._tool_use_bytes = 0
                self.open_block = "tool_use"
                out.write(
                    _open(out, "dim yellow")
                    + f"[writing {name} input · 0 bytes"
                )
                _flush(out)

        elif etype == "content_block_delta":
            delta = event.get("delta") or {}
            dtype = delta.get("type")
            if dtype == "thinking_delta" and self.open_block == "thinking":
                text = delta.get("thinking", "") or ""
                if text:
                    # Collapse newlines inside thinking to keep the line flowing.
                    # Style is already opened via _open — just write text.
                    out.write(text.replace("\n", " "))
                    _flush(out)
            elif dtype == "text_delta" and self.open_block == "text":
                out.write(delta.get("text", "") or "")
                _flush(out)
            elif dtype == "input_json_delta" and self.open_block == "tool_use":
                partial = delta.get("partial_json") or ""
                self._tool_use_bytes += len(partial)
                # Refresh the line in place via CR+clear-to-EOL so the byte
                # count ticks live without spamming new lines. Re-open the
                # style each time because \033[K clears any prior ANSI state.
                out.write(
                    "\r\033[K"
                    + _open(out, "dim yellow")
                    + f"[writing {self._tool_use_name} input · "
                    + _format_bytes(self._tool_use_bytes)
                )
                _flush(out)

        elif etype == "content_block_stop":
            if self.open_block == "thinking":
                out.write("]" + _close(out, "dim") + "\n")
            elif self.open_block == "text":
                out.write("\n")
            elif self.open_block == "tool_use":
                # Closed counter switches from dim-yellow (in-flight) to cyan
                # (settled) — visual signal the write phase is done.
                out.write(
                    "\r\033[K"
                    + _styled(
                        out,
                        f"[wrote {self._tool_use_name} input · "
                        f"{_format_bytes(self._tool_use_bytes)}]",
                        "cyan",
                    )
                    + "\n"
                )
                self._tool_use_name = None
                self._tool_use_bytes = 0
            self.open_block = None

    def close_open_block(self, out: Any) -> None:
        """Force-close any in-flight open block (e.g. before snapshot render)."""
        if self.open_block == "thinking":
            out.write("]" + _close(out, "dim") + "\n")
        elif self.open_block == "text":
            out.write("\n")
        elif self.open_block == "tool_use":
            out.write(
                "\r\033[K"
                + _styled(
                    out,
                    f"[wrote {self._tool_use_name} input · "
                    f"{_format_bytes(self._tool_use_bytes)}]",
                    "cyan",
                )
                + "\n"
            )
            self._tool_use_name = None
            self._tool_use_bytes = 0
        self.open_block = None

    def reset_for_next_assistant_turn(self) -> None:
        """Snapshot rendered → flags reset so the next assistant turn (within
        the same SDK call, if it tool-uses then text-replies) is unambiguous."""
        self.streamed_text = False
        self.streamed_thinking = False
        self.open_block = None
        self._heartbeat_count = 0
        self._heartbeat_started_inline = False
        self._tool_use_name = None
        self._tool_use_bytes = 0

    def flush_pending_tool_uses(self, out: Any) -> None:
        """Emit any deferred tool_use lines whose tool_result never arrived.

        Called on `ResultMessage` (turn complete) so unfinished tool calls
        still appear in the chat history.
        """
        for pending in self.pending_tool_uses.values():
            name = pending["name"]
            summary = pending["summary"]
            line = f"[→ {name}{' ' + summary if summary else ''}]"
            out.write(_styled(out, line, "bold cyan") + "\n")
        self.pending_tool_uses.clear()

    def heartbeat(self, out: Any) -> None:
        """Called when the SDK has been silent past `idle_timeout` seconds."""
        self._heartbeat_count += 1
        elapsed = self._heartbeat_count * 5
        # tool_use already has its own live byte counter — leave it alone.
        if self.open_block == "tool_use":
            return
        # If we're mid-stream inside thinking/text, append a tiny "·" to that
        # line so the user sees liveness without breaking flow. Otherwise
        # write a standalone status line.
        if self.open_block is not None:
            out.write("·")
            _flush(out)
            return
        if not self._heartbeat_started_inline:
            # Open dim style; close on _consume_heartbeat when a real event
            # arrives. Same dim treatment as thinking — both are "noise."
            out.write(_open(out, "dim") + f"[…still working · {elapsed}s")
            self._heartbeat_started_inline = True
        else:
            out.write(f" · {elapsed}s")
        _flush(out)

    def _consume_heartbeat(self, out: Any) -> None:
        if self._heartbeat_started_inline:
            out.write("]" + _close(out, "dim") + "\n")
            self._heartbeat_started_inline = False
            self._heartbeat_count = 0
