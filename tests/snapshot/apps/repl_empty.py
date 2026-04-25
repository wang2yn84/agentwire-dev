"""Snapshot fixture — empty REPL boot state.

Subclasses AgentwireREPL and overrides `_open_session` to skip the SDK +
persistence setup. The framework imports this module in-process to grab
the `app` reference, so we avoid patching `claude_agent_sdk` or
`persistence` globally — that would leak to other tests.
"""

from __future__ import annotations

from agentwire.repl.state import ReplState
from agentwire.repl.textual_app import AgentwireREPL


class _EmptyTranscript:
    """No-op transcript so on_unmount doesn't fail with AttributeError."""

    class _Path:
        def __str__(self):
            return "/tmp/agentwire-snapshot/session"

    def __init__(self):
        self.session_dir = self._Path()
        self.name = "snapshot-session"

    def write_event(self, event):
        pass

    def close(self):
        pass


class SnapshotREPL(AgentwireREPL):
    """REPL variant that skips SDK + persistence wiring for snapshots."""

    async def _open_session(self) -> None:
        self.state = ReplState(
            mode=self._cfg["mode"],
            model=self._cfg["model"] or "claude-opus-4-7",
            allowed_tools=["Read", "Bash", "Edit"],
        )
        self.state.role_names = ["agentwire", "voice"]
        self.state.voice = "default"
        self._transcript = _EmptyTranscript()
        self._render_banner()
        self._sink.write(
            "[transcript → /Users/dotdev/.agentwire/sessions/repl/snapshot-session]\n\n"
        )
        self._sink.flush()

    async def on_unmount(self) -> None:
        # Skip the parent's SDK + persistence cleanup (we never opened them).
        pass


app = SnapshotREPL(mode="bypass", model="claude-opus-4-7")

if __name__ == "__main__":
    app.run()
