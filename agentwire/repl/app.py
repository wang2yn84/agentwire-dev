"""Agentwire REPL application entry point.

Print mode: one-shot SDK call, stream events, exit.
Interactive: persistent prompt_toolkit loop holding one ClaudeSDKClient open
across turns. Ctrl+D exits; Ctrl+C cancels the current turn. Slash commands
live in `commands.py`; transcript persistence lives in `persistence.py`.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from agentwire.repl.commands import CONTINUE, EXIT, RESTART, RESUME, dispatch_command
from agentwire.repl import persistence
from agentwire.repl.damage_control import make_pre_tool_hook
from agentwire.repl.mentions import expand_mentions
from agentwire.repl.state import ReplState, reset_for_restart, track_result, track_system_init
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
    """Translate the REPL's `/thinking` mode into an SDK thinking config."""
    if mode == "adaptive":
        return {"type": "adaptive"}
    if mode == "summarized":
        return {"type": "adaptive", "display": "summarized"}
    if mode == "off":
        return {"type": "disabled"}
    return {"type": "adaptive"}


def run_repl(
    mode: str = "bypass",
    model: str | None = None,
    print_prompt: str | None = None,
    system_prompt: str | None = None,
    session_name: str | None = None,
    resume: str | None = None,
) -> int:
    """Run the REPL. Returns exit code.

    - Print mode (`-p PROMPT`): one SDK call, stream events, exit. No
      transcript (print mode is fire-and-forget).
    - Interactive: persistent loop with full transcript persistence under
      `~/.agentwire/sessions/repl/<session_name>/`.
    """
    if print_prompt is not None:
        return asyncio.run(_run_print_mode(print_prompt, mode, model, system_prompt))
    return asyncio.run(_run_interactive(mode, model, system_prompt, session_name, resume))


# -------- print mode (SDK-backed) --------


