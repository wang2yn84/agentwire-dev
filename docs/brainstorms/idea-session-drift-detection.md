# Session Drift Detection

> Automatically detect when agents wander off-task and alert before they waste significant time/tokens.

## Problem

LLM agents go on tangents. A worker assigned to "fix the login button" might:

1. Notice the auth code "could be cleaner"
2. Start refactoring the auth module
3. Find the tests are outdated
4. Begin rewriting tests
5. Discover a dependency needs updating
6. Start researching npm packages...

Meanwhile, the login button remains broken. The orchestrator waits for an idle notification that eventually arrives with: "Refactored auth module, updated tests, researched 12 npm packages. Oh, and I didn't get to the login button."

**This happens constantly.** Scope creep, rabbit holes, and "while I'm here" syndrome burn tokens and time without delivering the original ask.

## Why Current Approaches Fail

| Approach | Problem |
|----------|---------|
| Detailed instructions | Workers still drift when they "find issues" |
| Progress streaming | Shows activity, not whether it's on-task |
| Shorter timeouts | Kills workers before legitimate complex work finishes |
| Manual monitoring | Orchestrators can't watch every worker constantly |

The system can see what workers are doing (file changes, tool calls, output). It just doesn't compare that to what they should be doing.

## Proposed Solution: Drift Detection

### 1. Task Fingerprinting

When a worker spawns, capture its task signature:

```yaml
task_fingerprint:
  session: "myproject"
  pane: 2
  started: "2024-01-15T10:30:00Z"

  # The assignment
  original_task: "Fix the login button - it doesn't respond to clicks"

  # Expected scope (inferred or explicit)
  expected_files:
    - "src/components/LoginButton.tsx"
    - "src/components/LoginButton.test.tsx"
  expected_keywords:
    - "click"
    - "handler"
    - "button"
    - "login"
  expected_scope: "UI fix"  # categories: UI fix, API work, refactor, tests, docs
```

### 2. Activity Monitoring

Track what the worker actually does:

```yaml
activity_log:
  files_touched:
    - "src/components/LoginButton.tsx"      # expected
    - "src/services/auth.ts"                 # hmm
    - "src/services/auth.test.ts"            # drift!
    - "src/utils/tokenRefresh.ts"            # definitely drift
    - "package.json"                          # rabbit hole

  keywords_mentioned:
    - "login" (5x)
    - "refactor" (12x)     # warning sign
    - "while I'm here" (2x) # red flag
    - "cleanup" (8x)        # scope creep

  tool_calls:
    - Read: 45 files        # excessive exploration
    - Edit: 12 files        # way more than a button fix
    - Bash: 8 npm commands  # dependency rabbit hole
```

### 3. Drift Scoring

Calculate a drift score (0-100) based on:

```python
def calculate_drift_score(task: TaskFingerprint, activity: ActivityLog) -> int:
    score = 0

    # File drift: touching unexpected files
    unexpected_files = activity.files - task.expected_files
    score += min(30, len(unexpected_files) * 5)

    # Keyword drift: talking about unrelated things
    off_topic_keywords = count_drift_keywords(activity.keywords, task.keywords)
    score += min(25, off_topic_keywords * 3)

    # Scope drift: refactoring when asked to fix
    if task.expected_scope == "UI fix" and activity.mentions_refactor > 3:
        score += 20

    # Rabbit hole indicators
    if "while I'm here" in activity.text or "might as well" in activity.text:
        score += 15

    # Excessive exploration
    if activity.files_read > task_complexity * 3:
        score += 10

    return min(100, score)
```

### 4. Alert Thresholds

| Drift Score | Action |
|-------------|--------|
| 0-30 | Normal operation |
| 31-50 | Log warning, continue monitoring |
| 51-70 | Voice alert to orchestrator |
| 71-100 | Voice alert + optional auto-pause |

### 5. Voice Alerts

When drift exceeds threshold:

```
agentwire_say(text="Heads up - worker 2 seems to have wandered off.
Asked to fix login button, but it's refactoring auth services.
Drift score 65. Want me to intervene?")
```

Orchestrator can:
- **Ignore**: "Let it finish"
- **Redirect**: "Send worker 2 back on task"
- **Kill**: "Kill worker 2, spawn a new one with stricter instructions"

### 6. Intervention Messages

If orchestrator chooses to redirect:

```python
# System sends to drifting worker
"""
<drift_alert>
You've drifted from your original task.

ORIGINAL: Fix the login button - it doesn't respond to clicks
CURRENT: Refactoring auth services

Please return to the original task. If you believe the current work
is necessary for the original task, explain why in your next message.
</drift_alert>
"""
```

## Implementation

### File Structure

```
.agentwire/
├── drift/
│   ├── task-2.yaml        # Task fingerprint for pane 2
│   ├── activity-2.jsonl   # Activity log for pane 2
│   └── scores.json        # Current drift scores
```

### Monitoring Hook

