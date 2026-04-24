"""Per-session state tracked across turns of an agentwire REPL.

Holds the running token/cost totals and the current configuration so slash
commands like `/cost`, `/tools`, `/model` can report it without reaching back
into the options dict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReplState:
    """Mutable session-wide state for an interactive REPL.

    Reset (not deleted) when `/clear` fires — the session persists but the
    SDK conversation restarts, so we keep mode/model/tools but zero the
    running totals and bump `restart_count`.
    """
    mode: str
    model: str
    allowed_tools: list[str]
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    turn_count: int = 0
    restart_count: int = 0
    session_id: str | None = None


def track_system_init(state: ReplState, message: Any) -> None:
    """Capture session_id from a SystemMessage(subtype=init)."""
    data = getattr(message, "data", {}) or {}
    sid = data.get("session_id") or data.get("sessionId")
    if sid:
        state.session_id = sid


def track_result(state: ReplState, message: Any) -> None:
    """Fold a ResultMessage's usage + cost into the session totals."""
    usage = getattr(message, "usage", None) or {}
    if isinstance(usage, dict):
        state.total_input_tokens += int(usage.get("input_tokens", 0) or 0)
        state.total_output_tokens += int(usage.get("output_tokens", 0) or 0)
    cost = getattr(message, "total_cost_usd", None)
    if cost is not None:
        try:
            state.total_cost_usd += float(cost)
        except (TypeError, ValueError):
            pass
    state.turn_count += 1


def reset_for_restart(state: ReplState) -> None:
    """Zero the per-conversation counters and bump restart_count.

    Preserves mode/model/tools since those don't change across /clear.
    """
    state.total_input_tokens = 0
    state.total_output_tokens = 0
    state.total_cost_usd = 0.0
    state.turn_count = 0
    state.session_id = None
    state.restart_count += 1