async def _run_print_mode(
    prompt: str,
    mode: str,
    model: str | None,
    system_prompt: str | None,
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

    options = build_options(ClaudeAgentOptions, mode, model, system_prompt, cwd=Path.cwd())

    # claude-agent-sdk refuses to nest — same CLAUDECODE unset as the workflow runner.
    saved_claudecode = os.environ.pop("CLAUDECODE", None)
    exit_code = 0
    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            async for message in client.receive_response():
                try:
                    render_message(
                        message,
                        AssistantMessage=AssistantMessage,
                        UserMessage=UserMessage,
                        SystemMessage=SystemMessage,
                        ResultMessage=ResultMessage,
                        out=sys.stdout,
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
        "include_partial_messages": False,
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
) -> None:
    """Render one SDK message to the terminal.

    Compact, human-readable output matching the spirit of pi's JSONL but for
    a terminal reader. Tools like `Read`/`Bash` show the target; tool results
    show a one-line preview.
    """
    if isinstance(message, SystemMessage):
        if getattr(message, "subtype", None) == "init":
            data = getattr(message, "data", {}) or {}
            model = data.get("model", "") or ""
            sid = (data.get("session_id") or data.get("sessionId") or "")[:8]
            parts = [p for p in [model, f"session {sid}" if sid else ""] if p]
            out.write(f"[agent started · {' · '.join(parts)}]\n")
        return

    if isinstance(message, AssistantMessage):
        for block in getattr(message, "content", []) or []:
            btype = _block_type(block)
            if btype == "text":
                text = _block_attr(block, "text", "") or ""
                if text:
                    out.write(text)
                    if not text.endswith("\n"):
                        out.write("\n")
            elif btype == "tool_use":
                name = _block_attr(block, "name", "") or ""
                inp = _block_attr(block, "input", {}) or {}
                summary = _format_tool_input(name, inp)
                out.write(f"[→ {name}{' ' + summary if summary else ''}]\n")
            elif btype == "thinking":
                thinking = _block_attr(block, "thinking", "") or ""
                first = thinking.split("\n", 1)[0].strip()
                if first:
                    preview = first if len(first) <= 80 else first[:77] + "..."
                    out.write(f"[thinking: {preview}]\n")
        return

    if isinstance(message, UserMessage):
        content = getattr(message, "content", None)
        if isinstance(content, list):
            for block in content:
                btype = _block_type(block)
                if btype == "tool_result":
                    result_content = _block_attr(block, "content", None)
                    preview = _format_tool_result(result_content)
                    out.write(f"[← result: {preview}]\n")
        return

    if isinstance(message, ResultMessage):
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
            out.write(f"[error · {category}{suffix}] {err}\n")
        else:
            out.write(f"[done{suffix}]\n")
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


# -------- interactive (prompt_toolkit + persistent SDK client) --------


async def _run_interactive(
    mode: str,
    model: str | None,
    system_prompt: str | None,
    session_name: str | None = None,
    resume: str | None = None,
) -> int:
    """Run the interactive REPL.

    Holds one `ClaudeSDKClient` open across the whole session so each turn
    extends the same conversation (model keeps short-term context, session_id
    stays stable). Ctrl+D exits cleanly; Ctrl+C cancels the current turn.
    Slash commands are Phase 2 — here we only honor `/exit` and `/quit` as
    conveniences for users who prefer explicit exit to Ctrl+D.
    """
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

    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import InMemoryHistory
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.patch_stdout import patch_stdout
    except ImportError as e:
        print(f"error: prompt_toolkit not installed: {e}", file=sys.stderr)
        return 1

    # Resume handling — look up the SDK session_id to pass to the first
    # ClaudeSDKClient.
    resume_sdk_session_id: str | None = None
    if resume:
        prior = persistence.load_session(resume)
        if prior is None:
            print(f"error: no session named {resume!r} under {persistence.DEFAULT_REPL_HOME}", file=sys.stderr)
            return 1
        ids = prior.get("sdk_session_ids") or []
        if not ids:
            print(f"warning: session {resume!r} has no recorded sdk_session_id; starting fresh conversation", file=sys.stderr)
        else:
            resume_sdk_session_id = ids[-1]  # most recent

    # ReplState built up-front so the permission callback (and the bottom
    # toolbar) can close over it before the first SDK options bake.
    placeholder_tools = (RESTRICTED_TOOLS if mode == "restricted" else FULL_TOOLS).copy()
    state = ReplState(
        mode=mode,
        model=model or DEFAULT_MODEL,
        allowed_tools=placeholder_tools,
    )

    can_use_tool = _make_can_use_tool(state) if mode == "prompted" else None

    options = build_options(
        ClaudeAgentOptions, mode, model, system_prompt,
        cwd=Path.cwd(), resume_sdk_session_id=resume_sdk_session_id,
        effort=state.effort, thinking_mode=state.thinking_mode,
        can_use_tool=can_use_tool,
    )

    model_display = model or f"{DEFAULT_MODEL} (default)"
    sys.stdout.write(BANNER.format(mode=mode, model=model_display))
    if resume_sdk_session_id:
        sys.stdout.write(f"Resuming {resume!r} (sdk session {resume_sdk_session_id[:8]}…)\n")
    sys.stdout.write(
        "Interactive mode. Enter to send · Alt+Enter for newline · Ctrl+D to exit · Ctrl+C to cancel.\n"
        "Type /help for commands. @path/to/file expands inline. /effort and /thinking tune SDK knobs.\n"
    )
    if _mcp_enabled():
        sys.stdout.write("agentwire MCP server attached — /tools to see what's wired in.\n")
    sys.stdout.write("\n")
    sys.stdout.flush()

    allowed_tools = (
        list(options.allowed_tools)
        if hasattr(options, "allowed_tools")
        else list(getattr(options, "kwargs", {}).get("allowed_tools", []))
    )
    state.allowed_tools = allowed_tools

    transcript = persistence.create_session(
        mode=mode,
        model=model or DEFAULT_MODEL,
        allowed_tools=allowed_tools,
        name=session_name,
    )
    sys.stdout.write(f"[transcript → {transcript.session_dir}]\n\n")
    state.session_dir = str(transcript.session_dir)
    state.transcript_name = transcript.name

    # Multi-line input: only meaningful when stdin is a real TTY. With piped
    # stdin (smoke tests, scripts, scheduler invocations) prompt_toolkit's
    # multi-line mode treats every \n as literal and never submits on EOF,
    # so we fall back to single-line in that case.
    is_tty = sys.stdin.isatty()
    bottom_toolbar = _make_bottom_toolbar(state) if is_tty else None
    if is_tty:
        kb = KeyBindings()

        @kb.add("escape", "enter")
        def _(event):
            event.current_buffer.insert_text("\n")

        @kb.add("c-j")  # Ctrl-J fallback for terminals that don't pass Alt
        def _(event):
            event.current_buffer.insert_text("\n")

        @kb.add("enter")
        def _(event):
            event.current_buffer.validate_and_handle()

        prompt_session = PromptSession(
            history=InMemoryHistory(),
            multiline=True,
            key_bindings=kb,
            prompt_continuation="… ",
            bottom_toolbar=bottom_toolbar,
            refresh_interval=0.5,
        )
    else:
        prompt_session = PromptSession(history=InMemoryHistory())

    saved_claudecode = os.environ.pop("CLAUDECODE", None)
    exit_code = 0

    # Outer loop wraps the SDK client so /clear and /resume can close+reopen
    # it for a different conversation while the REPL keeps running.
    try:
        while True:
            restart_requested, should_exit, loop_exit_code = await _run_sdk_session(
                options=options,
                state=state,
                transcript=transcript,
                prompt_session=prompt_session,
                ClaudeSDKClient=ClaudeSDKClient,
                AssistantMessage=AssistantMessage,
                UserMessage=UserMessage,
                SystemMessage=SystemMessage,
                ResultMessage=ResultMessage,
                patch_stdout=patch_stdout,
            )
            if loop_exit_code != 0:
                exit_code = loop_exit_code
            if should_exit:
                break
            if not restart_requested:
                break
            # Rebuild options for /clear vs /resume vs /effort vs /thinking.
            next_resume_id = state.pending_resume_sdk_session_id
            state.pending_resume_sdk_session_id = None
            options = build_options(
                ClaudeAgentOptions, mode, model, system_prompt,
                cwd=Path.cwd(), resume_sdk_session_id=next_resume_id,
                effort=state.effort, thinking_mode=state.thinking_mode,
                can_use_tool=can_use_tool,
            )
            transcript.write_event({
                "type": "restart",
                "resume_sdk_session_id": next_resume_id,
            })
            reset_for_restart(state)
    finally:
        if saved_claudecode is not None:
            os.environ["CLAUDECODE"] = saved_claudecode
        persistence.finalize(transcript, state)
        transcript.close()
    return exit_code


async def _run_sdk_session(
    *,
    options: Any,
    state: ReplState,
    transcript: Any,
    prompt_session: Any,
    ClaudeSDKClient: Any,
    AssistantMessage: Any,
    UserMessage: Any,
    SystemMessage: Any,
    ResultMessage: Any,
    patch_stdout: Any,
) -> tuple[bool, bool, int]:
    """Run one ClaudeSDKClient lifecycle. Returns (restart, exit, exit_code).

    A slash-command `/clear` or `/resume` signals restart — outer loop
    reopens the client (with a resume session_id if /resume fired). EOF or
    `/exit` signals exit. Every turn + tool event is written to
    `transcript.events_path` as JSONL.
    """
    exit_code = 0
    async with ClaudeSDKClient(options=options) as client:
        while True:
            try:
                with patch_stdout():
                    user_input = await prompt_session.prompt_async("> ")
            except EOFError:
                sys.stdout.write("\n[exit]\n")
                return False, True, exit_code
            except KeyboardInterrupt:
                sys.stdout.write("\n")
                continue

            text = user_input.strip()
            if not text:
                continue

            if text.startswith("/"):
                action = dispatch_command(text, state, sys.stdout)
                if action == EXIT:
                    return False, True, exit_code
                if action in (RESTART, RESUME):
                    return True, False, exit_code
                continue

            # Expand @path mentions before sending. Record both raw and
            # expanded text so transcripts capture user intent + what the
            # model actually saw.
            expanded_text, expansions = expand_mentions(text, cwd=Path.cwd())
            if expansions:
                sys.stdout.write(
                    f"[expanded {len(expansions)} mention"
                    f"{'s' if len(expansions) != 1 else ''}: "
                    f"{', '.join(e.raw for e in expansions)}]\n"
                )
                sys.stdout.flush()

            transcript.write_event({
                "type": "user_input",
                "text": text,
                **(
                    {
                        "expanded_text": expanded_text,
                        "mentions": [{"raw": e.raw, "target": e.target} for e in expansions],
                    }
                    if expansions
                    else {}
                ),
            })

            try:
                await client.query(expanded_text)
                async for message in client.receive_response():
                    try:
                        render_message(
                            message,
                            AssistantMessage=AssistantMessage,
                            UserMessage=UserMessage,
                            SystemMessage=SystemMessage,
                            ResultMessage=ResultMessage,
                            out=sys.stdout,
                        )
                        sys.stdout.flush()
                        _persist_sdk_message(
                            message, transcript,
                            AssistantMessage, UserMessage,
                            SystemMessage, ResultMessage,
                        )
                        if isinstance(message, SystemMessage):
                            track_system_init(state, message)
                            if state.session_id:
                                persistence.record_session_id(transcript, state.session_id)
                        elif isinstance(message, ResultMessage):
                            track_result(state, message)
                            if getattr(message, "is_error", False):
                                exit_code = 1
                    except Exception as exc:
                        print(f"[repl: render error: {exc}]", file=sys.stderr)
            except (KeyboardInterrupt, asyncio.CancelledError):
                sys.stdout.write("\n[turn cancelled]\n")
                sys.stdout.flush()
                continue
            except Exception as exc:
                category = _classify_sdk_error(type(exc).__name__, str(exc))
                print(
                    f"[repl: {category} · {type(exc).__name__}: {exc}]",
                    file=sys.stderr,
                )
                continue


# -------- Phase 2 PR 4 helpers: bottom toolbar + permission prompt --------


def _make_bottom_toolbar(state: ReplState):
    """Return a callable rendered by prompt_toolkit at the bottom of the prompt.

    Reads from `state` directly so token/cost totals refresh after every turn
    without us pushing into prompt_toolkit. Falls back to a config-only line
    before the first response lands.
    """
    def _render():
        if state.turn_count == 0:
            return (
                f"{state.mode} · {state.model} · effort={state.effort} · "
                f"thinking={state.thinking_mode}"
            )
        total = state.total_input_tokens + state.total_output_tokens
        return (
            f"{state.turn_count} turn{'s' if state.turn_count != 1 else ''} · "
            f"{total} tok ({state.total_input_tokens} in / {state.total_output_tokens} out) · "
            f"${state.total_cost_usd:.4f} · effort={state.effort} · "
            f"thinking={state.thinking_mode}"
        )
    return _render


def _make_can_use_tool(state: ReplState):
    """Build a `can_use_tool` async callback for `sdk-prompted` mode.

    Prints a one-line tool-use prompt (`Read /etc/passwd?`) and reads y/n/a
    from stdin in a worker thread so the SDK's async loop isn't blocked. 'a'
    adds the tool name to a per-session always-allow set kept on `state`.
    Reset by `/clear`.
    """
    async def _can_use_tool(tool_name: str, tool_input: dict, ctx: Any):
        # Lazy import — keeps SDK absence from breaking module import.
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

        if tool_name in state.always_allow_tools:
            return PermissionResultAllow()

        summary = _format_tool_input(tool_name, tool_input or {})
        prompt = f"\n[? allow {tool_name}{' ' + summary if summary else ''}? (y/n/a=always)] "

        loop = asyncio.get_event_loop()
        try:
            answer = await loop.run_in_executor(None, _prompt_sync, prompt)
        except (EOFError, KeyboardInterrupt):
            sys.stdout.write("\n[denied via Ctrl+C/EOF]\n")
            sys.stdout.flush()
            return PermissionResultDeny(message="user denied (interrupt)")

        answer = (answer or "").strip().lower()
        if answer in ("a", "always"):
            state.always_allow_tools.add(tool_name)
            sys.stdout.write(f"[allow · {tool_name} now always allowed this session]\n")
            sys.stdout.flush()
            return PermissionResultAllow()
        if answer in ("", "y", "yes"):
            return PermissionResultAllow()
        sys.stdout.write(f"[deny · {tool_name}]\n")
        sys.stdout.flush()
        return PermissionResultDeny(message="user denied")

    return _can_use_tool


def _prompt_sync(prompt: str) -> str:
    """Blocking prompt for the permission callback. Runs off-loop."""
    sys.stdout.write(prompt)
    sys.stdout.flush()
    return sys.stdin.readline()
