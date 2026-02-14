"""Centralized task scheduler daemon.

Reads a board of registered tasks from ~/.agentwire/scheduler.yaml,
picks the most overdue one, dispatches it via `agentwire ensure`,
updates the board, and loops. No AI — pure subprocess management
and time math.
"""

import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml


# Board file location
BOARD_PATH = Path.home() / ".agentwire" / "scheduler.yaml"

# tmux session name for the scheduler daemon
SCHEDULER_SESSION = "agentwire-scheduler"

# Exit codes from ensure (must match __main__.py constants)
_EXIT_COMPLETE = 0
_EXIT_FAILED = 1
_EXIT_INCOMPLETE = 2
_EXIT_LOCK_CONFLICT = 3
_EXIT_PRE_FAILURE = 4
_EXIT_TIMEOUT = 5
_EXIT_SESSION_ERROR = 6

_EXIT_TO_STATUS = {
    _EXIT_COMPLETE: "complete",
    _EXIT_FAILED: "failed",
    _EXIT_INCOMPLETE: "incomplete",
    _EXIT_LOCK_CONFLICT: "lock_conflict",
    _EXIT_PRE_FAILURE: "failed",
    _EXIT_TIMEOUT: "timeout",
    _EXIT_SESSION_ERROR: "failed",
}


@dataclass
class SchedulerTask:
    name: str
    project: str          # ~/projects/foo (expanded at load time)
    session: str          # session name for ensure
    task: str             # task name in project's .agentwire.yml
    interval: int         # seconds between runs
    enabled: bool = True
    filler: bool = False  # only runs in spare cycles
    priority: int = 99    # filler ordering (lower = higher)
    type: str | None = None  # session type override (e.g., opencode-bypass)


@dataclass
class TaskState:
    last_run: datetime | None = None
    last_status: str = "never"    # complete, failed, incomplete, timeout, lock_conflict, never
    last_duration: int = 0
    run_count: int = 0


@dataclass
class Board:
    tasks: dict[str, SchedulerTask] = field(default_factory=dict)
    state: dict[str, TaskState] = field(default_factory=dict)


def load_board() -> Board:
    """Load the scheduler board from YAML.

    Returns:
        Board with tasks and state populated.

    Raises:
        FileNotFoundError: If board file doesn't exist.
        ValueError: If board file is malformed.
    """
    if not BOARD_PATH.exists():
        raise FileNotFoundError(
            f"Board file not found: {BOARD_PATH}\n"
            f"Create it with task definitions. See docs/missions/later/master-ralph-loop.md for format."
        )

    with open(BOARD_PATH) as f:
        raw = yaml.safe_load(f)

    if not raw or not isinstance(raw, dict):
        raise ValueError(f"Board file is empty or malformed: {BOARD_PATH}")

    raw_tasks = raw.get("tasks", {})
    if not raw_tasks:
        raise ValueError(f"No tasks defined in board: {BOARD_PATH}")

    board = Board()

    for name, t in raw_tasks.items():
        if not isinstance(t, dict):
            continue
        board.tasks[name] = SchedulerTask(
            name=name,
            project=str(Path(t.get("project", "")).expanduser()),
            session=t.get("session", name),
            task=t.get("task", name),
            interval=int(t.get("interval", 3600)),
            enabled=bool(t.get("enabled", True)),
            filler=bool(t.get("filler", False)),
            priority=int(t.get("priority", 99)),
            type=t.get("type"),
        )

    raw_state = raw.get("state", {})
    if raw_state and isinstance(raw_state, dict):
        for name, s in raw_state.items():
            if not isinstance(s, dict):
                continue
            last_run = None
            raw_lr = s.get("last_run")
            if raw_lr:
                if isinstance(raw_lr, datetime):
                    last_run = raw_lr
                else:
                    try:
                        last_run = datetime.fromisoformat(str(raw_lr))
                    except (ValueError, TypeError):
                        pass

            board.state[name] = TaskState(
                last_run=last_run,
                last_status=str(s.get("last_status", "never")),
                last_duration=int(s.get("last_duration", 0)),
                run_count=int(s.get("run_count", 0)),
            )

    return board


def save_board(board: Board) -> None:
    """Save board state back to YAML (preserves task definitions, rewrites state)."""
    if not BOARD_PATH.exists():
        return

    with open(BOARD_PATH) as f:
        raw = yaml.safe_load(f) or {}

    # Only update the state section
    state_dict = {}
    for name, s in board.state.items():
        state_dict[name] = {
            "last_run": s.last_run.isoformat() if s.last_run else None,
            "last_status": s.last_status,
            "last_duration": s.last_duration,
            "run_count": s.run_count,
        }

    raw["state"] = state_dict

    with open(BOARD_PATH, "w") as f:
        yaml.dump(raw, f, default_flow_style=False, sort_keys=False)


