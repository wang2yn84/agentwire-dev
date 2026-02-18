"""Tests for agentwire/scheduler.py — Format helpers, pick logic, board I/O."""

import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from agentwire.scheduler import (
    Board,
    SchedulerTask,
    TaskState,
    format_interval,
    format_overdue,
    pick_next_task,
    _EXIT_TO_STATUS,
)


# --- format_interval ---

class TestFormatInterval:
    @pytest.mark.parametrize("seconds,expected", [
        (0, "0s"),
        (30, "30s"),
        (45, "45s"),
        (59, "59s"),
        (60, "1m"),
        (120, "2m"),
        (3600, "1h"),
        (3660, "1h1m"),
        (7200, "2h"),
        (86400, "1d"),
        (90000, "1d1h"),
        (172800, "2d"),
    ])
    def test_formatting(self, seconds, expected):
        assert format_interval(seconds) == expected


# --- format_overdue ---

class TestFormatOverdue:
    def test_positive(self):
        result = format_overdue(3600.0)
        assert result == "+1h"

    def test_negative(self):
        result = format_overdue(-1800.0)
        assert result == "-30m"

    def test_zero(self):
        result = format_overdue(0.0)
        assert result == "+0s"

    def test_small_positive(self):
        result = format_overdue(45.0)
        assert result == "+45s"


# --- _EXIT_TO_STATUS mapping ---

class TestExitCodeMapping:
    @pytest.mark.parametrize("code,status", [
        (0, "complete"),
        (1, "failed"),
        (2, "incomplete"),
        (3, "lock_conflict"),
        (4, "failed"),      # pre-failure mapped to failed
        (5, "timeout"),
        (6, "failed"),      # session-error mapped to failed
    ])
    def test_exit_to_status(self, code, status):
        assert _EXIT_TO_STATUS[code] == status


# --- pick_next_task ---

class TestPickNextTask:
    def _make_board(self, tasks_and_states):
        """Helper: build a Board from list of (name, interval, enabled, filler, last_run_ts)."""
        board = Board()
        for name, interval, enabled, filler, last_run_ts in tasks_and_states:
            board.tasks[name] = SchedulerTask(
                name=name,
                project="/tmp/test",
                session=name,
                task=name,
                interval=interval,
                enabled=enabled,
                filler=filler,
            )
            if last_run_ts > 0:
                dt = datetime.fromtimestamp(last_run_ts, tz=timezone.utc)
                board.state[name] = TaskState(last_run=dt, last_status="complete")
        return board

    @patch("agentwire.scheduler._check_gate", return_value=True)
    def test_most_overdue_wins(self, mock_gate):
        now = time.time()
        board = self._make_board([
            ("task-a", 3600, True, False, now - 7200),  # 1h overdue
            ("task-b", 3600, True, False, now - 10800), # 2h overdue
        ])
        name, wait = pick_next_task(board)
        assert name == "task-b"  # More overdue
        assert wait == 0.0

    @patch("agentwire.scheduler._check_gate", return_value=True)
    def test_disabled_skipped(self, mock_gate):
        now = time.time()
        board = self._make_board([
            ("enabled-task", 60, True, False, now - 120),
            ("disabled-task", 60, False, False, now - 120),
        ])
        name, wait = pick_next_task(board)
        assert name == "enabled-task"

    @patch("agentwire.scheduler._check_gate", return_value=True)
    def test_fillers_after_main(self, mock_gate):
        now = time.time()
        board = self._make_board([
            ("main-task", 3600, True, False, now - 60),  # Not overdue (1h interval, 60s ago)
            ("filler-task", 60, True, True, now - 120),   # Overdue filler
        ])
        name, wait = pick_next_task(board)
        assert name == "filler-task"

    @patch("agentwire.scheduler._check_gate", return_value=True)
    def test_nothing_due_returns_wait(self, mock_gate):
        now = time.time()
        board = self._make_board([
            ("task-a", 3600, True, False, now - 10),  # 3590s until due
        ])
        name, wait = pick_next_task(board)
        assert name is None
        assert wait > 0

    @patch("agentwire.scheduler._check_gate", return_value=True)
    def test_never_run_task_is_overdue(self, mock_gate):
        board = self._make_board([
            ("new-task", 3600, True, False, 0),  # Never run (ts=0)
        ])
        name, wait = pick_next_task(board)
        assert name == "new-task"
        assert wait == 0.0
