"""Tests for __main__.py — build_agent_command for each session type."""

import os
from unittest.mock import patch

import pytest

from agentwire.roles import RoleConfig


# Mock for load_config() in __main__ used by pi-zai branch.
FAKE_CONFIG = {
    "zai": {
        "api_key": "test-key-123",
        "base_url": "https://api.z.ai/api/anthropic",
        "timeout_ms": 3000000,
    },
    "pi": {
        "default_model": "glm-5",
        "binary": "pi",
    },
}


@pytest.fixture(autouse=True)
def mock_main_load_config():
    with patch("agentwire.__main__.load_config", return_value=FAKE_CONFIG):
        yield


class TestBuildAgentCommand:
    def _build(self, session_type, roles=None, model=None):
        from agentwire.__main__ import build_agent_command
        return build_agent_command(session_type, roles=roles, model=model)

    def test_bare_empty_command(self):
        cmd = self._build("bare")
        assert cmd.command == ""
        assert cmd.temp_file is None

    def test_claude_bypass(self):
        cmd = self._build("claude-bypass")
        assert "claude" in cmd.command
        assert "--dangerously-skip-permissions" in cmd.command

    def test_claude_prompted(self):
        cmd = self._build("claude-prompted")
        assert "claude" in cmd.command
        assert "--dangerously-skip-permissions" not in cmd.command
        assert "--tools" not in cmd.command

    def test_claude_restricted(self):
        cmd = self._build("claude-restricted")
        assert "claude" in cmd.command
        assert "--tools Bash" in cmd.command

    def test_with_model_override(self):
        cmd = self._build("claude-bypass", model="haiku")
        assert "--model haiku" in cmd.command

    def test_with_roles_tools(self):
        roles = [RoleConfig(name="test", tools=["Bash", "Read"])]
        cmd = self._build("claude-bypass", roles=roles)
        assert "--tools" in cmd.command
        assert "Bash" in cmd.command

    def test_with_roles_instructions(self):
        roles = [RoleConfig(name="test", instructions="Be helpful")]
        cmd = self._build("claude-bypass", roles=roles)
        assert "--append-system-prompt" in cmd.command
        assert cmd.temp_file is not None
        if cmd.temp_file:
            os.unlink(cmd.temp_file)

    def test_restricted_ignores_role_flags(self):
        """claude-restricted should not get role tools/instructions."""
        roles = [RoleConfig(name="test", tools=["Read"], instructions="Hello")]
        cmd = self._build("claude-restricted", roles=roles)
        assert "--append-system-prompt" not in cmd.command

    def test_unknown_type_empty(self):
        cmd = self._build("nonexistent-type")
        assert cmd.command == ""

    # === pi-zai session types ===

    def test_pi_zai_basic(self):
        """pi-zai launches pi binary with Z.AI provider and default model."""
        cmd = self._build("pi-zai")
        assert cmd.command.startswith("pi --provider zai")
        # Key is injected via tmux set-environment, NOT on the command line
        assert "ZAI_API_KEY" not in cmd.command
        assert cmd.env == {"ZAI_API_KEY": "test-key-123"}
        assert "--model glm-5" in cmd.command
        # Pi has no --dangerously-skip-permissions (no permission system)
        assert "--dangerously-skip-permissions" not in cmd.command
        assert cmd.temp_file is None

    def test_pi_zai_key_not_in_command(self):
        """Regression: ZAI_API_KEY must live in cmd.env, never in cmd.command."""
        cmd = self._build("pi-zai")
        assert "test-key-123" not in cmd.command
        assert cmd.env.get("ZAI_API_KEY") == "test-key-123"

    def test_pi_zai_restricted(self):
        """pi-zai-restricted whitelists read-only tools + bash."""
        cmd = self._build("pi-zai-restricted")
        assert "pi --provider zai" in cmd.command
        assert "--tools read,grep,find,bash" in cmd.command

    def test_pi_zai_readonly(self):
        """pi-zai-readonly has no bash, no edits — pure inspection."""
        cmd = self._build("pi-zai-readonly")
        assert "pi --provider zai" in cmd.command
        assert "--tools read,grep,find" in cmd.command
        # No bash, no edit, no write
        assert "bash" not in cmd.command.split("--tools")[1]
        assert "edit" not in cmd.command.split("--tools")[1]
        assert "write" not in cmd.command.split("--tools")[1]

    def test_pi_zai_model_override(self):
        cmd = self._build("pi-zai", model="glm-5.1")
        assert "--model glm-5.1" in cmd.command
        # Default should not also appear
        assert "--model glm-5 " not in cmd.command

    def test_pi_zai_with_role_instructions(self):
        """pi-zai with role.instructions uses --append-system-prompt."""
        roles = [RoleConfig(name="worker", instructions="You are a worker agent.")]
        cmd = self._build("pi-zai", roles=roles)
        assert "--append-system-prompt" in cmd.command
        assert cmd.temp_file is not None
        with open(cmd.temp_file) as f:
            content = f.read()
        assert "You are a worker agent." in content
        os.unlink(cmd.temp_file)

    def test_pi_zai_with_role_tools(self):
        """pi-zai translates Claude tool names (CamelCase) to pi's lowercase."""
        roles = [RoleConfig(name="test", tools=["Read", "Bash", "Edit"])]
        cmd = self._build("pi-zai", roles=roles)
        assert "--tools" in cmd.command
        # Extracted tool list section
        tools_section = cmd.command.split("--tools")[1].split()[0]
        assert "read" in tools_section
        assert "bash" in tools_section
        assert "edit" in tools_section

    def test_pi_zai_filters_unknown_tools(self):
        """pi-zai drops tool names pi doesn't support (e.g., Glob, WebFetch)."""
        roles = [RoleConfig(name="test", tools=["Read", "Glob", "WebFetch", "Bash"])]
        cmd = self._build("pi-zai", roles=roles)
        tools_section = cmd.command.split("--tools")[1].split()[0]
        # Glob → find (but only if we translated; current impl filters out unknowns)
        # WebFetch → not supported by pi
        assert "webfetch" not in tools_section.lower()
        # Read and Bash are valid
        assert "read" in tools_section
        assert "bash" in tools_section

    def test_pi_zai_restricted_ignores_role_tools(self):
        """pi-zai-restricted keeps its curated tool list regardless of roles."""
        roles = [RoleConfig(name="test", tools=["Edit", "Write"])]
        cmd = self._build("pi-zai-restricted", roles=roles)
        # Should have restricted's tool list, not role's
        assert "--tools read,grep,find,bash" in cmd.command


    def test_pi_zai_readonly_ignores_role_instructions(self):
        """pi-zai-readonly is a curated context, skips role instructions."""
        roles = [RoleConfig(name="test", instructions="Be creative")]
        cmd = self._build("pi-zai-readonly", roles=roles)
        assert "--append-system-prompt" not in cmd.command
        assert cmd.temp_file is None


