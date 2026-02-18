"""Tests for agentwire/mcp_server.py — format functions, run_agentwire_cmd, helpers."""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Format functions
# ---------------------------------------------------------------------------


class TestFormatSessions:
    def setup_method(self):
        from agentwire.mcp_server import format_sessions
        self.fn = format_sessions

    def test_empty_sessions(self):
        assert self.fn({"sessions": []}) == "No active sessions."

    def test_missing_key(self):
        assert self.fn({}) == "No active sessions."

    def test_single_session_all_fields(self):
        data = {"sessions": [
            {"name": "my-app", "machine": "gpu-box", "windows": 3, "path": "/p", "type": "claude-bypass"},
        ]}
        result = self.fn(data)
        assert "my-app" in result
        assert "gpu-box" in result
        assert "3 window(s)" in result
        assert "type=claude-bypass" in result

    def test_multiple_sessions(self):
        data = {"sessions": [
            {"name": "a", "machine": None, "windows": 1, "path": "/a", "type": "bare"},
            {"name": "b", "machine": "m1", "windows": 2, "path": "/b", "type": "claude-bypass"},
        ]}
        result = self.fn(data)
        assert result.startswith("Active sessions:")
        lines = result.split("\n")
        assert len(lines) == 3

    def test_missing_optional_fields(self):
        data = {"sessions": [{"name": "x"}]}
        result = self.fn(data)
        assert "x (local)" in result
        assert "type=unknown" in result

    def test_null_machine_shows_local(self):
        data = {"sessions": [{"name": "x", "machine": None}]}
        result = self.fn(data)
        assert "local" in result

    def test_prefix_format(self):
        data = {"sessions": [{"name": "s"}]}
        assert "  - " in self.fn(data)


class TestFormatPanes:
    def setup_method(self):
        from agentwire.mcp_server import format_panes
        self.fn = format_panes

    def test_empty_panes(self):
        assert self.fn({"panes": [], "session": "test"}) == "No panes in session 'test'."

    def test_missing_key(self):
        assert "No panes" in self.fn({"session": "x"})

    def test_single_pane_orchestrator(self):
        data = {"panes": [{"index": 0, "command": "claude", "active": True}], "session": "s"}
        result = self.fn(data)
        assert "[orchestrator]" in result
        assert "(active)" in result

    def test_worker_pane(self):
        data = {"panes": [{"index": 1, "command": "bash"}], "session": "s"}
        result = self.fn(data)
        assert "[worker]" in result

    def test_multiple_panes(self):
        data = {"panes": [
            {"index": 0, "command": "claude", "active": False},
            {"index": 1, "command": "bash", "active": True},
        ], "session": "s"}
        result = self.fn(data)
        lines = result.split("\n")
        assert len(lines) == 3

    def test_missing_optional_fields(self):
        data = {"panes": [{}], "session": "s"}
        result = self.fn(data)
        assert "Pane 0" in result
        assert "unknown" in result

    def test_session_defaults_to_unknown(self):
        data = {"panes": [{"index": 0}]}
        result = self.fn(data)
        assert "unknown" in result


class TestFormatMachines:
    def setup_method(self):
        from agentwire.mcp_server import format_machines
        self.fn = format_machines

    def test_empty_machines(self):
        assert self.fn({"machines": []}) == "No remote machines configured."

    def test_missing_key(self):
        assert self.fn({}) == "No remote machines configured."

    def test_single_machine(self):
        data = {"machines": [{"id": "gpu", "host": "10.0.0.1", "user": "root", "status": "online"}]}
        result = self.fn(data)
        assert "root@10.0.0.1" in result
        assert "status: online" in result

    def test_machine_no_user(self):
        data = {"machines": [{"id": "m1", "host": "h", "user": "", "status": "unknown"}]}
        result = self.fn(data)
        assert "m1: h" in result
        assert "@" not in result.split("m1: ")[1].split(" ")[0]

    def test_multiple_machines(self):
        data = {"machines": [
            {"id": "a", "host": "1.1.1.1", "user": "u", "status": "ok"},
            {"id": "b", "host": "2.2.2.2", "user": "v", "status": "ok"},
        ]}
        lines = self.fn(data).split("\n")
        assert len(lines) == 3

    def test_missing_optional_fields(self):
        data = {"machines": [{}]}
        result = self.fn(data)
        assert "unknown" in result