def _get_last_run_ts(board: Board, task_name: str) -> float:
    """Get last run as a Unix timestamp (0 if never run)."""
    state = board.state.get(task_name)
    if not state or not state.last_run:
        return 0.0
    # Convert to timestamp
    dt = state.last_run
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def pick_next_task(board: Board) -> tuple[str | None, float]:
    """Pick the next task to run based on overdue score.

    Algorithm:
    1. Score each enabled non-filler task by overdue_by = (now - last_run) - interval
    2. Most overdue wins
    3. If nothing overdue, check fillers (respecting their interval)
    4. If nothing at all, return sleep time until earliest task is due

    Returns:
        (task_name, wait_seconds) — task_name is None if nothing to do,
        wait_seconds is 0 if task should run now, >0 if should wait.
    """
    now = time.time()
    best_name: str | None = None
    best_score = float("-inf")

    # Score non-filler tasks
    for name, task in board.tasks.items():
        if not task.enabled or task.filler:
            continue
        last_run = _get_last_run_ts(board, name)
        overdue_by = (now - last_run) - task.interval
        if overdue_by > best_score:
            best_name = name
            best_score = overdue_by

    # If best task is overdue (score >= 0), run it now
    if best_name is not None and best_score >= 0:
        return best_name, 0.0

    # If best non-filler task exists but isn't overdue yet,
    # check fillers first (they might be ready)
    if best_score < 0:
        # Check fillers sorted by priority (lower = higher priority)
        fillers = sorted(
            [(n, t) for n, t in board.tasks.items() if t.filler and t.enabled],
            key=lambda x: x[1].priority,
        )
        for name, task in fillers:
            last_run = _get_last_run_ts(board, name)
            if (now - last_run) >= task.interval:
                return name, 0.0

    # Nothing to run now — calculate sleep until earliest task is due
    wait = seconds_until_next_due(board)
    return None, wait


def seconds_until_next_due(board: Board) -> float:
    """Calculate seconds until the earliest task is due.

    Returns:
        Seconds to wait (0 if something is already due, 60.0 max as fallback).
    """
    now = time.time()
    earliest_wait = float("inf")

    for name, task in board.tasks.items():
        if not task.enabled:
            continue
        last_run = _get_last_run_ts(board, name)
        next_due = last_run + task.interval
        wait = next_due - now
        if wait <= 0:
            return 0.0
        if wait < earliest_wait:
            earliest_wait = wait

    if earliest_wait == float("inf"):
        return 60.0  # No tasks, just re-check periodically

    return earliest_wait


def _ensure_session(task: SchedulerTask) -> None:
    """Create the session if it doesn't exist, using the specified type.

    If the session already exists, this is a no-op. If a type override
    is specified (e.g., opencode-bypass), the session is created with
    that type instead of the project's .agentwire.yml default.
    """
    # Check if session already exists
    check = subprocess.run(
        ["tmux", "has-session", "-t", f"={task.session}"],
        capture_output=True,
    )
    if check.returncode == 0:
        return  # Already running

    # Create with specified type or let agentwire new use project defaults
    cmd = ["agentwire", "new", "-s", task.session, "-p", task.project]
    if task.type:
        cmd.extend(["--type", task.type])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode == 0:
        print(f"[{_ts()}] Created session: {task.session} (type={task.type or 'default'})")
    else:
        print(f"[{_ts()}] Warning: Failed to create session {task.session}: {result.stderr.strip()}")


def _auto_commit(task: SchedulerTask, task_name: str, status: str) -> None:
    """Auto-commit any changes the task made in the project directory.

    Creates a standardized commit message so each task run is a single
    revertable commit. No-op if there are no changes to commit.
    """
    project = task.project

    # Check if there are any changes to commit
    check = subprocess.run(
        ["git", "-C", project, "status", "--porcelain"],
        capture_output=True, text=True, timeout=10,
    )
    if check.returncode != 0 or not check.stdout.strip():
        return  # Not a git repo or no changes

    # Stage all changes
    subprocess.run(
        ["git", "-C", project, "add", "-A"],
        capture_output=True, timeout=10,
    )

    # Commit with standardized message
    msg = f"ralph: {task_name} ({status})"
    subprocess.run(
        ["git", "-C", project, "commit", "-m", msg, "--no-verify"],
        capture_output=True, text=True, timeout=15,
    )
    print(f"[{_ts()}] Auto-committed: {msg}")


