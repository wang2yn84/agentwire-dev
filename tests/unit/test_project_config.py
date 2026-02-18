"""Tests for agentwire/project_config.py — SessionType, ProjectConfig, normalize."""

import pytest
import yaml
from pathlib import Path

from agentwire.project_config import (
    SessionType,
    ProjectConfig,
    normalize_session_type,
    find_project_config,
    load_project_config,
    save_project_config,
)


# --- SessionType.from_str ---

class TestSessionTypeFromStr:
    @pytest.mark.parametrize("input_val,expected", [
        ("bare", SessionType.BARE),
        ("claude-bypass", SessionType.CLAUDE_BYPASS),
        ("claude-prompted", SessionType.CLAUDE_PROMPTED),
        ("claude-restricted", SessionType.CLAUDE_RESTRICTED),
        ("claudeglm-bypass", SessionType.CLAUDEGLM_BYPASS),
        ("claudeglm-prompted", SessionType.CLAUDEGLM_PROMPTED),
        ("claudeglm-restricted", SessionType.CLAUDEGLM_RESTRICTED),
        ("standard", SessionType.STANDARD),
        ("worker", SessionType.WORKER),
        ("voice", SessionType.VOICE),
    ])
    def test_valid_types(self, input_val, expected):
        assert SessionType.from_str(input_val) == expected

    def test_case_insensitive(self):
        assert SessionType.from_str("CLAUDE-BYPASS") == SessionType.CLAUDE_BYPASS
        assert SessionType.from_str("Bare") == SessionType.BARE

    def test_underscore_to_hyphen(self):
        assert SessionType.from_str("claude_bypass") == SessionType.CLAUDE_BYPASS
        assert SessionType.from_str("CLAUDE_RESTRICTED") == SessionType.CLAUDE_RESTRICTED

    def test_unknown_defaults_to_standard(self):
        assert SessionType.from_str("nonexistent") == SessionType.STANDARD
        assert SessionType.from_str("") == SessionType.STANDARD


# --- SessionType.to_cli_flags ---

class TestSessionTypeToCliFlags:
    def test_bare_empty(self):
        assert SessionType.BARE.to_cli_flags() == []

    def test_bypass_has_skip_permissions(self):
        flags = SessionType.CLAUDE_BYPASS.to_cli_flags()
        assert "--dangerously-skip-permissions" in flags

    def test_prompted_no_flags(self):
        assert SessionType.CLAUDE_PROMPTED.to_cli_flags() == []

    def test_restricted_has_tools_bash(self):
        flags = SessionType.CLAUDE_RESTRICTED.to_cli_flags()
        assert flags == ["--tools", "Bash"]

    def test_standard_empty(self):
        # Universal types return empty (they need normalizing first)
        assert SessionType.STANDARD.to_cli_flags() == []


# --- normalize_session_type ---

class TestNormalizeSessionType:
    @pytest.mark.parametrize("universal,agent,expected", [
        ("standard", "claude", "claude-bypass"),
        ("standard", "claudeglm", "claudeglm-bypass"),
        ("worker", "claude", "claude-restricted"),
        ("worker", "claudeglm", "claudeglm-restricted"),
        ("voice", "claude", "claude-prompted"),
        ("voice", "claudeglm", "claudeglm-prompted"),
    ])
    def test_universal_mappings(self, universal, agent, expected):
        assert normalize_session_type(universal, agent) == expected

    @pytest.mark.parametrize("agent_specific", [
        "claude-bypass", "claude-prompted", "claude-restricted",
        "claudeglm-bypass", "claudeglm-prompted", "claudeglm-restricted",
        "bare",
    ])
    def test_agent_specific_passthrough(self, agent_specific):
        assert normalize_session_type(agent_specific, "claude") == agent_specific
        assert normalize_session_type(agent_specific, "claudeglm") == agent_specific

    def test_unknown_defaults_to_bypass(self):
        assert normalize_session_type("foobar", "claude") == "claude-bypass"
        assert normalize_session_type("foobar", "claudeglm") == "claudeglm-bypass"


