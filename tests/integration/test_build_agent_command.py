"""Tests for __main__.py — build_agent_command for each session type."""

import os
from unittest.mock import patch

import pytest

from agentwire.roles import RoleConfig


# Use a minimal mock for the config dict returned by load_config() in __main__
FAKE_CONFIG = {
    "zai": {
        "api_key": "test-key-123",
        "base_url": "https://api.z.ai/api/anthropic",
        "opus_model": "glm-5",
        "sonnet_model": "glm-5",
        "haiku_model": "glm-4.7-flash",
        "timeout_ms": 3000000,
    }
}


@pytest.fixture(autouse=True)
def mock_main_load_config():
    """Mock the __main__.load_config (dict version) for claudeglm tests."""
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

    def test_claudeglm_bypass(self):
        cmd = self._build("claudeglm-bypass")
        assert "claude" in cmd.command
        assert "ANTHROPIC_BASE_URL" in cmd.command
        assert "ANTHROPIC_AUTH_TOKEN" in cmd.command
        assert "--dangerously-skip-permissions" in cmd.command

    def test_claudeglm_restricted(self):
        cmd = self._build("claudeglm-restricted")
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
        # Clean up temp file
        if cmd.temp_file:
            os.unlink(cmd.temp_file)

    def test_restricted_ignores_role_flags(self):
        """claude-restricted should not get role tools/instructions."""
        roles = [RoleConfig(name="test", tools=["Read"], instructions="Hello")]
        cmd = self._build("claude-restricted", roles=roles)
        # Should only have --tools Bash from restricted, not from role
        assert "--append-system-prompt" not in cmd.command

    def test_unknown_type_empty(self):
        cmd = self._build("nonexistent-type")
        assert cmd.command == ""