class TestFormatProjects:
    def setup_method(self):
        from agentwire.mcp_server import format_projects
        self.fn = format_projects

    def test_empty_projects(self):
        assert self.fn({"projects": []}) == "No projects found."

    def test_missing_key(self):
        assert self.fn({}) == "No projects found."

    def test_project_with_config(self):
        data = {"projects": [{"name": "app", "path": "/app", "has_config": True}]}
        result = self.fn(data)
        assert "(has .agentwire.yml)" in result

    def test_project_without_config(self):
        data = {"projects": [{"name": "app", "path": "/app", "has_config": False}]}
        result = self.fn(data)
        assert ".agentwire.yml" not in result

    def test_multiple_projects(self):
        data = {"projects": [
            {"name": "a", "path": "/a"},
            {"name": "b", "path": "/b"},
        ]}
        lines = self.fn(data).split("\n")
        assert len(lines) == 3

    def test_missing_optional_fields(self):
        data = {"projects": [{}]}
        result = self.fn(data)
        assert "unknown" in result


class TestFormatRoles:
    def setup_method(self):
        from agentwire.mcp_server import format_roles
        self.fn = format_roles

    def test_empty_roles(self):
        assert self.fn({"roles": []}) == "No roles available."

    def test_missing_key(self):
        assert self.fn({}) == "No roles available."

    def test_single_role(self):
        data = {"roles": [{"name": "voice", "description": "Voice comms", "source": "bundled"}]}
        result = self.fn(data)
        assert "voice: Voice comms (bundled)" in result

    def test_multiple_roles(self):
        data = {"roles": [
            {"name": "a", "description": "da", "source": "s"},
            {"name": "b", "description": "db", "source": "s"},
        ]}
        lines = self.fn(data).split("\n")
        assert len(lines) == 3

    def test_missing_optional_fields(self):
        data = {"roles": [{}]}
        result = self.fn(data)
        assert "unknown" in result


class TestFormatVoices:
    def setup_method(self):
        from agentwire.mcp_server import format_voices
        self.fn = format_voices

    def test_empty_voices(self):
        assert "No custom voices" in self.fn({"voices": []})

    def test_missing_key(self):
        assert "No custom voices" in self.fn({})

    def test_dict_entries(self):
        data = {"voices": [{"name": "alice"}, {"name": "bob"}]}
        result = self.fn(data)
        assert "alice" in result
        assert "bob" in result

    def test_string_entries(self):
        data = {"voices": ["alice", "bob"]}
        result = self.fn(data)
        assert "alice" in result
        assert "bob" in result

    def test_mixed_entries(self):
        data = {"voices": [{"name": "alice"}, "bob"]}
        result = self.fn(data)
        assert "alice" in result
        assert "bob" in result

    def test_dict_missing_name(self):
        data = {"voices": [{}]}
        result = self.fn(data)
        assert "unknown" in result


# ---------------------------------------------------------------------------
# run_agentwire_cmd
# ---------------------------------------------------------------------------


