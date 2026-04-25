"""Tests for the REPL session-context loader (Phase 3 PR 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentwire.repl.context import load_session_context


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
    def test_returns_empty(self, tmp_path):
        ctx = load_session_context(tmp_path)
        assert ctx.role_names == []
        assert ctx.role_instructions is None
        assert ctx.voice is None
        assert ctx.missing_roles == []


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
        # Override == [] means user wants no roles, ignore config.
        assert ctx.role_names == []
        assert ctx.role_instructions is None


class TestVoice:
    def test_loads_voice(self, tmp_path):
        _write_project_yaml(tmp_path, voice="alice")
        ctx = load_session_context(tmp_path)
        assert ctx.voice == "alice"

    def test_no_voice_in_config(self, tmp_path):
        _write_project_yaml(tmp_path)
        ctx = load_session_context(tmp_path)
        assert ctx.voice is None
