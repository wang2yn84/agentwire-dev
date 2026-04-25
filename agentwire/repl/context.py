"""Project-level context loading for the agentwire REPL (Phase 3 PR 3).

Surfaces .agentwire.yml + role files into a shape the REPL can consume:
- merged role instructions (appended to system prompt)
- per-session voice (used by /say)
- effective role names (shown in banner)

Lives in its own module so build_options stays a thin glue function and the
business of "what does this project look like" can grow without crowding it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentwire.project_config import load_project_config
from agentwire.roles import load_roles, merge_roles


@dataclass
class SessionContext:
    role_names: list[str]
    role_instructions: str | None  # None when no roles resolved
    voice: str | None  # from .agentwire.yml
    missing_roles: list[str]  # names in config that didn't resolve


def load_session_context(
    cwd: Path,
    *,
    role_overrides: list[str] | None = None,
) -> SessionContext:
    """Load roles + voice for a REPL session.

    Order of precedence for role names:
      1. `role_overrides` (from `--role` CLI flag, if any)
      2. `roles:` in the nearest `.agentwire.yml`
      3. empty list

    Voice is taken from `.agentwire.yml` only — `/say` defers to `agentwire
    say`'s own resolution (CLI flag → .agentwire.yml → global default), so
    here we only need to surface what the project declares.
    """
    cfg = load_project_config(cwd)
    if role_overrides is not None:
        names = list(role_overrides)
    elif cfg is not None:
        names = list(cfg.roles)
    else:
        names = []

    if names:
        roles, missing = load_roles(names, project_path=cwd)
        merged = merge_roles(roles)
        instructions = merged.instructions or None
    else:
        roles, missing = [], []
        instructions = None

    voice = cfg.voice if cfg is not None else None
    return SessionContext(
        role_names=[r.name for r in roles],
        role_instructions=instructions,
        voice=voice,
        missing_roles=missing,
    )