class TestSessionEnvInjection:
    def test_build_session_env_shell_fragment_empty(self):
        from agentwire.__main__ import build_session_env_shell_fragment
        assert build_session_env_shell_fragment("s", {}) == ""

    def test_build_session_env_shell_fragment_quoted(self):
        from agentwire.__main__ import build_session_env_shell_fragment
        frag = build_session_env_shell_fragment("my-session", {"ZAI_API_KEY": "abc 123"})
        # Must end with trailing ` && ` so it can splice into a compound command
        assert frag.endswith(" && ")
        assert "set-environment -t my-session" in frag
        assert "ZAI_API_KEY" in frag
        # Value with spaces must be shell-quoted
        assert "'abc 123'" in frag

    def test_build_session_env_shell_fragment_multiple(self):
        from agentwire.__main__ import build_session_env_shell_fragment
        frag = build_session_env_shell_fragment("s", {"A": "1", "B": "2"})
        assert "A 1" in frag
        assert "B 2" in frag
        assert frag.count("set-environment") == 2


class TestParseEnvArgs:
    def test_none_returns_empty(self):
        from agentwire.__main__ import parse_env_args
        assert parse_env_args(None) == {}
        assert parse_env_args([]) == {}

    def test_single_pair(self):
        from agentwire.__main__ import parse_env_args
        assert parse_env_args(["FOO=bar"]) == {"FOO": "bar"}

    def test_multiple_pairs(self):
        from agentwire.__main__ import parse_env_args
        result = parse_env_args(["A=1", "B=2", "C=3"])
        assert result == {"A": "1", "B": "2", "C": "3"}

    def test_value_with_equals_sign_preserved(self):
        from agentwire.__main__ import parse_env_args
        # Values can contain `=` (e.g. base64 payloads) — only split on the first.
        assert parse_env_args(["TOKEN=abc=def=xyz"]) == {"TOKEN": "abc=def=xyz"}

    def test_empty_value_allowed(self):
        from agentwire.__main__ import parse_env_args
        assert parse_env_args(["DEBUG="]) == {"DEBUG": ""}

    def test_missing_equals_exits(self):
        from agentwire.__main__ import parse_env_args
        with pytest.raises(SystemExit):
            parse_env_args(["BROKEN"])

    def test_empty_key_exits(self):
        from agentwire.__main__ import parse_env_args
        with pytest.raises(SystemExit):
            parse_env_args(["=value"])

    def test_later_value_wins(self):
        from agentwire.__main__ import parse_env_args
        # If the same key appears twice, last one wins (standard dict semantics).
        assert parse_env_args(["K=1", "K=2"]) == {"K": "2"}
