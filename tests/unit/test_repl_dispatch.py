"""Tests for `run_repl` dispatch — Phase 1A of the Textual rewrite.

Decision tree:
  1. `print_prompt is not None`         → `_run_print_mode`
  2. AGENTWIRE_REPL_TUI=textual + TTY   → `run_textual_repl`
  3. AGENTWIRE_REPL_TUI=textual + !TTY  → `_run_interactive`
  4. flag unset                         → `_run_interactive`
"""

from __future__ import annotations

import pytest


def _stub_async(*args, **kwargs):
    async def _coro():
        return 0
    return _coro()


def _record(calls: list[str], name: str):
    def _impl(*args, **kwargs):
        calls.append(name)

        async def _coro():
            return 0
        return _coro()
    return _impl


@pytest.fixture
def calls(monkeypatch):
    """Wires recording stubs over each impl branch and returns the call log."""
    log: list[str] = []
    from agentwire.repl import app

    monkeypatch.setattr(app, "_run_print_mode", _record(log, "print"))
    monkeypatch.setattr(app, "_run_interactive", _record(log, "interactive"))

    # textual_app is imported lazily inside run_repl, so patch the import target.
    import agentwire.repl.textual_app as textual_app
    monkeypatch.setattr(textual_app, "run_textual_repl", _record(log, "textual"))

    return log


class TestDispatch:
    def test_print_mode_short_circuits(self, calls, monkeypatch):
        monkeypatch.setenv("AGENTWIRE_REPL_TUI", "textual")  # ignored in print mode
        from agentwire.repl.app import run_repl

        run_repl(print_prompt="hello")
        assert calls == ["print"]

    def test_flag_unset_routes_to_interactive(self, calls, monkeypatch):
        monkeypatch.delenv("AGENTWIRE_REPL_TUI", raising=False)
        from agentwire.repl.app import run_repl

        run_repl()
        assert calls == ["interactive"]

    def test_flag_on_tty_routes_to_textual(self, calls, monkeypatch):
        monkeypatch.setenv("AGENTWIRE_REPL_TUI", "textual")
        from agentwire.repl import app

        # Both stdin and stdout must be a TTY.
        monkeypatch.setattr(app.sys.stdin, "isatty", lambda: True, raising=False)
        monkeypatch.setattr(app.sys.stdout, "isatty", lambda: True, raising=False)

        app.run_repl()
        assert calls == ["textual"]

    def test_flag_on_non_tty_falls_back_to_interactive(self, calls, monkeypatch):
        monkeypatch.setenv("AGENTWIRE_REPL_TUI", "textual")
        from agentwire.repl import app

        monkeypatch.setattr(app.sys.stdin, "isatty", lambda: False, raising=False)
        monkeypatch.setattr(app.sys.stdout, "isatty", lambda: True, raising=False)

        app.run_repl()
        assert calls == ["interactive"]

    def test_flag_on_stdout_only_falls_back(self, calls, monkeypatch):
        # Both must be a TTY — stdout-only isn't enough.
        monkeypatch.setenv("AGENTWIRE_REPL_TUI", "textual")
        from agentwire.repl import app

        monkeypatch.setattr(app.sys.stdin, "isatty", lambda: True, raising=False)
        monkeypatch.setattr(app.sys.stdout, "isatty", lambda: False, raising=False)

        app.run_repl()
        assert calls == ["interactive"]

    def test_flag_other_values_dont_route(self, calls, monkeypatch):
        # Only "textual" enables the new path. Other values are ignored.
        monkeypatch.setenv("AGENTWIRE_REPL_TUI", "1")
        from agentwire.repl.app import run_repl

        run_repl()
        assert calls == ["interactive"]

    def test_flag_case_insensitive(self, calls, monkeypatch):
        monkeypatch.setenv("AGENTWIRE_REPL_TUI", "TEXTUAL")
        from agentwire.repl import app

        monkeypatch.setattr(app.sys.stdin, "isatty", lambda: True, raising=False)
        monkeypatch.setattr(app.sys.stdout, "isatty", lambda: True, raising=False)

        app.run_repl()
        assert calls == ["textual"]


class TestTextualEntryPoint:
    """Phase 1B replaces the stub with an AgentwireREPL App. Verify the
    entry point exists and is async (without booting the actual TUI here —
    that lives in test_repl_textual_app.py)."""

    def test_run_textual_repl_exists(self):
        import inspect
        from agentwire.repl.textual_app import run_textual_repl

        assert inspect.iscoroutinefunction(run_textual_repl)

    def test_app_class_exposed(self):
        from agentwire.repl.textual_app import AgentwireREPL

        # Just verify the class is importable; full lifecycle tests are in
        # test_repl_textual_app.py.
        assert AgentwireREPL.__name__ == "AgentwireREPL"


class TestImportFallback:
    """If textual import fails, dispatcher falls back to interactive."""

    def test_missing_textual_falls_back(self, calls, monkeypatch, capsys):
        monkeypatch.setenv("AGENTWIRE_REPL_TUI", "textual")
        from agentwire.repl import app

        monkeypatch.setattr(app.sys.stdin, "isatty", lambda: True, raising=False)
        monkeypatch.setattr(app.sys.stdout, "isatty", lambda: True, raising=False)

        # Simulate textual not being importable.
        import builtins
        real_import = builtins.__import__

        def _block_textual(name, *args, **kwargs):
            if name == "textual":
                raise ImportError("textual not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _block_textual)

        app.run_repl()
        assert calls == ["interactive"]
        err = capsys.readouterr().err
        assert "AGENTWIRE_REPL_TUI=textual but textual is not installed" in err
