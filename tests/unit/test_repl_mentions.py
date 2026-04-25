"""Tests for @-mention expansion (Phase 2 PR 3)."""

from __future__ import annotations

import pytest

from agentwire.repl.mentions import (
    MAX_FILE_BYTES,
    MAX_GLOB_FILES,
    expand_mentions,
)


@pytest.fixture
def project(tmp_path):
    """A small project tree to mention against."""
    (tmp_path / "README.md").write_text("# Project\nHello world.\n")
    (tmp_path / "main.py").write_text("def main():\n    return 1\n")
    sub = tmp_path / "src"
    sub.mkdir()
    (sub / "a.py").write_text("a = 1\n")
    (sub / "b.py").write_text("b = 2\n")
    return tmp_path


class TestNoExpansion:
    def test_no_at_sign(self, project):
        out, exp = expand_mentions("hello world", cwd=project)
        assert out == "hello world"
        assert exp == []

    def test_email_not_expanded(self, project):
        # foo@bar.com — `@` preceded by alphanum, no whitespace → ignored
        out, exp = expand_mentions("Email: foo@bar.com please", cwd=project)
        assert out == "Email: foo@bar.com please"
        assert exp == []

    def test_at_with_no_path_chars_ignored(self, project):
        # `@somebody` looks like an @-mention but has no `.` `/` glob
        out, exp = expand_mentions("Hi @somebody what's up", cwd=project)
        assert out == "Hi @somebody what's up"
        assert exp == []

    def test_inside_backticks_skipped(self, project):
        out, exp = expand_mentions("the `@README.md` syntax expands files", cwd=project)
        assert "@README.md" in out
        assert "Hello world" not in out  # not expanded
        assert exp == []


class TestFileExpansion:
    def test_expands_file(self, project):
        out, exp = expand_mentions("look at @README.md", cwd=project)
        assert len(exp) == 1
        assert exp[0].raw == "@README.md"
        assert "Hello world" in out
        assert "# README.md" in out  # path comment in fence

    def test_missing_file_marker(self, project):
        out, exp = expand_mentions("see @nope.txt", cwd=project)
        assert "(not found)" in out
        assert len(exp) == 1

    def test_directory_listing(self, project):
        # `@src` (no path-y char) is heuristically treated as a non-path
        # to avoid colliding with @username mentions; users use `@src/`
        # or `@./src` to reference a directory.
        out, exp = expand_mentions("contents of @src/", cwd=project)
        assert "directory" in out
        assert "a.py" in out and "b.py" in out

    def test_truncates_large_file(self, project):
        big = project / "big.txt"
        big.write_bytes(b"x" * (MAX_FILE_BYTES + 1000))
        out, exp = expand_mentions("@big.txt", cwd=project)
        assert "truncated" in out

    def test_multiple_mentions(self, project):
        out, exp = expand_mentions("@README.md and @main.py side by side", cwd=project)
        assert len(exp) == 2
        assert "Hello world" in out
        assert "def main()" in out


class TestGlobs:
    def test_glob_under_limit_expands_each(self, project):
        out, exp = expand_mentions("@src/*.py", cwd=project)
        assert "a = 1" in out
        assert "b = 2" in out
        assert len(exp) == 1  # one mention, multiple files inlined

    def test_glob_over_limit_lists(self, project):
        # Make MAX_GLOB_FILES + 5 matching files
        for i in range(MAX_GLOB_FILES + 5):
            (project / "src" / f"big{i}.py").write_text(f"x{i}=1\n")
        out, exp = expand_mentions("@src/*.py", cwd=project)
        assert "matched" in out
        assert "tighter pattern" in out

    def test_glob_no_matches(self, project):
        out, exp = expand_mentions("@nothing/*.xyz", cwd=project)
        assert "no matches" in out


class TestFenceSelection:
    def test_avoids_collision_with_inner_backticks(self, project):
        bad = project / "tricky.md"
        bad.write_text("```code inside```\n")
        out, exp = expand_mentions("@tricky.md", cwd=project)
        # Outer fence must be at least 4 backticks since inner has 3.
        assert "````" in out

    def test_binary_file_marker(self, project):
        bin_path = project / "bin.dat"
        bin_path.write_bytes(b"\x00\x01\x02\xff")
        out, exp = expand_mentions("@bin.dat", cwd=project)
        assert "binary file" in out
