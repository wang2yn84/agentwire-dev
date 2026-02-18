"""Integration tests for scheduler board load/save round-trip."""

import shutil
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from agentwire.scheduler import Board, TaskState, load_board, save_board

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def board_env(tmp_path):
    """Set up a scheduler board file and mock config to point at it."""
    board_path = tmp_path / "scheduler.yaml"
    shutil.copy(FIXTURES_DIR / "sample_scheduler.yaml", board_path)

    # Mock the scheduler config to use our temp path
    class FakeSchedulerConfig:
        board_file = board_path
        events_file = tmp_path / "events.jsonl"
        live_state_file = tmp_path / "live.json"
        git_timeout = 10
        git_op_timeout = 15
        gate_timeout = 10
        portal_notify_timeout = 5
        session_create_timeout = 30
        max_loop_sleep = 60

    with patch("agentwire.scheduler._sched_config", return_value=FakeSchedulerConfig()):
        yield board_path


class TestSchedulerBoardRoundTrip:
    def test_load_board(self, board_env):
        board = load_board()
        assert "code-quality" in board.tasks
        assert "doc-drift" in board.tasks
        assert "disabled-task" in board.tasks
        assert board.tasks["doc-drift"].filler is True
        assert board.tasks["disabled-task"].enabled is False

    def test_state_parsed(self, board_env):
        board = load_board()
        state = board.state.get("code-quality")
        assert state is not None
        assert state.last_status == "complete"
        assert state.run_count == 5
        assert state.last_duration == 120

    def test_round_trip_preserves_state(self, board_env):
        board = load_board()

        # Mutate state
        board.state["code-quality"] = TaskState(
            last_run=datetime(2026, 2, 1, 15, 0, 0, tzinfo=timezone.utc),
            last_status="failed",
            last_duration=300,
            run_count=6,
            last_summary="Something broke",
        )

        save_board(board)

        # Reload and verify
        board2 = load_board()
        state = board2.state["code-quality"]
        assert state.last_status == "failed"
        assert state.run_count == 6
        assert state.last_duration == 300
        assert state.last_summary == "Something broke"

        # Task definitions should be unchanged
        assert board2.tasks["code-quality"].interval == 3600
        assert board2.tasks["doc-drift"].filler is True
