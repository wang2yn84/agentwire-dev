# Idea: Task Time Budgets

**Give orchestrators a sense of time — learned estimates, hard budgets, and auto-escalation when workers run long.**

## Problem

Orchestrators spawn workers and wait. But they have zero sense of how long a task *should* take. A worker editing a config file and a worker refactoring an auth system both look the same: "in progress." There's no way to distinguish "normal pace" from "silently stuck."

This creates real problems:

- **Invisible stuck workers.** A worker hits a loop (retrying a failing test, fighting a type error) and burns tokens for 45 minutes. The orchestrator doesn't know until it manually checks output.
- **No historical baseline.** "Fix the nav links" took 3 minutes last time. This time it's been 20 minutes. Something is wrong — but nobody tracks this.
- **Can't scope delegation.** When the orchestrator says "implement dark mode," there's no way to say "spend at most 15 minutes, then report back even if incomplete." Workers work until done or stuck, with no middle ground.
- **Wasted compute.** The most expensive failure mode is a worker spinning on a dead-end approach for an hour when a 2-minute conversation with the orchestrator would have unblocked it.

## Proposed Solution

### 1. Task Time Annotations

Allow optional time budgets when delegating tasks:

```
agentwire_pane_send(pane=1, message="Fix the broken nav links. Budget: 10m")
```

The system parses `Budget: Xm` from task messages and tracks elapsed time against it.

### 2. Learned Estimates

Track task durations over time in a local SQLite database (`~/.agentwire/task-history.db`):

```sql
CREATE TABLE task_durations (
  id INTEGER PRIMARY KEY,
  session TEXT,
  task_summary TEXT,       -- first 100 chars of task message
  task_hash TEXT,          -- fuzzy hash for matching similar tasks
  started_at TIMESTAMP,
  completed_at TIMESTAMP,
  duration_seconds INTEGER,
  status TEXT,             -- done, stuck, escalated, timeout
  worker_type TEXT         -- claude, glm
);
```

Over time, the system builds a baseline: "tasks mentioning 'nav' average 4 minutes, tasks mentioning 'auth' average 22 minutes."

When a new task is sent, the orchestrator gets a whispered estimate:

```
[estimate] Similar tasks averaged 8m (range: 3m-15m)
```

### 3. Budget Enforcement Tiers

Three escalation levels, configurable per task:

| Tier | Trigger | Action |
|------|---------|--------|
| **Soft warning** | 80% of budget | Alert to orchestrator: "Pane 1 at 8m/10m budget" |
| **Hard warning** | 100% of budget | Alert + capture worker's last 30 lines of output |
| **Auto-escalate** | 150% of budget | Send worker a nudge: "You've exceeded your time budget. Summarize progress and blockers now." |

The orchestrator can then decide: extend the budget, pivot the approach, or kill and reassign.

### 4. CLI & MCP Integration

```bash
# View task timing for current session
agentwire timing list
# Output:
# Pane 1: "Fix nav links"     - 4m12s (budget: 10m) ██░░░░ 42%
# Pane 2: "Add dark mode"     - 18m03s (budget: 20m) █████░ 90% ⚠️

# View historical averages
agentwire timing history --similar "auth"
# Output:
# Average: 22m | Median: 18m | P90: 41m | Tasks: 14

# Set default budget for a session
agentwire timing default 15m
```

MCP tools for orchestrators:

```python
agentwire_timing_status()          # All active pane timers
agentwire_timing_set(pane=1, budget="15m")  # Set/update budget
agentwire_timing_extend(pane=1, minutes=10) # Grant more time
```

### 5. Worker-Side Awareness

Workers see their own budget in the task message. The nudge at 150% asks them to write a structured checkpoint:

```markdown
## Time Budget Checkpoint
- **Task:** Fix nav links
- **Progress:** Found the issue (missing id attributes), fixed 2 of 4 links
- **Remaining:** 2 more links + testing
- **Blocker:** None, just needs more time
- **Estimate to complete:** 5 more minutes
```

This gives the orchestrator actionable information without killing the worker.

## Implementation Considerations

- **Storage:** SQLite is lightweight and local. No external dependencies.
- **Budget parsing:** Simple regex on task messages (`Budget:\s*(\d+)m`). Non-invasive — tasks without budgets work exactly as before.
- **Fuzzy matching:** Use trigram similarity on task summaries to find comparable historical tasks. Don't need ML — simple text matching works for developer tasks.
- **Timer accuracy:** Track wall-clock time from `pane_send` to worker idle/summary. Don't try to measure "active" vs "thinking" time — wall clock is what matters for cost.
- **Alert routing:** Use the existing `agentwire alert` queue system. No new notification infrastructure needed.

## Potential Challenges

- **Task variance.** "Fix auth" could mean a typo or a redesign. Historical averages may mislead. Mitigation: show range (P10-P90), not just average. Let orchestrators override estimates.
- **Budget pressure.** Workers might rush and produce lower-quality work. Mitigation: frame budgets as "check in at this point" rather than "stop working." The nudge asks for a checkpoint, not a hard stop.
- **Cold start.** No history means no estimates for the first few weeks. Mitigation: system is purely additive — works fine with zero history, gets better over time.
- **Noisy alerts.** If every task has a budget, orchestrators get flooded with timing alerts. Mitigation: only alert on warnings (80%+), not on normal completion. Make budgets opt-in per task.
