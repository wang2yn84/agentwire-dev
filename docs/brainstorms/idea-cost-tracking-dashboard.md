# Cost Tracking Dashboard

**One-line summary:** Real-time visibility into API token costs across orchestrators and workers, with budgets, alerts, and cost-per-task breakdowns.

## Problem It Solves

When running multiple agents - orchestrators delegating to workers, parallel task execution, long-running sessions - costs accumulate invisibly:

1. **No visibility** - You don't know what a session costs until the API bill arrives
2. **Runaway workers** - A worker stuck in a loop burns tokens with no warning
3. **Unclear delegation value** - Is it actually cheaper to delegate to GLM vs doing it in Claude?
4. **Budget surprises** - "This feature cost $47 in API calls" is learned retroactively
5. **No cost attribution** - Can't tell which task/feature consumed what

The voice-first, autonomous nature of AgentWire amplifies this - agents work in the background, and without visual feedback, cost accumulation is invisible.

## Proposed Solution

**Cost Tracking Dashboard** - A system that tracks token usage in real-time across all sessions, provides cost attribution, and alerts on budget thresholds.

### 1. Token Capture Layer

Intercept API calls at the agent level:

```yaml
# Captured per API call
call:
  session: "api-server"
  pane: 1
  model: "claude-sonnet-4-20250514"
  input_tokens: 4523
  output_tokens: 892
  timestamp: "2024-01-15T10:30:00"
  tool_calls: ["Read", "Edit", "Edit"]
  task_context: "fix-auth-bug"  # From active task if available
```

**Capture methods:**

For **Claude Code**:
- Parse `~/.claude/logs/` for token counts (already logged)
- Or: Custom hook that logs to `.agentwire/costs/`

For **OpenCode**:
- Parse OpenCode's native logging
- Or: OpenCode plugin writes cost events

### 2. Cost Calculation

Map tokens to dollars using model pricing:

```python
PRICING = {
    # Per 1M tokens
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "claude-opus-4-5-20250514": {"input": 15.00, "output": 75.00},
    "claude-haiku-3-5-20241022": {"input": 0.80, "output": 4.00},
    "glm-4-flash": {"input": 0.007, "output": 0.007},  # Very cheap
}

def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rates = PRICING[model]
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000
```

### 3. Real-Time Dashboard

Portal web UI shows:

```
┌─────────────────────────────────────────────────────────────┐
│ AgentWire Cost Dashboard                    Today: $12.47   │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│ Sessions                        │ Last Hour        │ Total  │
│ ─────────────────────────────────────────────────────────── │
│ api-server (orchestrator)       │ $3.21 ████░░░░  │ $8.92  │
│   └─ pane 1 (glm-worker)        │ $0.04 ░░░░░░░░  │ $0.12  │
│   └─ pane 2 (glm-worker)        │ $0.02 ░░░░░░░░  │ $0.08  │
│ frontend (orchestrator)         │ $1.82 ██░░░░░░  │ $3.35  │
│                                                             │
│ By Model                                                    │
│ ─────────────────────────────────────────────────────────── │
│ claude-sonnet-4    │ $10.21 (82%)  ██████████████░░░░░░    │
│ glm-4-flash        │  $0.24 (2%)   ░░░░░░░░░░░░░░░░░░░░    │
│ claude-opus-4-5    │  $2.02 (16%)  ███░░░░░░░░░░░░░░░░░    │
│                                                             │
│ Budget: $50/day    │ Used: 25%     │ Projected: $37.41     │
└─────────────────────────────────────────────────────────────┘
```

### 4. Voice Cost Queries

Ask about costs via voice:

```
[User]: "How much have we spent today?"
[System]: "Twelve dollars and forty-seven cents across 4 sessions. 
          API server is the biggest at eight ninety-two."

[User]: "How much did the auth fix cost?"
[System]: "The auth bug fix task cost two dollars and thirty cents, 
          mostly from the orchestrator exploring the codebase."

[User]: "Which worker is most expensive?"
[System]: "Pane 1 on api-server has used twelve cents. 
          All workers combined are under fifty cents today."
```

### 5. Budget Alerts

Set spending limits with voice/CLI alerts:

```yaml
# ~/.agentwire/config.yaml
cost_tracking:
  enabled: true
  budgets:
    daily: 50.00
    per_session: 20.00
    per_task: 10.00
  alerts:
    - threshold: 80%
      action: voice  # "Heads up, you're at 80% of daily budget"
    - threshold: 100%
      action: pause  # Stop spawning new workers
```

When threshold hit:
```
[System speaks]: "Heads up, you've used forty dollars today. 
                  That's 80% of your daily budget."
```

### 6. Cost Attribution

Track costs per logical task:

```yaml
# Task cost tracking
tasks:
  fix-auth-bug:
    started: "2024-01-15T10:00:00"
    completed: "2024-01-15T10:45:00"
    total_cost: 2.30
    breakdown:
      orchestrator_exploration: 1.45
      worker_1_implementation: 0.62
      worker_2_tests: 0.23
    files_changed: 3
    cost_per_file: 0.77
```

### 7. Delegation ROI Analysis

Compare orchestrator-direct vs delegated costs:

