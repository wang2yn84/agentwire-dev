"""Tests for the REPL session-context loader (Phase 3 PR 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentwire.repl.context import DEFAULT_ROLE, load_session_context


@pytest.fixture(autouse=True)
def _no_global_voice(monkeypatch):
    # Most tests don't care about voice; pin the global lookup off so they
    # see voice=None unless they wire it explicitly.
    from agentwire.repl import context
    monkeypatch.setattr(context, "_global_default_voice", lambda: None)


def _write_role(dir: Path, name: str, body: str = "Be helpful.") -> None:
    role_dir = dir / ".agentwire" / "roles"
    role_dir.mkdir(parents=True, exist_ok=True)
    (role_dir / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: test\n---\n\n{body}\n"
    )


def _write_project_yaml(dir: Path, *, roles=None, voice=None) -> None:
    lines = ["type: standard"]
    if roles:
        lines.append("roles:")
        lines.extend(f"  - {r}" for r in roles)
    if voice:
        lines.append(f"voice: {voice}")
    (dir / ".agentwire.yml").write_text("\n".join(lines) + "\n")


class TestNoConfig:
    def test_falls_back_to_default_agentwire_role(self, tmp_path):
        # Without a project config, the bundled `agentwire` role is loaded.
        ctx = load_session_context(tmp_path)
        assert ctx.role_names == [DEFAULT_ROLE]
        assert ctx.role_instructions  # non-empty
        assert ctx.voice is None  # no project voice; fixture pins global to None
        assert ctx.missing_roles == []

    def test_global_voice_picked_up(self, tmp_path, monkeypatch):
        from agentwire.repl import context
        monkeypatch.setattr(context, "_global_default_voice", lambda: "af_heart")
        ctx = load_session_context(tmp_path)
        assert ctx.voice == "af_heart"


class TestRolesFromConfig:
    def test_resolves_project_role(self, tmp_path):
        _write_role(tmp_path, "tester", "Write thorough tests.")
        _write_project_yaml(tmp_path, roles=["tester"])

        ctx = load_session_context(tmp_path)
        assert ctx.role_names == ["tester"]
        assert "thorough tests" in ctx.role_instructions
        assert ctx.missing_roles == []

    def test_multiple_roles_merged(self, tmp_path):
        _write_role(tmp_path, "tester", "Test rigorously.")
        _write_role(tmp_path, "reviewer", "Read critically.")
        _write_project_yaml(tmp_path, roles=["tester", "reviewer"])

        ctx = load_session_context(tmp_path)
        assert set(ctx.role_names) == {"tester", "reviewer"}
        assert "Test rigorously" in ctx.role_instructions
        assert "Read critically" in ctx.role_instructions

    def test_missing_role_reported(self, tmp_path):
        _write_project_yaml(tmp_path, roles=["nope"])
        ctx = load_session_context(tmp_path)
        assert ctx.role_names == []
        assert ctx.role_instructions is None
        assert "nope" in ctx.missing_roles


class TestRoleOverrides:
    def test_override_replaces_config(self, tmp_path):
        _write_role(tmp_path, "tester", "From project.")
        _write_role(tmp_path, "reviewer", "From override.")
        _write_project_yaml(tmp_path, roles=["tester"])

        ctx = load_session_context(tmp_path, role_overrides=["reviewer"])
        assert ctx.role_names == ["reviewer"]
        assert "From override" in ctx.role_instructions
        assert "From project" not in (ctx.role_instructions or "")

    def test_empty_override_kills_config(self, tmp_path):
        _write_role(tmp_path, "tester", "Should not appear.")
        _write_project_yaml(tmp_path, roles=["tester"])

        ctx = load_session_context(tmp_path, role_overrides=[])
        # Override == [] means user wants no roles. Skips both the project
        # config AND the default-agentwire fallback (explicit > implicit).
        assert ctx.role_names == []
        assert ctx.role_instructions is None


class TestProjectWithoutRoles:
    """A `.agentwire.yml` exists but doesn't declare any roles."""

    def test_default_role_still_applies(self, tmp_path):
        # Empty `roles:` in project config should still fall back to
        # DEFAULT_ROLE — otherwise users in agentwire projects mysteriously
        # lose the agentwire identity vs. running outside any project.
        from pathlib import Path as _Path
        (tmp_path / ".agentwire.yml").write_text("type: standard\nvoice: alice\n")
        ctx = load_session_context(tmp_path)
        assert ctx.role_names == ["agentwire"]
        assert ctx.voice == "alice"  # project voice wins over global


class TestVoice:
    def test_loads_voice(self, tmp_path):
        _write_project_yaml(tmp_path, voice="alice")
        ctx = load_session_context(tmp_path)
        assert ctx.voice == "alice"

    def test_no_voice_in_config(self, tmp_path):
        _write_project_yaml(tmp_path)
        ctx = load_session_context(tmp_path)
        assert ctx.voice is None