class TestRunAgentwireCmd:
    def setup_method(self):
        from agentwire.mcp_server import run_agentwire_cmd
        self.fn = run_agentwire_cmd

    @patch("agentwire.mcp_server.subprocess.run")
    def test_successful_json(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"success": true, "data": 1}',
            stderr="",
        )
        result = self.fn(["list"])
        assert result == {"success": True, "data": 1}
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd == ["agentwire", "list", "--json"]

    @patch("agentwire.mcp_server.subprocess.run")
    def test_json_array_wrapping(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='[{"id": 1}, {"id": 2}]',
            stderr="",
        )
        result = self.fn(["history", "list"])
        assert result["success"] is True
        assert result["items"] == [{"id": 1}, {"id": 2}]

    @patch("agentwire.mcp_server.subprocess.run")
    def test_json_without_success_key(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"data": "hello"}',
            stderr="",
        )
        result = self.fn(["info"])
        assert result["success"] is True
        assert result["data"] == "hello"

    @patch("agentwire.mcp_server.subprocess.run")
    def test_json_parse_failure_falls_back(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="not json",
            stderr="",
        )
        result = self.fn(["list"])
        assert result["success"] is True
        assert result["output"] == "not json"

    @patch("agentwire.mcp_server.subprocess.run")
    def test_nonzero_returncode(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="session not found",
        )
        result = self.fn(["kill", "-s", "x"])
        assert result["success"] is False
        assert "session not found" in result["error"]

    @patch("agentwire.mcp_server.subprocess.run")
    def test_json_output_false(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="raw output here",
            stderr="",
        )
        result = self.fn(["say", "hello"], json_output=False)
        assert result["success"] is True
        assert result["output"] == "raw output here"
        cmd = mock_run.call_args[0][0]
        assert "--json" not in cmd

    @patch("agentwire.mcp_server.subprocess.run")
    def test_timeout_expired(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="agentwire", timeout=30)
        result = self.fn(["long-cmd"])
        assert result["success"] is False
        assert "timed out" in result["error"]

    @patch("agentwire.mcp_server.subprocess.run")
    def test_file_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        result = self.fn(["list"])
        assert result["success"] is False
        assert "not found" in result["error"]

    @patch("agentwire.mcp_server.subprocess.run")
    def test_command_construction(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
        self.fn(["new", "-s", "test"])
        cmd = mock_run.call_args[0][0]
        assert cmd == ["agentwire", "new", "-s", "test", "--json"]

    @patch("agentwire.mcp_server.subprocess.run")
    def test_custom_timeout(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
        self.fn(["spawn"], timeout=120)
        assert mock_run.call_args[1]["timeout"] == 120

    @patch("agentwire.mcp_server.subprocess.run")
    def test_generic_exception(self, mock_run):
        mock_run.side_effect = OSError("permission denied")
        result = self.fn(["list"])
        assert result["success"] is False
        assert "permission denied" in result["error"]

    @patch("agentwire.mcp_server.subprocess.run")
    def test_empty_stdout_nonzero(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
        result = self.fn(["x"])
        assert result["success"] is False


# ---------------------------------------------------------------------------
# get_portal_url
# ---------------------------------------------------------------------------


class TestGetPortalUrl:
    def setup_method(self):
        from agentwire.mcp_server import get_portal_url
        self.fn = get_portal_url

    def test_env_var(self, monkeypatch):
        monkeypatch.setenv("AGENTWIRE_PORTAL_URL", "https://custom:9999")
        assert self.fn() == "https://custom:9999"

    def test_config_file(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AGENTWIRE_PORTAL_URL", raising=False)
        config_dir = tmp_path / ".agentwire"
        config_dir.mkdir()
        config_file = config_dir / "config.yaml"
        import yaml
        config_file.write_text(yaml.dump({"portal": {"url": "https://from-config:1234"}}))
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        assert self.fn() == "https://from-config:1234"

    def test_default_fallback(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AGENTWIRE_PORTAL_URL", raising=False)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        assert self.fn() == "https://localhost:8765"

    def test_env_var_priority_over_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTWIRE_PORTAL_URL", "https://env:1111")
        config_dir = tmp_path / ".agentwire"
        config_dir.mkdir()
        import yaml
        (config_dir / "config.yaml").write_text(yaml.dump({"portal": {"url": "https://cfg:2222"}}))
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        assert self.fn() == "https://env:1111"

    def test_malformed_yaml(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AGENTWIRE_PORTAL_URL", raising=False)
        config_dir = tmp_path / ".agentwire"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(": : : bad yaml {{{{")
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        assert self.fn() == "https://localhost:8765"


# ---------------------------------------------------------------------------
# get_caller_session
# ---------------------------------------------------------------------------


class TestGetCallerSession:
    def setup_method(self):
        from agentwire.mcp_server import get_caller_session
        self.fn = get_caller_session

    def test_no_tmux_pane(self, monkeypatch):
        monkeypatch.delenv("TMUX_PANE", raising=False)
        assert self.fn() is None

    @patch("agentwire.mcp_server.subprocess.run")
    def test_returns_session_name(self, mock_run, monkeypatch):
        monkeypatch.setenv("TMUX_PANE", "%5")
        mock_run.return_value = MagicMock(returncode=0, stdout="my-session\n")
        assert self.fn() == "my-session"

    @patch("agentwire.mcp_server.subprocess.run")
    def test_empty_stdout(self, mock_run, monkeypatch):
        monkeypatch.setenv("TMUX_PANE", "%5")
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        assert self.fn() is None

    @patch("agentwire.mcp_server.subprocess.run")
    def test_nonzero_returncode(self, mock_run, monkeypatch):
        monkeypatch.setenv("TMUX_PANE", "%5")
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert self.fn() is None

    @patch("agentwire.mcp_server.subprocess.run")
    def test_timeout_expired(self, mock_run, monkeypatch):
        monkeypatch.setenv("TMUX_PANE", "%5")
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="tmux", timeout=2)
        assert self.fn() is None


# ---------------------------------------------------------------------------
# _portal_request
# ---------------------------------------------------------------------------


class TestPortalRequest:
    def setup_method(self):
        from agentwire.mcp_server import _portal_request
        self.fn = _portal_request

    @patch("agentwire.mcp_server.get_portal_url", return_value="https://localhost:8765")
    @patch("requests.get")
    def test_get_request(self, mock_get, mock_url):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True, "windows": []}
        mock_get.return_value = mock_resp
        result = self.fn("GET", "/api/desktop/windows")
        assert result == {"success": True, "windows": []}
        mock_get.assert_called_once_with(
            "https://localhost:8765/api/desktop/windows", verify=False, timeout=10
        )

    @patch("agentwire.mcp_server.get_portal_url", return_value="https://localhost:8765")
    @patch("requests.post")
    def test_post_request(self, mock_post, mock_url):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True}
        mock_post.return_value = mock_resp
        result = self.fn("POST", "/api/desktop/window/open", {"type": "session"})
        assert result["success"] is True
        mock_post.assert_called_once()

    @patch("agentwire.mcp_server.get_portal_url", return_value="https://localhost:8765")
    @patch("requests.get")
    def test_non_200_status(self, mock_get, mock_url):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_get.return_value = mock_resp
        result = self.fn("GET", "/api/health")
        assert result["success"] is False
        assert "500" in result["error"]

    @patch("agentwire.mcp_server.get_portal_url", return_value="https://localhost:8765")
    @patch("requests.get")
    def test_connection_error(self, mock_get, mock_url):
        import requests
        mock_get.side_effect = requests.exceptions.ConnectionError()
        result = self.fn("GET", "/api/health")
        assert result["success"] is False
        assert "not reachable" in result["error"]

    @patch("agentwire.mcp_server.get_portal_url", return_value="https://localhost:8765")
    @patch("requests.get")
    def test_generic_exception(self, mock_get, mock_url):
        mock_get.side_effect = Exception("timeout")
        result = self.fn("GET", "/api/health")
        assert result["success"] is False
        assert "timeout" in result["error"]

    @patch("agentwire.mcp_server.get_portal_url", return_value="https://localhost:8765")
    @patch("requests.post")
    def test_post_default_empty_body(self, mock_post, mock_url):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True}
        mock_post.return_value = mock_resp
        self.fn("POST", "/api/path")
        _, kwargs = mock_post.call_args
        assert kwargs["json"] == {}