```python
# In agentwire event loop
@every(30_seconds)
async def check_worker_drift():
    for pane in active_worker_panes():
        task = load_task_fingerprint(pane)
        activity = load_activity_log(pane)

        score = calculate_drift_score(task, activity)

        if score > 50 and not already_alerted(pane):
            await voice_alert(
                f"Worker {pane} drifting. Score {score}. "
                f"Task was: {task.summary}. "
                f"Now touching: {activity.recent_files}"
            )
            mark_alerted(pane)
```

### Activity Capture

Hook into existing output monitoring:

```python
@on_pane_output(pane)
def update_activity(pane: int, output: str):
    activity = load_activity_log(pane)

    # Extract files mentioned
    files = extract_file_paths(output)
    activity.files_touched.update(files)

    # Extract keywords
    keywords = extract_keywords(output)
    activity.keywords.update(keywords)

    # Check for drift indicators
    if contains_drift_phrases(output):
        activity.drift_indicators.append(extract_context(output))

    save_activity_log(pane, activity)
```

### CLI Commands

```bash
# Check current drift status
agentwire drift status
#   Pane 1: score 15 (on track)
#   Pane 2: score 62 (DRIFTING - auth refactor)
#   Pane 3: score 8 (on track)

# View drift details
agentwire drift show --pane 2
#   Original task: Fix login button
#   Current activity: Refactoring auth module
#   Files touched: 12 (expected: 2)
#   Drift indicators: "while I'm here", "might as well clean up"

# Manual intervention
agentwire drift redirect --pane 2
#   → Sends drift_alert to worker 2

# Configure thresholds
agentwire drift config --alert-threshold 60 --auto-pause-threshold 85
```

### MCP Tools

```python
@mcp.tool()
def drift_status(pane: int | None = None) -> str:
    """Get drift status for workers.

    Returns drift scores and summaries for all workers or specific pane.
    """

@mcp.tool()
def drift_redirect(pane: int, message: str | None = None) -> str:
    """Send a redirect message to a drifting worker.

    Optionally include a custom message about what to focus on.
    """

@mcp.tool()
def drift_config(alert_threshold: int = 50, auto_pause: int = 85) -> str:
    """Configure drift detection thresholds."""
```

## Configuration

```yaml
# In ~/.agentwire/config.yaml
drift_detection:
  enabled: true

  thresholds:
    warning: 30      # Log warning
    alert: 50        # Voice alert to orchestrator
    auto_pause: 85   # Pause worker and alert (optional)

  check_interval: 30s

  # Phrases that indicate drift
  drift_phrases:
    - "while I'm here"
    - "might as well"
    - "let me also"
    - "should probably"
    - "noticed that"
    - "could be improved"
    - "quick cleanup"

  # How to alert
  alert_mode: voice    # voice, text, both

  # Per-task overrides
  strict_mode: false   # If true, alert at lower thresholds
```

## Example Scenario

```
[Orchestrator spawns worker 2]
"Fix the login button - it doesn't respond to clicks"

[Worker 2 starts]
t+0s:  Reading LoginButton.tsx
t+30s: Reading auth.ts (drift score: 10)
t+60s: Editing auth.ts (drift score: 25)
t+90s: "I noticed the auth module could be cleaner..." (drift score: 45)
t+120s: Creating auth.test.ts (drift score: 62)

[System voice alert]
"Worker 2 is drifting. Asked to fix login button, now creating auth tests.
Drift score 62. Intervene?"

[Orchestrator]: "Redirect it"

[System sends to worker 2]
<drift_alert>
You've drifted from your original task.
ORIGINAL: Fix the login button - it doesn't respond to clicks
CURRENT: Creating auth tests
Please return to the original task.
</drift_alert>

[Worker 2 receives alert]
"You're right, let me focus on the login button..."

[Worker completes actual task]
```

## Potential Challenges

1. **False positives**: Sometimes exploring related code IS necessary.
   - Solution: Allow workers to explain with "This is necessary because..." override
   - Solution: Learn from overrides to tune thresholds

2. **Task ambiguity**: Vague tasks like "improve the app" have no clear scope.
   - Solution: Skip drift detection for tasks without clear scope
   - Solution: Prompt orchestrator to provide expected_files/scope

3. **Legitimate discoveries**: Worker finds a critical bug while fixing something else.
   - Solution: Allow "pause original task, report finding" workflow
   - Solution: Different alert for "found something important" vs "wandered off"

4. **Monitoring overhead**: Constant activity analysis.
   - Solution: Lightweight keyword/file tracking, not full semantic analysis
   - Solution: Only check every 30s, not on every output line

5. **Agent compliance**: Workers might ignore drift alerts.
   - Solution: Auto-pause at high drift scores
   - Solution: Include drift alert handling in worker role instructions

## Success Criteria

1. Drifting workers are caught before completing off-task work (>80% of cases)
2. Orchestrators report fewer "worker did the wrong thing" incidents
3. Average task completion time decreases (less wasted exploration)
4. Token spend per task decreases (workers stay focused)
5. False positive rate stays below 20% (workers aren't constantly interrupted)

## Non-Goals

- **Preventing all exploration**: Some breadth is healthy
- **Micromanaging workers**: Alert, don't constantly redirect
- **Penalizing workers**: This is about helping, not blame
- **Replacing good task descriptions**: Better prompts > drift detection
