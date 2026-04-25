"""Snapshot tests for the Textual REPL — Phase 3A.

Uses `pytest-textual-snapshot` to capture the rendered SVG of the app at
known states. Run with `pytest tests/snapshot/ --snapshot-update` to
generate or refresh baselines, then `pytest tests/snapshot/` to verify.

The snapshot suite is **opt-in via the `snapshots` marker**. Running it
in the same pytest invocation as the rest of the suite leaves Python's
asyncio event-loop state polluted (Textual + pytest-textual-snapshot
share the loop with subsequent tests in a way that breaks anything
relying on `asyncio.get_event_loop()`). Until that's resolved upstream,
run snapshots in a separate invocation:

    pytest tests/snapshot/ -m snapshots

Snapshots live next to the test file (one per test) under
`__snapshots__/` and are committed to the repo. CI runs snapshots in
their own job; failures emit a diff page in the test output.

Add new snapshots for any visual change that lands in Phase 2/3+ —
layout adjustments, theme tweaks, modal screens, etc.
"""

from __future__ import annotations

from pathlib import Path

import pytest

APPS_DIR = Path(__file__).parent / "apps"


@pytest.mark.snapshots
def test_empty_boot_snapshot(snap_compare):
    """The empty REPL banner + chat + action + status + footer layout.

    Captures the initial state right after on_mount completes. Subsequent
    visual changes (theming, layout tweaks, new widgets) should show up
    here as a snapshot diff.
    """
    assert snap_compare(
        APPS_DIR / "repl_empty.py",
        terminal_size=(120, 35),
    )
