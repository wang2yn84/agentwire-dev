# Worker Heartbeat Watchdog

**One-liner:** Automatic detection and recovery when workers silently fail, hang, or go off-track.

## Problem

Workers can fail in ways that aren't immediately visible to orchestrators:

1. **Silent hangs** - Worker appears active but is stuck in a loop or waiting indefinitely
2. **Context exhaustion** - Worker runs out of context and starts producing garbage
3. **Off-track drift** - Worker wanders into irrelevant work without reporting
4. **Crash without cleanup** - Worker dies before writing summary file
5. **Zombie panes** - tmux pane exists but agent process has exited

Currently, orchestrators rely on idle notifications and summary files. If a worker fails before reaching idle state, the orchestrator may wait indefinitely or assume work is still in progress.

## Proposed Solution

Implement a **heartbeat watchdog system** that monitors worker health through multiple signals and provides graduated responses.

### Health Signals

| Signal | How Detected | Indicates |
|--------|--------------|-----------|
| Process alive | `pgrep` in pane | Basic liveness |
| Output activity | `tmux capture-pane` diff | Agent producing output |
| Token burn rate | Output length over time | Active thinking vs. stuck |
| Progress markers | Regex for tool calls, file edits | Meaningful work |
| Context warnings | "context limit", "summarizing" | Approaching exhaustion |

### Watchdog States

```
HEALTHY → active output, process alive, recent progress
SLUGGISH → alive but no output for 60s
STUCK → alive but no progress markers for 3m
EXHAUSTED → context warning detected
DEAD → process exited without summary
```

### Graduated Responses

1. **SLUGGISH** → Log warning, continue monitoring
2. **STUCK** → Voice alert to orchestrator: "Worker 1 appears stuck on auth task. No progress for 3 minutes."
3. **EXHAUSTED** → Auto-inject: "CONTEXT LOW. Write summary to .agentwire/worker-{pane}.md and exit."
4. **DEAD** → Voice alert + auto-cleanup pane + mark task for retry

### Orchestrator Integration

New MCP tool for orchestrators:

```python
agentwire_worker_health(pane=1)
# Returns: {"state": "HEALTHY", "last_progress": "2m ago", "output_rate": "normal"}
```

New role instruction for leaders:

```markdown
## Worker Health Monitoring

The watchdog monitors your workers. You'll receive voice alerts for:
- STUCK workers (no progress for 3m)
- EXHAUSTED workers (context limit approaching)
- DEAD workers (crashed without summary)

On alert, decide: kill and respawn, inject guidance, or mark task blocked.
```

### Configuration

In `config.yaml`:

```yaml
watchdog:
  enabled: true
  check_interval: 30  # seconds
  thresholds:
    sluggish: 60      # no output
    stuck: 180        # no progress
    context_warn: ["context limit", "token limit", "summarizing"]
  responses:
    stuck: voice      # voice | alert | auto-kill
    exhausted: inject # inject summary request
    dead: cleanup     # cleanup pane, notify orchestrator
```

## Implementation Considerations

### Where It Runs

Option A: **Portal-side background task** - Portal already has WebSocket connections and session awareness. Add a periodic task that checks all active worker panes.

Option B: **tmux hook-based** - Use tmux's `pane-exited` hook for DEAD detection. Less overhead but only catches exits.

Recommendation: **Hybrid** - tmux hooks for exit detection, portal task for health monitoring.

### Progress Markers

Need to define what "progress" looks like for each agent type:

**Claude Code:**
- Tool calls: `Read`, `Edit`, `Write`, `Bash`
- Output patterns: "I'll", "Let me", file paths

**OpenCode:**
- Similar patterns, different formatting
- Look for `[tool]` blocks in output

### Context Injection

For EXHAUSTED state, need safe way to inject a message. Options:

1. `tmux send-keys` with the summary request
2. Write to a file the agent is instructed to check
3. Use existing `agentwire send --pane` mechanism

Option 3 is cleanest - reuse existing infrastructure.

### Race Conditions

Worker might complete naturally while watchdog is responding. Handle by:
- Check for summary file before taking action
- Use file locks for state transitions
- Idempotent responses (alerting twice is fine)

## Potential Challenges

1. **False positives** - Agent thinking deeply looks like "stuck". Need tunable thresholds per task type.

2. **Agent confusion** - Injected messages might confuse the agent's context. Need clear, unambiguous injection format.

3. **Resource overhead** - Polling every 30s for many workers adds load. Consider event-driven approach where possible.

4. **Cross-machine complexity** - For remote workers, health checks need SSH round-trips. May need local watchdog on each machine reporting to central portal.

5. **Different agent behaviors** - Claude Code and OpenCode have different output patterns. Need agent-specific progress detection.

## Success Metrics

- Reduce "lost worker" incidents (orchestrator waiting on dead worker) to zero
- Detect stuck workers within 3 minutes
- No false positive alerts on healthy workers doing complex reasoning
- Recovery time from worker failure < 1 minute

## Future Extensions

- **Predictive failure** - ML model trained on output patterns to predict failures before they happen
- **Auto-retry with context** - When respawning, inject summary of what previous worker attempted
- **Worker performance scoring** - Track which workers fail more often, inform spawning decisions