def dispatch_task(board: Board, task_name: str) -> TaskState:
    """Run a task via `agentwire ensure` and return updated state.

    Args:
        board: Current board (for reading task config).
        task_name: Name of the task to dispatch.

    Returns:
        Updated TaskState with results.
    """
    task = board.tasks[task_name]
    existing_state = board.state.get(task_name, TaskState())

    # Ensure session exists with the right type (e.g., opencode-bypass)
    _ensure_session(task)

    cmd = [
        "agentwire", "ensure",
        "-s", task.session,
        "--task", task.task,
        "--project", task.project,
        "--skip-if-locked",
        "--json",
    ]

    start_time = time.time()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 min hard limit for the subprocess
        )
        exit_code = result.returncode
    except subprocess.TimeoutExpired:
        exit_code = _EXIT_TIMEOUT
    except Exception:
        exit_code = _EXIT_FAILED

    duration = int(time.time() - start_time)
    status = _EXIT_TO_STATUS.get(exit_code, "failed")

    # On lock conflict, don't update last_run so task remains eligible
    if exit_code == _EXIT_LOCK_CONFLICT:
        return TaskState(
            last_run=existing_state.last_run,
            last_status="lock_conflict",
            last_duration=duration,
            run_count=existing_state.run_count,
        )

    # Auto-commit any changes the task made (each task = one revertable commit)
    _auto_commit(task, task_name, status)

    return TaskState(
        last_run=datetime.now(timezone.utc),
        last_status=status,
        last_duration=duration,
        run_count=existing_state.run_count + 1,
    )


def format_interval(seconds: int) -> str:
    """Format seconds into a human-readable interval string."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h{m}m" if m else f"{h}h"
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    return f"{d}d{h}h" if h else f"{d}d"


def format_overdue(seconds: float) -> str:
    """Format overdue seconds with +/- prefix."""
    prefix = "+" if seconds >= 0 else "-"
    abs_s = abs(int(seconds))
    return f"{prefix}{format_interval(abs_s)}"


def get_board_display(board: Board) -> list[dict]:
    """Get board data formatted for display.

    Returns:
        List of dicts with task info and computed scores.
    """
    now = time.time()
    rows = []

    for name, task in board.tasks.items():
        state = board.state.get(name, TaskState())
        last_run_ts = _get_last_run_ts(board, name)
        overdue_by = (now - last_run_ts) - task.interval

        # Format last run time
        if state.last_run:
            lr = state.last_run
            today = datetime.now().date()
            if lr.date() == today:
                last_run_str = lr.strftime("%H:%M")
            else:
                last_run_str = lr.strftime("%Y-%m-%d %H:%M")
        else:
            last_run_str = "never"

        label = name
        if task.filler:
            label = f"{name} (filler)"

        rows.append({
            "name": name,
            "label": label,
            "interval": task.interval,
            "interval_str": format_interval(task.interval),
            "last_run": last_run_str,
            "last_run_iso": state.last_run.isoformat() if state.last_run else None,
            "last_status": state.last_status,
            "last_duration": state.last_duration,
            "run_count": state.run_count,
            "overdue_by": round(overdue_by, 1),
            "overdue_str": format_overdue(overdue_by),
            "enabled": task.enabled,
            "filler": task.filler,
            "priority": task.priority,
            "session": task.session,
            "task": task.task,
            "project": task.project,
        })

    # Sort: enabled first, then by overdue (most overdue first)
    rows.sort(key=lambda r: (not r["enabled"], -r["overdue_by"]))
    return rows


def run_scheduler_loop() -> None:
    """Main scheduler daemon loop. Runs forever."""
    print(f"[{_ts()}] Scheduler starting...")
    print(f"[{_ts()}] Board: {BOARD_PATH}")

    try:
        board = load_board()
    except (FileNotFoundError, ValueError) as e:
        print(f"[{_ts()}] Error: {e}", file=sys.stderr)
        sys.exit(1)

    task_count = len(board.tasks)
    enabled_count = sum(1 for t in board.tasks.values() if t.enabled)
    print(f"[{_ts()}] Loaded {task_count} tasks ({enabled_count} enabled)")

    while True:
        try:
            board = load_board()
        except (FileNotFoundError, ValueError) as e:
            print(f"[{_ts()}] Board read error: {e}", file=sys.stderr)
            time.sleep(60)
            continue

        task_name, wait_seconds = pick_next_task(board)

        if task_name is None:
            sleep_time = min(wait_seconds, 60)
            print(f"[{_ts()}] Nothing due. Sleeping {int(sleep_time)}s...")
            time.sleep(sleep_time)
            continue

        if wait_seconds > 0:
            sleep_time = min(wait_seconds, 60)
            print(f"[{_ts()}] Next: {task_name} in {format_interval(int(wait_seconds))}. Sleeping {int(sleep_time)}s...")
            time.sleep(sleep_time)
            continue

        # Dispatch the task
        print(f"[{_ts()}] Running: {task_name}")
        state = dispatch_task(board, task_name)
        board.state[task_name] = state
        save_board(board)
        print(f"[{_ts()}] Done: {task_name} → {state.last_status} ({state.last_duration}s)")


def _ts() -> str:
    """Current timestamp for log lines."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
