"""Tests for agentwire/roles/__init__.py — Role parsing, merging, discovery."""

from pathlib import Path

import pytest

from agentwire.roles import (
    RoleConfig,
    MergedRole,
    parse_role_file,
    merge_roles,
    discover_role,
)


@pytest.fixture
def role_file(tmp_path):
    """Create a test role markdown file."""
    path = tmp_path / "test-role.md"
    path.write_text(
        "---\n"
        "name: test-role\n"
        "description: A test role\n"
        "tools: Bash,Read,Write\n"
        "disallowedTools: AskUserQuestion\n"
        'color: "#FF0000"\n'
        "---\n"
        "\n"
        "# Test Role\n"
        "\n"
        "You are a test role.\n"
    )
    return path


# --- parse_role_file ---

class TestParseRoleFile:
    def test_full_frontmatter(self, role_file):
        role = parse_role_file(role_file)
        assert role is not None
        assert role.name == "test-role"
        assert role.description == "A test role"
        assert role.tools == ["Bash", "Read", "Write"]
        assert role.disallowed_tools == ["AskUserQuestion"]
        assert role.color == "#FF0000"
        assert "You are a test role." in role.instructions

    def test_no_frontmatter(self, tmp_path):
        path = tmp_path / "plain.md"
        path.write_text("# Just instructions\n\nDo things.\n")
        role = parse_role_file(path)
        assert role is not None
        assert role.name == "plain"  # Uses stem
        assert role.tools == []
        assert role.disallowed_tools == []

    def test_missing_file(self, tmp_path):
        role = parse_role_file(tmp_path / "nonexistent.md")
        assert role is None

    def test_tools_as_string(self, tmp_path):
        path = tmp_path / "r.md"
        path.write_text("---\nname: r\ntools: Bash,Read\n---\n\nHello\n")
        role = parse_role_file(path)
        assert role is not None
        assert role.tools == ["Bash", "Read"]

    def test_tools_as_list(self, tmp_path):
        path = tmp_path / "r.md"
        path.write_text("---\nname: r\ntools: [Bash, Read]\n---\n\nHello\n")
        role = parse_role_file(path)
        assert role is not None
        assert role.tools == ["Bash", "Read"]


# --- merge_roles ---

class TestMergeRoles:
    def test_empty_roles(self):
        merged = merge_roles([])
        assert merged.tools == set()
        assert merged.disallowed_tools == set()
        assert merged.instructions == ""

    def test_tools_union(self):
        r1 = RoleConfig(name="a", tools=["Bash", "Read"])
        r2 = RoleConfig(name="b", tools=["Read", "Write"])
        merged = merge_roles([r1, r2])
        assert merged.tools == {"Bash", "Read", "Write"}

    def test_disallowed_intersection(self):
        r1 = RoleConfig(name="a", disallowed_tools=["AskUserQuestion", "Edit"])
        r2 = RoleConfig(name="b", disallowed_tools=["AskUserQuestion"])
        merged = merge_roles([r1, r2])
        # Only AskUserQuestion is in both
        assert merged.disallowed_tools == {"AskUserQuestion"}

    def test_disallowed_empty_when_no_overlap(self):
        r1 = RoleConfig(name="a", disallowed_tools=["Edit"])
        r2 = RoleConfig(name="b", disallowed_tools=["Write"])
        merged = merge_roles([r1, r2])
        assert merged.disallowed_tools == set()

    def test_instructions_concatenated(self):
        r1 = RoleConfig(name="a", instructions="Do A.")
        r2 = RoleConfig(name="b", instructions="Do B.")
        merged = merge_roles([r1, r2])
        assert "Do A." in merged.instructions
        assert "Do B." in merged.instructions

    def test_single_role(self):
        r1 = RoleConfig(name="a", tools=["Bash"], disallowed_tools=["Edit"], instructions="Hello")
        merged = merge_roles([r1])
        assert merged.tools == {"Bash"}
        assert merged.disallowed_tools == {"Edit"}
        assert merged.instructions == "Hello"


# --- discover_role ---

class TestDiscoverRole:
    def test_bundled_roles_found(self):
        """All 6 bundled roles should be discoverable."""
        for name in ["agentwire", "voice", "worker", "task-runner", "chatbot", "init"]:
            path = discover_role(name)
            assert path is not None, f"Bundled role '{name}' not found"

    def test_project_level_overrides_bundled(self, tmp_path):
        # Create project-level role
        project_roles = tmp_path / ".agentwire" / "roles"
        project_roles.mkdir(parents=True)
        custom = project_roles / "agentwire.md"
        custom.write_text("---\nname: agentwire\n---\n\nCustom!\n")

        path = discover_role("agentwire", project_path=tmp_path)
        assert path == custom

    def test_unknown_role_returns_none(self):
        path = discover_role("nonexistent-role-xyz")
        assert path is None
