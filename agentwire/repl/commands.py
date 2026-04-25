"""Slash command registry for the agentwire REPL.

The loop hands each line starting with `/` to `dispatch_command()`. Handlers
receive the session state and return a `CommandResult` telling the loop what
to do next: keep going, exit cleanly, or restart the SDK client (fresh
conversation). New commands are added by adding an entry to `COMMANDS`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TextIO

from agentwire.repl.state import ReplState


# Sentinel return values. Strings over an Enum so test expectations read
# literally and new actions can be added without enum churn.
CONTINUE = "continue"
EXIT = "exit"
RESTART = "restart"
RESUME = "resume"


Handler = Callable[[ReplState, str, TextIO], str]


@dataclass
class Command:
    name: str
    handler: Handler
    summary: str
    aliases: tuple[str, ...] = ()


def dispatch_command(line: str, state: ReplState, out: TextIO) -> str | None:
    """Dispatch `/foo args` to its handler, return the action.

    Returns `None` if `line` isn't a recognized slash command (caller falls
    back to sending it as a user turn). Returns `CONTINUE`, `EXIT`, or
    `RESTART` for recognized commands.
    """
    if not line.startswith("/"):
        return None
    parts = line.split(None, 1)
    name = parts[0]
    args = parts[1] if len(parts) > 1 else ""

    cmd = COMMANDS.get(name)
    if cmd is None:
        out.write(f"[unknown command: {name} — try /help]\n")
        return CONTINUE
    return cmd.handler(state, args, out)


# -------- handlers --------


def _help(state: ReplState, args: str, out: TextIO) -> str:
    out.write("Available commands:\n")
    seen = set()
    for cmd in COMMANDS.values():
        if cmd.name in seen:
            continue
        seen.add(cmd.name)
        alias_part = f"  (aliases: {', '.join(cmd.aliases)})" if cmd.aliases else ""
        out.write(f"  {cmd.name:<12} {cmd.summary}{alias_part}\n")
    return CONTINUE


def _exit(state: ReplState, args: str, out: TextIO) -> str:
    out.write("[exit]\n")
    return EXIT


def _clear(state: ReplState, args: str, out: TextIO) -> str:
    out.write("[restarting conversation — session history cleared]\n")
    return RESTART


def _cost(state: ReplState, args: str, out: TextIO) -> str:
    if state.turn_count == 0:
        out.write("[no turns yet this conversation]\n")
        return CONTINUE
    total_tok = state.total_input_tokens + state.total_output_tokens
    out.write(
        f"[session · {state.turn_count} turn{'s' if state.turn_count != 1 else ''} · "
        f"{state.total_input_tokens}+{state.total_output_tokens}={total_tok} tok · "
        f"${state.total_cost_usd:.4f}]\n"
    )
    return CONTINUE


def _tools(state: ReplState, args: str, out: TextIO) -> str:
    if not state.allowed_tools:
        out.write("[no tools allowed]\n")
        return CONTINUE
    out.write(f"[allowed tools (mode={state.mode}): {', '.join(state.allowed_tools)}]\n")
    if any(t.startswith("mcp__agentwire") for t in state.allowed_tools):
        out.write(
            "[agentwire MCP server attached — sessions, panes, voice, scheduler, "
            "channels, workflows, etc. are first-class tool calls]\n"
        )
    return CONTINUE


def _model(state: ReplState, args: str, out: TextIO) -> str:
    sid = state.session_id or "(not yet started)"
    out.write(f"[model={state.model} · mode={state.mode} · session={sid[:8] if state.session_id else sid}]\n")
    return CONTINUE


# Effort + thinking are SDK-side knobs. Changing them mid-session means the
# next conversation needs new options, so the handlers also signal RESTART.
# The user is told the conversation will reset; this is the same UX as
# /clear.
_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
_THINKING_MODES = ("adaptive", "summarized", "off")


def _effort(state: ReplState, args: str, out: TextIO) -> str:
    target = args.strip().lower()
    if not target:
        out.write(
            f"[effort={state.effort} · choices: {', '.join(_EFFORT_LEVELS)}]\n"
            "Usage: /effort <level>\n"
        )
        return CONTINUE
    if target not in _EFFORT_LEVELS:
        out.write(f"[unknown effort {target!r}; choices: {', '.join(_EFFORT_LEVELS)}]\n")
        return CONTINUE
    if target == state.effort:
        out.write(f"[effort already {target}]\n")
        return CONTINUE
    state.effort = target
    out.write(f"[effort → {target} · restarting conversation]\n")
    return RESTART


def _run_workflow(state: ReplState, args: str, out: TextIO) -> str:
    """`/run-workflow <name> [-v]` runs `agentwire workflow run <name>`.

    Streaming the workflow's stdout into the REPL terminal in real time.
    Phase 4 reverse-direction primitive — REPL spawns a workflow with the
    current cwd; output is plain text (the workflow's own log lines), not
    folded into the SDK conversation.
    """
    parts = args.split()
    if not parts:
        out.write("Usage: /run-workflow <name> [extra args]\n")
        return CONTINUE

    import subprocess
    cmd = ["agentwire", "workflow", "run", *parts]
    out.write(f"[running: {' '.join(cmd)}]\n")
    out.flush() if hasattr(out, "flush") else None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        for line in proc.stdout:
            out.write(line)
        proc.wait()
        out.write(f"[workflow exited with code {proc.returncode}]\n")
    except FileNotFoundError:
        out.write("[run-workflow failed: `agentwire` not found on PATH]\n")
    except Exception as exc:
        out.write(f"[run-workflow error: {exc}]\n")
    return CONTINUE


def _say(state: ReplState, args: str, out: TextIO) -> str:
    """`/say <text>` speaks `text` via `agentwire say`.

    Voice resolution is delegated to `agentwire say` (CLI flag → .agentwire.yml
    → global config default). We pass `--voice` only if the project config
    explicitly set one, so a user-level default doesn't get blown away.
    """
    text = args.strip()
    if not text:
        if state.voice:
            out.write(f"[say · voice={state.voice}]\nUsage: /say <text>\n")
        else:
            out.write("Usage: /say <text>\n")
        return CONTINUE

    import subprocess
    cmd = ["agentwire", "say"]
    if state.voice:
        cmd += ["--voice", state.voice]
    cmd += ["--", text]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            out.write(f"[say failed: {proc.stderr.strip() or 'unknown error'}]\n")
        else:
            out.write(f"[said · {len(text)} chars]\n")
    except FileNotFoundError:
        out.write("[say failed: `agentwire` not found on PATH]\n")
    except subprocess.TimeoutExpired:
        out.write("[say timed out after 30s]\n")
    except Exception as exc:
        out.write(f"[say error: {exc}]\n")
    return CONTINUE


def _thinking(state: ReplState, args: str, out: TextIO) -> str:
    target = args.strip().lower()
    if not target:
        out.write(
            f"[thinking={state.thinking_mode} · choices: {', '.join(_THINKING_MODES)}]\n"
            "Usage: /thinking <mode>\n"
            "  adaptive   — Claude decides budget; reasoning hidden (Opus 4.7 default)\n"
            "  summarized — Claude decides budget; show summarized reasoning\n"
            "  off        — disable extended thinking\n"
        )
        return CONTINUE
    if target not in _THINKING_MODES:
        out.write(f"[unknown thinking {target!r}; choices: {', '.join(_THINKING_MODES)}]\n")
        return CONTINUE
    if target == state.thinking_mode:
        out.write(f"[thinking already {target}]\n")
        return CONTINUE
    state.thinking_mode = target
    out.write(f"[thinking → {target} · restarting conversation]\n")
    return RESTART


def _save(state: ReplState, args: str, out: TextIO) -> str:
    """Confirm where the transcript is being written. Every turn is already
    auto-persisted, so `/save` is a UX affirmation, not a flush-on-demand."""
    if not state.session_dir:
        out.write("[no transcript dir yet — persistence starts with the first turn]\n")
        return CONTINUE
    out.write(
        f"[transcript · {state.transcript_name} · "
        f"{state.turn_count} turn{'s' if state.turn_count != 1 else ''}]\n"
        f"[path · {state.session_dir}]\n"
    )
    if state.session_id:
        out.write(f"[resume with · /resume {state.transcript_name}]\n")
    return CONTINUE


def _resume(state: ReplState, args: str, out: TextIO) -> str:
    """`/resume` lists recent sessions; `/resume NAME` restarts with that
    session's sdk_session_id to continue the prior conversation.

    Looks up metadata in `~/.agentwire/sessions/repl/` and defers the
    actual SDK re-open to the outer loop (via RESUME + pending_resume_*).
    """
    # Local import — commands.py stays lightweight + avoids circular imports
    # through app.py.
    from agentwire.repl import persistence

    target = args.strip()
    if not target:
        sessions = persistence.list_sessions(limit=10)
        if not sessions:
            out.write("[no saved sessions found]\n")
            return CONTINUE
        out.write("Recent sessions (most recent first):\n")
        for meta in sessions:
            name = meta.get("name", "?")
            turns = meta.get("turn_count", 0)
            mode = meta.get("mode", "?")
            started = meta.get("started_at", 0)
            from datetime import datetime, timezone
            try:
                when = datetime.fromtimestamp(started, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            except Exception:
                when = "?"
            out.write(f"  {name}  ({turns} turn{'s' if turns != 1 else ''} · mode={mode} · {when} UTC)\n")
        out.write("\nUsage: /resume <name>\n")
        return CONTINUE

    meta = persistence.load_session(target)
    if meta is None:
        out.write(f"[no session found: {target}]\n")
        return CONTINUE
    ids = meta.get("sdk_session_ids") or []
    if not ids:
        out.write(f"[session {target!r} has no recorded sdk_session_id; cannot resume]\n")
        return CONTINUE

    state.pending_resume_sdk_session_id = ids[-1]
    out.write(f"[resuming {target} (sdk session {ids[-1][:8]}…) — reopening client]\n")
    return RESUME


# -------- registry --------

def _build_registry() -> dict[str, Command]:
    """Build the command dict. `/quit` maps to the same Command as `/exit`."""
    exit_cmd = Command(name="/exit", handler=_exit, summary="Exit the REPL", aliases=("/quit",))
    commands = [
        Command(name="/help", handler=_help, summary="Show available commands"),
        Command(name="/clear", handler=_clear, summary="Reset conversation (fresh context)"),
        Command(name="/cost", handler=_cost, summary="Show session token + cost totals"),
        Command(name="/tools", handler=_tools, summary="List allowed tools for this mode"),
        Command(name="/model", handler=_model, summary="Show current model + session id"),
        Command(name="/save", handler=_save, summary="Show transcript path + resume hint"),
        Command(name="/resume", handler=_resume, summary="Resume a saved session (or list)"),
        Command(name="/effort", handler=_effort, summary="Show or set thinking effort (low/medium/high/xhigh/max)"),
        Command(name="/thinking", handler=_thinking, summary="Show or set thinking display (adaptive/summarized/off)"),
        Command(name="/say", handler=_say, summary="Speak text aloud via agentwire say (uses project voice)"),
        Command(name="/run-workflow", handler=_run_workflow, summary="Run an agentwire workflow and stream output here"),
        exit_cmd,
    ]
    registry: dict[str, Command] = {}
    for cmd in commands:
        registry[cmd.name] = cmd
        for alias in cmd.aliases:
            registry[alias] = cmd
    return registry


COMMANDS: dict[str, Command] = _build_registry()
