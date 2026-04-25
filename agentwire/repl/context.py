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

# When there's no `.agentwire.yml` and no `--role`, the REPL still wants an
# agentwire-aware identity by default — pulls the bundled `agentwire` role
# (agentwire/roles/agentwire.md) so the model knows about MCP tools, voice
# routing, etc. without each project re-declaring it.
DEFAULT_ROLE = "agentwire"


@dataclass
class SessionContext:
    role_names: list[str]
    role_instructions: str | None  # None when no roles resolved
    voice: str | None  # from .agentwire.yml or global config default
    missing_roles: list[str]  # names in config that didn't resolve


def load_session_context(
    cwd: Path,
    *,
    role_overrides: list[str] | None = None,
) -> SessionContext:
    """Load roles + voice for a REPL session.

    Order of precedence for role names:
      1. `role_overrides` (from `--role` CLI flag, if any) — including `[]`
         to explicitly opt out of all roles
      2. `roles:` in the nearest `.agentwire.yml`
      3. `[DEFAULT_ROLE]` — the bundled `agentwire` role, so a bare
         `agentwire repl` outside any project still has agentwire identity

    Voice precedence:
      1. `.agentwire.yml` `voice:`
      2. Global config `tts.default_voice` (we only run one voice these
         days; surfacing it in the REPL state means /say + the banner show
         the right voice without per-project setup)
    """
    cfg = load_project_config(cwd)
    if role_overrides is not None:
        names = list(role_overrides)
    elif cfg is not None and cfg.roles:
        names = list(cfg.roles)
    else:
        names = [DEFAULT_ROLE]

    if names:
        roles, missing = load_roles(names, project_path=cwd)
        merged = merge_roles(roles)
        instructions = merged.instructions or None
    else:
        roles, missing = [], []
        instructions = None

    voice: str | None = cfg.voice if cfg is not None else None
    if not voice:
        voice = _global_default_voice()

    return SessionContext(
        role_names=[r.name for r in roles],
        role_instructions=instructions,
        voice=voice,
        missing_roles=missing,
    )


def _global_default_voice() -> str | None:
    """Read tts.default_voice from ~/.agentwire/config.yaml. None on failure."""
    try:
        from agentwire.config import load_config
        cfg = load_config()
        voice = getattr(getattr(cfg, "tts", None), "default_voice", None)
        # Guard the placeholder string the config dataclass uses pre-config-file.
        if voice and voice != "default":
            return voice
    except Exception:
        return None
    return None
