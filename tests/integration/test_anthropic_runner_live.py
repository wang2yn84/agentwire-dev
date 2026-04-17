"""Live integration test for AnthropicRunner.

Gated on AGENTWIRE_LIVE_SDK_TESTS=1 because it actually calls the Claude API
via claude-agent-sdk using subscription auth. Skipped by default in CI.

To run locally:
    AGENTWIRE_LIVE_SDK_TESTS=1 uv run pytest tests/integration/test_anthropic_runner_live.py -v -s
"""

from __future__ import annotations

import os

import pytest

from agentwire.workflows.node import ActionNode
from agentwire.workflows.runners.anthropic import AnthropicRunner

LIVE = os.environ.get("AGENTWIRE_LIVE_SDK_TESTS") == "1"
pytestmark = pytest.mark.skipif(not LIVE, reason="AGENTWIRE_LIVE_SDK_TESTS!=1")


@pytest.mark.slow
def test_hello_world_minimal_prompt(tmp_path):
    """Smallest possible live call — should return success + nonzero tokens."""
    node = ActionNode(
        id="hello",
        prompt="Reply with exactly the word: pong",
        runner="anthropic",
        model="claude-opus-4-7",
        thinking_config={"type": "disabled"},  # cheapest possible
    )

    runner = AnthropicRunner()
    result = runner.run(
        node,
        workflow_cwd=str(tmp_path),
        event_log_path=tmp_path / "hello.events.jsonl",
    )

    assert result.status == "success", f"unexpected error: {result.error}"
    assert result.final_text.strip(), "final_text should be populated"
    assert result.tokens_used.get("input", 0) > 0
    assert result.tokens_used.get("output", 0) > 0
    assert result.duration_ms > 0
    # Events JSONL should have landed on disk.
    assert (tmp_path / "hello.events.jsonl").is_file()
