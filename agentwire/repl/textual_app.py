"""Textual-based rendering layer for `agentwire repl`.

Phase 1A stub — the dispatcher in `app.py::run_repl` routes here when
`AGENTWIRE_REPL_TUI=textual` is set on a TTY and `textual` is importable.

Implementation arrives in Phase 1B (skeleton app), 1C (parity wiring),
and 1D (tests). See `docs/missions/agentwire-repl-textual.md` for the
phase-by-phase plan.
"""

from __future__ import annotations


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
    raise NotImplementedError(
        "Textual REPL not yet implemented — Phase 1B (skeleton app) lands next. "
        "Unset AGENTWIRE_REPL_TUI to use the existing line-mode REPL."
    )
