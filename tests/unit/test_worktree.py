"""Tests for agentwire/worktree.py — Session name parsing, paths."""

from pathlib import Path

import pytest

from agentwire.worktree import (
    parse_session_name,
    get_session_path,
    is_git_repo,
    get_project_type,
)


# --- parse_session_name ---

class TestParseSessionName:
    def test_simple(self):
        assert parse_session_name("myapp") == ("myapp", None, None)

    def test_with_branch(self):
        assert parse_session_name("myapp/feature") == ("myapp", "feature", None)

    def test_with_machine(self):
        assert parse_session_name("myapp@server") == ("myapp", None, "server")

    def test_with_branch_and_machine(self):
        assert parse_session_name("myapp/feature@server") == ("myapp", "feature", "server")

    def test_deep_branch(self):
        # "myapp/feat/sub" — first / splits project from branch
        project, branch, machine = parse_session_name("myapp/feat/sub")
        assert project == "myapp"
        assert branch == "feat/sub"
        assert machine is None


# --- get_session_path ---

class TestGetSessionPath:
    def test_simple_project(self):
        projects = Path("/home/user/projects")
        result = get_session_path("myapp", projects)
        assert result == Path("/home/user/projects/myapp")

    def test_worktree_with_suffix(self):
        projects = Path("/home/user/projects")
        result = get_session_path("myapp/feature", projects)
        assert result == Path("/home/user/projects/myapp-worktrees/feature")

    def test_custom_suffix(self):
        projects = Path("/home/user/projects")
        result = get_session_path("myapp/branch", projects, worktree_suffix="-wt")
        assert result == Path("/home/user/projects/myapp-wt/branch")

    def test_machine_ignored_in_path(self):
        projects = Path("/home/user/projects")
        result = get_session_path("myapp@server", projects)
        assert result == Path("/home/user/projects/myapp")


# --- is_git_repo ---

class TestIsGitRepo:
    def test_with_git_dir(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert is_git_repo(tmp_path) is True

    def test_without_git_dir(self, tmp_path):
        assert is_git_repo(tmp_path) is False


# --- get_project_type ---

class TestGetProjectType:
    def test_full_with_git(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert get_project_type(tmp_path) == "full"

    def test_scratch_without_git(self, tmp_path):
        assert get_project_type(tmp_path) == "scratch"