# --- ProjectConfig ---

class TestProjectConfig:
    def test_from_dict_full(self):
        data = {
            "type": "claude-bypass",
            "roles": ["agentwire", "voice"],
            "voice": "dotdev",
            "parent": "main",
            "shell": "/bin/bash",
            "tasks": {"t1": {"prompt": "hello"}},
        }
        config = ProjectConfig.from_dict(data)
        assert config.type == SessionType.CLAUDE_BYPASS
        assert config.roles == ["agentwire", "voice"]
        assert config.voice == "dotdev"
        assert config.parent == "main"
        assert config.shell == "/bin/bash"
        assert "t1" in config.tasks

    def test_from_dict_defaults(self):
        config = ProjectConfig.from_dict({})
        assert config.type == SessionType.STANDARD
        assert config.roles == []
        assert config.voice is None
        assert config.parent is None
        assert config.shell is None
        assert config.tasks == {}

    def test_roles_string_to_list_coercion(self):
        config = ProjectConfig.from_dict({"roles": "agentwire"})
        assert config.roles == ["agentwire"]

    def test_roles_none_to_empty_list(self):
        config = ProjectConfig.from_dict({"roles": None})
        assert config.roles == []

    def test_to_dict_omits_none(self):
        config = ProjectConfig(type=SessionType.CLAUDE_BYPASS)
        d = config.to_dict()
        assert d == {"type": "claude-bypass"}
        assert "voice" not in d
        assert "parent" not in d
        assert "roles" not in d

    def test_to_dict_includes_populated(self):
        config = ProjectConfig(
            type=SessionType.WORKER,
            roles=["agentwire"],
            voice="dotdev",
        )
        d = config.to_dict()
        assert d["type"] == "worker"
        assert d["roles"] == ["agentwire"]
        assert d["voice"] == "dotdev"

    def test_round_trip(self):
        original = ProjectConfig(
            type=SessionType.CLAUDE_PROMPTED,
            roles=["voice", "worker"],
            voice="may",
            parent="main",
            shell="/bin/zsh",
        )
        d = original.to_dict()
        restored = ProjectConfig.from_dict(d)
        assert restored.type == original.type
        assert restored.roles == original.roles
        assert restored.voice == original.voice
        assert restored.parent == original.parent
        assert restored.shell == original.shell


# --- load/save/find_project_config ---

class TestProjectConfigIO:
    def test_load_from_directory(self, project_dir, project_config_file):
        config = load_project_config(project_dir)
        assert config is not None
        assert config.type == SessionType.CLAUDE_BYPASS
        assert "agentwire" in config.roles

    def test_load_from_file_path(self, project_config_file):
        config = load_project_config(project_config_file)
        assert config is not None
        assert config.type == SessionType.CLAUDE_BYPASS

    def test_load_missing_returns_none(self, tmp_path):
        config = load_project_config(tmp_path / "nonexistent")
        assert config is None

    def test_save_and_reload(self, project_dir):
        config = ProjectConfig(
            type=SessionType.VOICE,
            roles=["voice"],
            voice="echo",
        )
        assert save_project_config(config, project_dir) is True

        loaded = load_project_config(project_dir)
        assert loaded is not None
        assert loaded.type == SessionType.VOICE
        assert loaded.roles == ["voice"]
        assert loaded.voice == "echo"

    def test_find_walks_up_parents(self, tmp_path):
        # Create config in parent
        parent = tmp_path / "project"
        parent.mkdir()
        child = parent / "src" / "deep"
        child.mkdir(parents=True)

        config_path = parent / ".agentwire.yml"
        with open(config_path, "w") as f:
            yaml.safe_dump({"type": "bare"}, f)

        found = find_project_config(child)
        assert found is not None
        assert found == config_path

    def test_find_returns_none_when_absent(self, tmp_path):
        found = find_project_config(tmp_path)
        assert found is None
