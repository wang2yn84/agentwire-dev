# Session Momentum Scoring

> Track real-time productive velocity per session and intervene when progress stalls.

## Problem

Sessions silently struggle. A worker might spend 15 minutes retrying a broken approach, an orchestrator might spawn worker after worker without any succeeding, or a session might be technically "active" but making zero forward progress. Right now, the only way to know is to manually check output or wait for a failure alert.

```
Session: auth-service
Status: "active" ← technically true
Reality:
  Turn 1: Read file... ok
  Turn 2: Edit file... syntax error
  Turn 3: Fix syntax... different error
  Turn 4: Undo edit... read file again
  Turn 5: Try different approach... same error
  Turn 6: Read docs... (still stuck)
  ...
  20 minutes later, orchestrator checks: "wait, why isn't this done?"
```

The session is alive but thrashing. Heartbeat monitors say it's fine. Drift detection doesn't apply (it's working on the right task). The problem is invisible until someone looks.

## Why This Matters

1. **Silent token burn** - Stuck sessions waste money without anyone noticing
2. **Cascading delays** - Downstream tasks wait on a session that's going nowhere
3. **Orchestrator blindness** - No signal distinguishing "making progress" from "spinning wheels"
4. **Late intervention** - By the time you notice, 10-20 minutes and hundreds of tokens are gone
5. **Human attention cost** - Manually monitoring session output doesn't scale past 3-4 sessions

## Proposed Solution: Momentum Scoring

### 1. Define Momentum Signals

Track lightweight signals from session output that indicate forward progress vs. stalling:

| Signal | Positive (progress) | Negative (stalling) |
|--------|---------------------|---------------------|
| File operations | New files created, successful edits | Same file edited 3+ times, reverted changes |
| Commands | Tests passing, builds succeeding | Same command retried, repeated failures |
| Output patterns | "Done", "Created", "Updated" | "Error", "Failed", "Cannot", "Retrying" |
| Worker lifecycle | Workers completing with DONE | Workers exiting with ERROR/BLOCKED |
| Turn efficiency | Work completed per turn | Turns spent reading without writing |
| Time gaps | Steady activity cadence | Long pauses followed by repeated attempts |

### 2. Calculate Momentum Score

A rolling score from 0-100 computed over a sliding window:

```python
@dataclass
class MomentumSnapshot:
    score: int           # 0-100
    trend: str           # "rising", "stable", "falling", "stalled"
    window_minutes: int  # How far back we're looking
    signals: dict        # What contributed to the score

def calculate_momentum(session: str, window_minutes: int = 5) -> MomentumSnapshot:
    """Calculate momentum from recent session activity."""

    output = capture_output(session, lines=100)

    positive = 0
    negative = 0

    # Count positive signals
    positive += count_successful_edits(output)
    positive += count_passing_tests(output)
    positive += count_new_files(output)
    positive += count_completion_markers(output)

    # Count negative signals
    negative += count_errors(output) * 2        # Errors weigh more
    negative += count_retries(output) * 1.5     # Retries are a strong signal
    negative += count_reverted_changes(output)
    negative += count_repeated_reads(output)     # Reading same file 3+ times

    # Factor in worker outcomes (for orchestrator sessions)
    worker_stats = get_recent_worker_stats(session, window_minutes)
    positive += worker_stats.completed * 3
    negative += worker_stats.failed * 3
    negative += worker_stats.blocked * 2

    # Calculate score
    total = positive + negative
    if total == 0:
        return MomentumSnapshot(score=50, trend="stable")  # No activity = neutral

    raw_score = (positive / total) * 100

    # Compare to previous window for trend
    prev = get_previous_momentum(session)
    trend = classify_trend(prev.score, raw_score)

    return MomentumSnapshot(
        score=int(raw_score),
        trend=trend,
        window_minutes=window_minutes,
        signals={"positive": positive, "negative": negative}
    )
```

### 3. Threshold-Based Interventions

When momentum drops, the system takes graduated action:

```yaml
momentum:
  thresholds:
    # Score 70-100: Healthy, no action
    healthy: 70

    # Score 40-69: Slowing down
    # → Log it, include in next voice briefing
    caution: 40

    # Score 20-39: Stalling
    # → Voice alert to orchestrator/user
    warning: 20

    # Score 0-19: Stuck
    # → Auto-gather debug context, escalate
    critical: 0
```

**Graduated responses:**

| Level | Score | Action |
|-------|-------|--------|
| Healthy | 70-100 | None. Session is productive. |
| Caution | 40-69 | Tag in dashboard. Include in next periodic briefing. |
| Warning | 20-39 | Voice alert: "Auth-service session has been struggling for 5 minutes. 3 failed edits and 2 test failures." |
| Critical | 0-19 | Auto-capture context (last 50 lines, error summary, files touched), voice alert with context, suggest intervention options. |

### 4. Intervention Actions

When momentum hits warning/critical, the system provides actionable information:

```
[Voice alert]
"The auth-service worker has stalled. It's been retrying the same
JWT validation test for 8 minutes with 4 failures. The error is
'secret key undefined'. Want me to kill it and spawn a fresh worker
with the env config included?"
```

For orchestrator sessions managing workers:

```
[Voice alert to orchestrator]
"Worker on pane 2 has zero momentum. It's edited src/auth.ts
five times and reverted each change. Recommend killing it and
retrying with more specific instructions. Errors suggest it
doesn't know about the custom auth middleware."
```

### 5. Momentum Dashboard Integration

Portal shows momentum as a visual indicator per session:

```
Sessions:
  auth-service    ████████░░  78  ↑ rising
  api-gateway     ██████░░░░  55  → stable
  frontend        ██░░░░░░░░  18  ↓ stalled (3m)
                                    └─ "TypeError: Cannot read property..."
```

Color coding: green (70+), yellow (40-69), red (20-39), flashing red (0-19).

## Implementation

### Output Analysis (Lightweight)

Momentum scoring uses pattern matching on captured output, not LLM analysis:

```python
# Positive patterns
PROGRESS_PATTERNS = [
    r"(?i)created?\s+\S+",          # "Created src/auth.ts"
    r"(?i)updated?\s+\S+",          # "Updated package.json"
    r"(?i)tests?\s+pass",           # "Tests passed"
    r"(?i)build\s+succeed",         # "Build succeeded"
    r"✓|✅|PASS|DONE",              # Common success markers
]

# Negative patterns
STALL_PATTERNS = [
    r"(?i)error:|Error:|ERROR",     # Error messages
    r"(?i)failed|failure|FAIL",     # Failure indicators
    r"(?i)retry|retrying|again",    # Retry language
    r"(?i)cannot|can't|unable",     # Inability markers
    r"(?i)revert|undo|rollback",    # Reversals
]

# Thrashing detection: same file appearing in edit commands 3+ times
def detect_file_thrashing(output: str) -> int:
    file_edits = extract_file_operations(output)
    return sum(1 for f, count in file_edits.items() if count >= 3)
```

### Integration Points

```python
# In the idle detection loop (already runs periodically)
async def check_session_health(session: str):
    momentum = calculate_momentum(session)
    store_momentum(session, momentum)

    if momentum.score < thresholds["warning"] and momentum.trend == "stalled":
        context = gather_stall_context(session)

        # Alert the session's parent (or the user)
        parent = get_parent_session(session)
        if parent:
            alert_text = format_momentum_alert(session, momentum, context)
            agentwire_alert(text=alert_text, to=parent)

        # Voice alert if critical
        if momentum.score < thresholds["critical"]:
            voice_text = format_voice_alert(session, momentum, context)
            agentwire_say(text=voice_text, session=parent or session)
```

### Storage

Momentum history stored in memory with periodic flush:

```python
# In-memory ring buffer per session (last 30 minutes)
momentum_history: dict[str, deque[MomentumSnapshot]] = {}

# Flush to .agentwire/momentum/{session}.jsonl every 5 minutes
# for dashboard and post-mortem analysis
```

### CLI Commands

```bash
# Check momentum for a session
agentwire momentum auth-service
# → Score: 78/100 ↑ rising (healthy)
# → Last 5 min: 4 successful edits, 1 test pass, 0 errors

# Check all sessions
agentwire momentum --all
# → auth-service   78 ↑  frontend  18 ↓ (stalled 3m)

# Momentum history
agentwire momentum auth-service --history
# → 10:00  85  10:05  72  10:10  45  10:15  18 ← stall started here

# Configure thresholds
agentwire momentum config --warning 25 --critical 10
```

### MCP Tool

```python
@mcp.tool()
def momentum_check(session: str | None = None) -> str:
    """Check momentum score for a session or all sessions.

    Returns current score, trend, and any alerts.
    Useful for orchestrators deciding whether to intervene.
    """
```

## Configuration

```yaml
# In ~/.agentwire/config.yaml
momentum:
  enabled: true

  # Scoring window
  window_minutes: 5

  # How often to recalculate
  interval_seconds: 30

  # Intervention thresholds
  thresholds:
    healthy: 70
    caution: 40
    warning: 20
    critical: 0

  # Minimum stall duration before alerting (prevent false positives)
  min_stall_minutes: 3

  # Alert cooldown (don't spam)
  alert_cooldown_minutes: 5

  # What to do at each level
  actions:
    caution: log          # Just log it
    warning: alert        # Voice/text alert
    critical: alert+context  # Alert with gathered debug context
```

## Potential Challenges

1. **False positives during exploration.** A session reading many files before starting work looks like "no progress" but is actually healthy orientation.
   - Mitigation: Distinguish "exploration phase" (first 2-3 minutes) from "stuck phase." Weight early reads as neutral, not negative. Add a warmup grace period.

2. **Pattern matching accuracy.** Output formats vary between agents and tools. An "error" in a log message isn't the same as an actual failure.
   - Mitigation: Use context-aware patterns (error at start of line vs. in quoted text). Weight multiple signals together so a single false match doesn't tank the score.

3. **Orchestrator sessions look different from workers.** An orchestrator session that's waiting for workers has low output volume but isn't stalled.
   - Mitigation: Detect orchestrator role and factor in worker activity as the session's momentum. Orchestrator waiting with healthy workers = healthy momentum.

4. **Alert fatigue.** Too many momentum warnings become noise.
   - Mitigation: Cooldown periods, minimum stall duration, and graduated responses. Only voice-alert on sustained stalls (3+ minutes), not momentary dips.

5. **Overhead.** Constantly analyzing output adds CPU/memory cost.
   - Mitigation: Pattern matching is cheap (regex on last 100 lines). Run on the same interval as idle detection (already exists). No LLM calls in the scoring loop.
