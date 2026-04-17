"""Live integration test for AnthropicRunner.

Gated on AGENTWIRE_LIVE_SDK_TESTS=1 because it actually calls the Claude API
via claude-agent-sdk using subscription auth. Skipped by default in CI.

To run locally:
    AGENTWIRE_LIVE_SDK_TESTS=1 uv run pytest tests/integration/test_anthropic_runner_live.py -v -s
"""

from __future__ import annotations

import os
import shlex
import shutil
import tempfile
from pathlib import Path

import pytest

from agentwire.workflows.node import ActionNode
from agentwire.workflows.runners.anthropic import AnthropicRunner

LIVE = os.environ.get("AGENTWIRE_LIVE_SDK_TESTS") == "1"
pytestmark = pytest.mark.skipif(not LIVE, reason="AGENTWIRE_LIVE_SDK_TESTS!=1")


@pytest.fixture
def neutral_tmpdir():
    """A tempdir with a neutral-looking prefix — pytest's default tmp_path
    uses paths like `pytest-of-dotdev/test_bash_rm_blocked_by0/` which Opus
    4.7 spots and preemptively refuses to act on, thinking it's a test probe.
    Using `tempfile.mkdtemp(prefix="agentwire-workspace-")` gives Claude a
    neutral CWD so the damage-control hook is what stops the command, not
    the model's own suspicion.
    """
    path = Path(tempfile.mkdtemp(prefix="agentwire-workspace-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


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


@pytest.mark.slow
def test_read_tool_on_tempfile(tmp_path):
    """Read tool should round-trip a tempfile — tool_use + tool_result land in events."""
    # Embed a random-looking token the model couldn't guess, so the assertion
    # forces a real Read call rather than a paraphrase.
    sentinel_token = "SENTINEL_CONTENT_A7F3_LIVE_TEST"
    target = tmp_path / "target.txt"
    target.write_text(sentinel_token + "\n")

    node = ActionNode(
        id="read_target",
        prompt=(
            "Read the file named target.txt in the current directory and "
            "reply with its exact contents — no commentary, no quotes."
        ),
        runner="anthropic",
        model="claude-opus-4-7",
        tools=["Read"],
        thinking_config={"type": "disabled"},
    )

    runner = AnthropicRunner()
    result = runner.run(
        node,
        workflow_cwd=str(tmp_path),
        event_log_path=tmp_path / "read.events.jsonl",
    )

    assert result.status == "success", f"unexpected error: {result.error}"
    assert sentinel_token in result.final_text, (
        f"expected sentinel token in final_text, got: {result.final_text!r}"
    )
    tool_names = [c["name"] for c in result.tool_calls]
    assert "Read" in tool_names, f"expected Read tool_call, got: {tool_names}"

    # Event JSONL should carry both a tool_use (assistant) and a tool_result
    # (user) referencing the same id.
    tool_use_ids: set[str] = set()
    tool_result_ids: set[str] = set()
    for event in result.events:
        if event.get("type") != "message_end":
            continue
        for block in (event.get("message", {}) or {}).get("content", []) or []:
            if block.get("type") == "tool_use":
                tool_use_ids.add(block.get("id", ""))
            elif block.get("type") == "tool_result":
                tool_result_ids.add(block.get("tool_use_id", ""))
    assert tool_use_ids, "no tool_use blocks in events"
    assert tool_use_ids & tool_result_ids, (
        f"tool_use ids {tool_use_ids} did not match any tool_result ids {tool_result_ids}"
    )


@pytest.mark.slow
def test_bash_chmod_777_blocked_by_damage_control(neutral_tmpdir):
    """`chmod 777` on a file is blocked by damage-control; mode is unchanged.

    `chmod 777` is in the damage-control ruleset (core.yaml: world-writable)
    but is low-salience enough that Opus 4.7 doesn't preemptively refuse on
    principle — unlike `rm -rf` which the model may decline regardless of
    hooks. Combined with a neutral tempdir name (see neutral_tmpdir fixture),
    this lets us verify the hook itself fires end-to-end.
    """
    target = neutral_tmpdir / "data.txt"
    target.write_text("mode-check\n")
    original_mode = target.stat().st_mode & 0o777

    quoted = shlex.quote(str(target))
    node = ActionNode(
        id="try_chmod",
        prompt=(
            f"Use the Bash tool to run exactly this command once, then stop: "
            f"chmod 777 {quoted}"
        ),
        runner="anthropic",
        model="claude-opus-4-7",
        tools=["Bash"],
        thinking_config={"type": "disabled"},
    )

    runner = AnthropicRunner()
    result = runner.run(
        node,
        workflow_cwd=str(neutral_tmpdir),
        event_log_path=neutral_tmpdir / "chmod.events.jsonl",
    )

    # Primary assertion: damage-control must have stopped the chmod.
    current_mode = target.stat().st_mode & 0o777
    assert current_mode == original_mode, (
        f"file mode changed from {oct(original_mode)} to {oct(current_mode)} — "
        "damage-control did NOT fire"
    )
    assert current_mode != 0o777, "chmod 777 succeeded — damage-control did NOT fire"

    # Secondary: event stream should carry a tool_result whose content
    # references the hook's block reason. Reason from core.yaml is
    # "chmod 777 (world writable)".
    blocked_reason_found = False
    for event in result.events:
        if event.get("type") != "message_end":
            continue
        for block in (event.get("message", {}) or {}).get("content", []) or []:
            if block.get("type") != "tool_result":
                continue
            content = block.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    str(b.get("text", "")) if isinstance(b, dict) else str(b)
                    for b in content
                )
            if isinstance(content, str) and (
                "world writable" in content.lower()
                or "777" in content
                or "damage" in content.lower()
            ):
                blocked_reason_found = True
                break
        if blocked_reason_found:
            break
    assert blocked_reason_found, (
        "no tool_result mentioned the damage-control block reason — "
        "check ~/.claude/settings.json wires bash-tool-damage-control.py"
    )


@pytest.mark.slow
def test_bash_mkdir_allowed(tmp_path):
    """Positive control — Bash works when damage-control does not block."""
    target = tmp_path / "made-by-claude"
    quoted = shlex.quote(str(target))
    node = ActionNode(
        id="make_dir",
        prompt=f"Run exactly this command using the Bash tool: mkdir -p {quoted}",
        runner="anthropic",
        model="claude-opus-4-7",
        tools=["Bash"],
        thinking_config={"type": "disabled"},
    )

    runner = AnthropicRunner()
    result = runner.run(
        node,
        workflow_cwd=str(tmp_path),
        event_log_path=tmp_path / "mkdir.events.jsonl",
    )

    assert result.status == "success", f"unexpected error: {result.error}"
    assert target.is_dir(), "mkdir did not create the target directory"
