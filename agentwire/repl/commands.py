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
    return CONTINUE


def _model(state: ReplState, args: str, out: TextIO) -> str:
    sid = state.session_id or "(not yet started)"
    out.write(f"[model={state.model} · mode={state.mode} · session={sid[:8] if state.session_id else sid}]\n")
    return CONTINUE


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
        exit_cmd,
    ]
    registry: dict[str, Command] = {}
    for cmd in commands:
        registry[cmd.name] = cmd
        for alias in cmd.aliases:
            registry[alias] = cmd
    return registry


COMMANDS: dict[str, Command] = _build_registry()
