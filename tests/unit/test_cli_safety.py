"""Tests for agentwire/cli_safety.py — Glob patterns, command safety checks."""

import pytest

from agentwire.cli_safety import (
    is_glob_pattern,
    glob_to_regex,
    matches_path_in_command,
    check_command_safety,
)


# --- is_glob_pattern ---

class TestIsGlobPattern:
    @pytest.mark.parametrize("pattern,expected", [
        ("*.py", True),
        ("file?.txt", True),
        ("[abc].txt", True),
        ("plain.txt", False),
        ("/path/to/file", False),
        ("no wildcards", False),
        ("", False),
    ])
    def test_detection(self, pattern, expected):
        assert is_glob_pattern(pattern) == expected


# --- glob_to_regex ---

class TestGlobToRegex:
    def test_star_to_dotstar(self):
        assert ".*" in glob_to_regex("*.txt")

    def test_question_to_dot(self):
        regex = glob_to_regex("file?.txt")
        assert "file." in regex

    def test_dots_escaped(self):
        regex = glob_to_regex("file.txt")
        assert "\\." in regex

    def test_bracket_preserved(self):
        regex = glob_to_regex("[abc].txt")
        assert "[abc]" in regex


# --- matches_path_in_command ---

class TestMatchesPathInCommand:
    def test_simple_path_match(self):
        assert matches_path_in_command("/etc/passwd", "cat /etc/passwd") is True

    def test_no_match(self):
        assert matches_path_in_command("/etc/passwd", "echo hello") is False

    def test_glob_matches_file(self):
        assert matches_path_in_command("*.py", 'rm test.py') is True

    def test_glob_avoids_method_calls(self):
        # module.py() should not match *.py in a method-call context
        assert matches_path_in_command("*.py", "python -c 'import module.py()'") is False


# --- check_command_safety ---

class TestCheckCommandSafety:
    def test_allowed_with_empty_patterns(self, tmp_path, monkeypatch):
        """If patterns.yaml doesn't exist, everything is allowed."""
        # Point PATTERNS_FILE to nonexistent path
        import agentwire.cli_safety as mod
        original = mod.PATTERNS_FILE
        monkeypatch.setattr(mod, "PATTERNS_FILE", tmp_path / "no-patterns.yaml")

        result = check_command_safety("echo hello")
        assert result["decision"] == "allow"

        monkeypatch.setattr(mod, "PATTERNS_FILE", original)

    def test_result_structure(self, tmp_path, monkeypatch):
        import agentwire.cli_safety as mod
        monkeypatch.setattr(mod, "PATTERNS_FILE", tmp_path / "no-patterns.yaml")

        result = check_command_safety("ls -la")
        assert "decision" in result
        assert "reason" in result
        assert "pattern" in result
        assert "command" in result
        assert result["command"] == "ls -la"