```
Delegation Analysis (Last 7 Days)
─────────────────────────────────────────────────
                          │ Direct │ Delegated │ Savings
Simple file edits         │ $0.45  │ $0.03     │ 93%
Multi-file features       │ $2.10  │ $0.89     │ 58%
Complex debugging         │ $4.50  │ $3.20     │ 29%
─────────────────────────────────────────────────
Recommendation: Delegate simple edits, keep complex debugging direct
```

## Implementation Considerations

### Token Capture for Claude Code

Claude Code logs to `~/.claude/logs/`. Parse these for token counts:

```python
def parse_claude_logs(session_id: str) -> list[TokenEvent]:
    """Extract token usage from Claude Code logs."""
    log_dir = Path.home() / ".claude" / "logs"
    events = []
    
    for log_file in log_dir.glob("*.jsonl"):
        for line in log_file:
            entry = json.loads(line)
            if entry.get("type") == "api_response":
                events.append(TokenEvent(
                    session=session_id,
                    model=entry["model"],
                    input_tokens=entry["usage"]["input_tokens"],
                    output_tokens=entry["usage"]["output_tokens"],
                    timestamp=entry["timestamp"]
                ))
    return events
```

### Session Attribution

Link log entries to agentwire sessions:

1. **Environment variable**: Set `AGENTWIRE_SESSION` when spawning agents
2. **PID tracking**: Map tmux pane PID to log entries
3. **Timestamp correlation**: Match log timestamps to known session activity

### Storage

Lightweight local storage:

```
~/.agentwire/
  costs/
    2024-01-15.jsonl    # Daily cost events
    summary.yaml        # Aggregated totals
    tasks/
      fix-auth-bug.yaml # Per-task breakdowns
```

Or SQLite for complex queries:
```sql
CREATE TABLE cost_events (
    id INTEGER PRIMARY KEY,
    session TEXT,
    pane INTEGER,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    task_id TEXT,
    timestamp DATETIME
);
```

### Real-Time Updates

Portal polls or uses file watchers:

```python
async def cost_stream():
    """Stream cost updates to dashboard."""
    watcher = FileWatcher("~/.agentwire/costs/")
    async for event in watcher:
        cost = calculate_cost(event)
        await broadcast_to_dashboard({
            "type": "cost_update",
            "session": event.session,
            "cost": cost,
            "running_total": get_daily_total()
        })
```

## CLI Commands

```bash
# View current costs
agentwire costs                    # Today's summary
agentwire costs --session api-server
agentwire costs --task fix-auth-bug
agentwire costs --range "last 7 days"

# Set budgets
agentwire costs budget set --daily 50
agentwire costs budget set --session 20
agentwire costs budget status

# Export for analysis
agentwire costs export --format csv > costs.csv
agentwire costs report --range "last month" > report.md
```

## MCP Tools

```python
@mcp.tool()
def costs_summary(
    session: str | None = None,
    range: str = "today"  # "today", "yesterday", "this week", "last 7 days"
) -> str:
    """Get cost summary for sessions.
    
    Returns total spend, breakdown by session/model, and budget status.
    """

@mcp.tool()
def costs_task(task_id: str) -> str:
    """Get cost breakdown for a specific task.
    
    Shows orchestrator vs worker costs, cost per file changed.
    """

@mcp.tool()
def costs_alert_threshold(
    percentage: int,
    action: Literal["voice", "pause", "none"] = "voice"
) -> str:
    """Set cost alert threshold.
    
    When daily spend reaches percentage of budget, take action.
    """
```

## Voice Integration

Natural cost queries:

| Voice Command | Response |
|---------------|----------|
| "How much today?" | "$12.47 across 4 sessions" |
| "What's the most expensive session?" | "API server at $8.92" |
| "Cost of auth fix?" | "$2.30 for the fix-auth-bug task" |
| "Am I on budget?" | "You're at 25% of your $50 daily budget" |
| "Stop if we hit $40" | "Okay, I'll alert at $40 and pause spawning" |

## Potential Challenges

1. **Log Format Changes**
   - Claude Code/OpenCode might change log formats
   - Mitigation: Version-aware parsers, graceful degradation

2. **Attribution Accuracy**
   - Hard to attribute costs to specific tasks/features
   - Mitigation: Explicit task context when spawning, heuristics for matching

3. **Real-Time Accuracy**
   - Logs might be buffered, causing delays
   - Mitigation: Periodic sync, "cost as of X minutes ago" disclaimer

4. **Multi-Machine Sync**
   - Costs on remote machines need aggregation
   - Mitigation: Each machine reports to portal, portal aggregates

5. **Model Pricing Updates**
   - API pricing changes over time
   - Mitigation: Configurable pricing, periodic updates, fetch from API

## Success Metrics

- Users check cost dashboard at least weekly
- Budget alerts prevent >10% overruns
- Users can answer "how much did X cost?" within 30 seconds
- Delegation decisions informed by ROI analysis
- No more "surprise" API bills

## Future Extensions

- **Cost predictions**: "This task will likely cost $2-3 based on similar work"
- **Cost optimization suggestions**: "Consider using Haiku for these exploration tasks"
- **Team cost allocation**: Split costs across team members/projects
- **Invoice generation**: Export costs for client billing
