> Living document. Update this, don't create new versions.

# Mission: AgentWire Scheduler

## Concept

A deterministic daemon that dispatches single-run tasks across projects on a shared cadence. No AI in the scheduler itself — it's a pure orchestrator that reads a board of registered tasks, picks the most overdue one, runs it via `agentwire ensure`, waits for completion, updates the board, and loops.

Each project defines its own tasks in `.agentwire.yml` as normal (`mode: standard`). The scheduler just calls them at the right time. Projects can still run their own independent ralph loops if they want — the scheduler is an optional centralized alternative for pacing work across a single shared resource (one machine, one API key, etc.).

## Why

- Long-running daemons with `loop_delay` work but each burns a session
- Multiple projects competing for the same API quota need coordination
- A single scheduler can pace work across projects, respecting each task's desired cadence
- "Spare" cycles can run filler tasks (housekeeping, social posting, audits)

## Architecture

```
agentwire scheduler (deterministic Python loop, runs in tmux session)
  │
  ├─ read ~/.agentwire/scheduler.yaml (the board)
  ├─ score tasks: how overdue is each? (now - last_run vs interval)
  ├─ pick most overdue task (or filler if nothing due)
  ├─ agentwire ensure -s {session} --task {task}
  ├─ wait for exit code
  ├─ update board: last_run, last_status, duration
  ├─ calculate sleep until next task is due
  ├─ sleep (or run filler)
  └─ loop
```

No AI, no prompts — just subprocess management and time math.

## The Board (`~/.agentwire/scheduler.yaml`)

```yaml
tasks:
  # Each entry references a project's .agentwire.yml task
  social-content-sweep:
    project: ~/projects/agentwire-social
    session: agentwire-social       # session name for ensure
    task: content-sweep             # task name in .agentwire.yml
    interval: 1800                  # seconds between runs (30 min)
    enabled: true

  dev-doc-drift:
    project: ~/projects/agentwire-dev
    session: agentwire-dev
    task: doc-drift-check
    interval: 86400                 # once per day
    enabled: true

  # Filler tasks — run when nothing else is due
  housekeeping:
    project: ~/projects/agentwire-dev
    session: agentwire-dev
    task: cleanup
    filler: true                    # only runs in spare cycles
    priority: 1                     # filler ordering (lower = higher priority)
    interval: 3600                  # minimum interval even as filler

state:
  # Auto-managed by scheduler, don't edit
  social-content-sweep:
    last_run: 2026-02-14T07:00:00
    last_status: complete           # complete, failed, incomplete, timeout
    last_duration: 480              # seconds
    run_count: 12
  dev-doc-drift:
    last_run: 2026-02-13T09:00:00
    last_status: complete
    last_duration: 120
    run_count: 3
```

## CLI Commands

```bash
agentwire scheduler start          # start scheduler daemon (tmux session)
agentwire scheduler stop           # stop scheduler
agentwire scheduler status         # show health, next task due, board summary
agentwire scheduler board          # show full board with overdue scores
agentwire scheduler add <name>     # register a task interactively
agentwire scheduler remove <name>  # unregister a task
agentwire scheduler enable <name>  # enable a task
agentwire scheduler disable <name> # disable without removing
agentwire scheduler run <name>     # force-run a specific task now
agentwire scheduler history        # show recent runs with status/duration
```

## Scheduling Logic

```python
def pick_next_task(board):
    now = time.time()
    best = None
    best_score = -inf

    for name, task in board.tasks.items():
        if not task.enabled or task.filler:
            continue
        state = board.state.get(name, {})
        last_run = parse_time(state.get("last_run", 0))
        overdue_by = (now - last_run) - task.interval
        if overdue_by > best_score:
            best = name
            best_score = overdue_by

    # If nothing is overdue, pick highest-priority filler
    # (only if its own interval has elapsed)
    if best_score < 0:
        for name, task in sorted(board.tasks.items(),
                                  key=lambda t: t[1].get("priority", 99)):
            if not task.filler or not task.enabled:
                continue
            state = board.state.get(name, {})
            last_run = parse_time(state.get("last_run", 0))
            if (now - last_run) >= task.interval:
                return name, 0  # run now
        # Nothing to do — sleep until earliest task is due
        return None, seconds_until_next_due(board)

    return best, max(0, -best_score)  # task name, seconds to wait
```

## Runs in tmux (Cross-Platform)

Same pattern as portal/TTS/STT:
- `agentwire scheduler start` → spawns `agentwire-scheduler` tmux session
- The session runs a Python process that loops forever
- Attach to watch it: `tmux attach -t agentwire-scheduler`
- Observable, killable, works on macOS and Linux

## MCP Tools

```
scheduler_status()              # health + next task
scheduler_board()               # full board with scores
scheduler_run(task="name")      # force-run a task
```

## Edge Cases

- **Task takes too long**: `ensure` has its own timeout. Scheduler gets exit code 5 (timeout), updates board status, moves on.
- **Session doesn't exist**: Scheduler creates it via `agentwire new` before running `ensure`.
- **Task fails**: Board records `last_status: failed`. Task still eligible for next scheduling cycle (it's overdue).
- **Multiple tasks overdue**: Most overdue wins. Fair scheduling over time.
- **Machine asleep/rebooted**: On startup, scheduler sees everything is overdue, runs the most overdue first, works through the backlog.
- **Concurrent with project loops**: Fine — scheduler uses `ensure` which has lock management. If a project's own loop is running, the lock prevents collision (exit code 3).

## Implementation Order

1. Board schema + YAML parsing
2. Scheduling logic (pick_next_task, sleep calculation)
3. Scheduler daemon (Python loop with ensure subprocess calls)
4. CLI commands (start/stop/status/board)
5. MCP tools (status/board/run)
6. `agentwire init` integration (optional board setup)

## Related

- `docs/missions/later/ralph-loop-use-cases.md` — brainstormed task ideas (fillers)
- `loop_delay` feature — per-task pacing (for independent project loops)
- `agentwire ensure` — existing task runner (scheduler wraps this)
- `[From:]` in session_send — inter-agent communication
