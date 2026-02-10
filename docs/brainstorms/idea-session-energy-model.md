# Session Energy Model

> Track and maintain session health as a unified "energy" score that decays over time, triggering automatic renewal before sessions degrade.

## Problem

Long-running sessions silently degrade. There's no single indicator of session health - instead, multiple independent problems accumulate until the session becomes noticeably broken:

```
Hour 0: Fresh session, sharp responses, fast execution
Hour 1: Context window 40% full, still performing well
Hour 2: Context filling, agent starts forgetting earlier decisions
Hour 3: 3 lint warnings accumulated, 1 skipped test, responses getting vague
Hour 4: Context compressed, lost some nuance from early conversation
Hour 5: Agent contradicts earlier decision, takes wrong approach
Hour 6: User notices something is "off", manually restarts session
```

Each of these signals exists independently, but nobody is watching the combined picture. The user only notices when things have already gone wrong - wasted tokens, bad code committed, frustrating interactions.

**This is worse for orchestrators.** An orchestrator session that has degraded makes bad delegation decisions - spawning wrong workers, giving vague task descriptions, missing conflicts. The downstream cost multiplies.

## Why Individual Signals Aren't Enough

| Signal | Exists Today? | Why It's Not Sufficient Alone |
|--------|---------------|-------------------------------|
| Context window usage | Partially (model tracks internally) | High context doesn't always mean degraded - could be rich useful context |
| Time since start | Trivial to track | Long sessions aren't inherently bad if well-managed |
| Accumulated warnings/errors | Not tracked | A few warnings are fine; the rate of accumulation matters |
| Agent coherence | Not measured | Hard to detect from inside the session |
| Technical debt | Not tracked | Lint warnings and skipped tests pile up invisibly |
| Decision consistency | Not measured | Agent may contradict earlier choices as context compresses |

No single metric tells you "this session needs maintenance." It's the combination and trajectory that matters.

## Proposed Solution: Unified Energy Score

Model each session's health as an "energy" value (0-100) computed from weighted sub-signals. Energy naturally decays over time and drops sharply on negative events. Maintenance actions restore energy.

### Energy Components

```
Session Energy = weighted_sum(
    context_headroom,     # How much context window remains
    freshness,            # Time since session start / last restart
    coherence,            # Are responses consistent and on-topic?
    debt_level,           # Accumulated unfixed issues
    completion_rate,      # Recent task success rate
)
```

Each component is 0-100, combined with configurable weights:

```yaml
energy:
  weights:
    context_headroom: 0.30   # Most important - can't work without context
    freshness: 0.20          # Decay over time
    coherence: 0.20          # Quality of output
    debt_level: 0.15         # Accumulated cruft
    completion_rate: 0.15    # Success momentum
```

### Component Calculations

**Context Headroom (0-100)**
```python
def context_headroom(session: Session) -> float:
    # Estimate from conversation length and compression events
    if session.compression_count == 0:
        # Pre-compression: linear estimate
        estimated_usage = session.message_count * AVG_TOKENS_PER_TURN
        return max(0, 100 * (1 - estimated_usage / MODEL_CONTEXT_LIMIT))
    else:
        # Post-compression: each compression loses nuance
        base = 60  # After first compression, start at 60
        penalty = 15 * (session.compression_count - 1)  # -15 per additional
        return max(0, base - penalty)
```

**Freshness (0-100)**
```python
def freshness(session: Session) -> float:
    hours = session.age_hours()
    # Exponential decay: 100 at 0h, ~60 at 4h, ~35 at 8h, ~13 at 12h
    return 100 * math.exp(-0.15 * hours)
```

**Coherence (0-100)**

Hardest to measure. Use proxy signals:

```python
def coherence(session: Session) -> float:
    score = 100

    # Repeated tool failures (agent confused about state)
    recent_failures = session.tool_failures_last_30min()
    score -= min(40, recent_failures * 10)

    # Worker re-spawns for same task (sign of bad instructions)
    retries = session.task_retry_count()
    score -= min(30, retries * 15)

    # Time between user clarifications (more = worse coherence)
    clarifications = session.user_clarifications_last_hour()
    score -= min(30, clarifications * 10)

    return max(0, score)
```

**Debt Level (0-100, inverted: 100 = clean)**
```python
def debt_level(session: Session) -> float:
    score = 100
    cwd = session.working_directory()

    # Uncommitted changes accumulating
    uncommitted_files = git_status(cwd).changed_count
    score -= min(20, uncommitted_files * 3)

    # Lint/type warnings introduced during session
    new_warnings = session.warnings_introduced()
    score -= min(30, new_warnings * 5)

    # Skipped or failing tests
    failing_tests = session.failing_test_count()
    score -= min(30, failing_tests * 10)

    # Stale worker summaries not cleaned up
    orphan_files = count_orphan_summaries(cwd)
    score -= min(20, orphan_files * 5)

    return max(0, score)
```

**Completion Rate (0-100)**
```python
def completion_rate(session: Session) -> float:
    recent = session.tasks_last_hour()
    if not recent:
        return 70  # Neutral if no recent tasks

    succeeded = sum(1 for t in recent if t.status == "DONE")
    return 100 * (succeeded / len(recent))
```

### Energy Thresholds and Actions

| Energy | State | Visual | Action |
|--------|-------|--------|--------|
| 80-100 | Healthy | Green | None |
| 60-79 | Aging | Yellow | Suggest maintenance |
| 40-59 | Degraded | Orange | Warn orchestrator, suggest fork |
| 20-39 | Critical | Red | Auto-fork or alert human |
| 0-19 | Depleted | Red pulse | Force renewal |

### Maintenance Actions

When energy drops, the system suggests or triggers renewal:

**Suggest (60-79 energy):**
```
[Voice]: "Session energy at 65. Context is getting heavy and there are
2 uncommitted lint warnings. Want me to clean up and commit a checkpoint?"
```

**Warn (40-59 energy):**
```
[Voice]: "Session energy dropping - at 45. I've been running for 6 hours
and my context has been compressed twice. Recommend forking to a fresh
session. I'll write a handoff summary."
```

**Auto-renew (20-39 energy):**
```
[Voice]: "Session energy critical at 25. Forking to fresh session now.
Writing handoff packet."

[System automatically]:
1. Generates handoff summary (current task, progress, decisions made)
2. Commits any uncommitted clean work
3. Forks to new session via `agentwire fork`
4. New session receives handoff as initial context
```

### Portal Dashboard

The portal shows energy as a visual indicator per session:

```
┌─────────────────────────────────────────────────────┐
│ Sessions                                             │
├─────────────────────────────────────────────────────┤
│ agentwire-dev   ██████████████████░░ 88  [healthy]  │
│ website         ████████████░░░░░░░░ 62  [aging]    │
│ api-server      ██████░░░░░░░░░░░░░░ 34  [critical] │
│ docs            ████████████████████ 95  [healthy]  │
└─────────────────────────────────────────────────────┘
```

Hovering/clicking shows component breakdown:

```
api-server: 34/100
├── Context headroom:  30/100  (compressed 3x)
├── Freshness:         20/100  (running 11 hours)
├── Coherence:         45/100  (2 retries, 1 clarification)
├── Debt:              50/100  (3 uncommitted files, 1 failing test)
└── Completion rate:   40/100  (2/5 recent tasks succeeded)
```

## Implementation

### Energy Calculator Service

A background process that periodically computes energy:

```python
class EnergyCalculator:
    def __init__(self, config: EnergyConfig):
        self.weights = config.weights
        self.interval_seconds = 60  # Recalculate every minute

    async def calculate(self, session: Session) -> EnergyReport:
        components = {
            "context_headroom": context_headroom(session),
            "freshness": freshness(session),
            "coherence": coherence(session),
            "debt_level": debt_level(session),
            "completion_rate": completion_rate(session),
        }

        total = sum(
            components[k] * self.weights[k]
            for k in components
        )

        return EnergyReport(
            session=session.name,
            total=round(total),
            components=components,
            threshold=self.classify(total),
            timestamp=now(),
        )

    def classify(self, energy: float) -> str:
        if energy >= 80: return "healthy"
        if energy >= 60: return "aging"
        if energy >= 40: return "degraded"
        if energy >= 20: return "critical"
        return "depleted"
```

### Data Collection

Most signals come from existing infrastructure:

| Signal | Source | Already Available? |
|--------|--------|--------------------|
| Session age | tmux session creation time | Yes |
| Message count | Output capture line count | Approximate |
| Compression events | Agent-specific (Claude uses `/compact`) | Needs hook |
| Tool failures | Output parsing for error patterns | Needs implementation |
| Git status | `git status --porcelain` | Yes |
| Test results | Output parsing or explicit reporting | Partial |
| Worker retries | Summary file status field | Yes |

New collection needed:
- **Compression detection**: Watch for context compression markers in output
- **Coherence proxies**: Parse output for retry patterns, clarification requests
- **Warning tracking**: Run linter periodically or parse worker output

### Storage

```
~/.agentwire/energy/
├── current.json          # Latest energy for all sessions
├── history/
│   ├── agentwire-dev.jsonl   # Historical energy readings
│   └── api-server.jsonl
```

History enables trend visualization in the portal (energy over time chart).

### CLI Commands

```bash
# Check energy for all sessions
agentwire energy
# Output:
# agentwire-dev   88  healthy
# website         62  aging
# api-server      34  critical

# Detailed breakdown for one session
agentwire energy -s api-server
# Output:
# api-server: 34/100 (critical)
#   context_headroom:  30  (compressed 3x)
#   freshness:         20  (11h uptime)
#   coherence:         45  (2 retries)
#   debt_level:        50  (3 dirty files)
#   completion_rate:   40  (2/5 succeeded)
#
# Recommendation: Fork to fresh session

# Manually trigger maintenance
agentwire energy renew -s api-server

# View energy history
agentwire energy history -s api-server --since 24h
```

### MCP Tools

```python
@mcp.tool()
def session_energy(session: str | None = None) -> str:
    """Get energy report for a session or all sessions.

    Returns energy score, component breakdown, and recommendations.
    Useful for orchestrators deciding whether to continue or renew.
    """

@mcp.tool()
def session_renew(session: str | None = None, reason: str | None = None) -> str:
    """Trigger session renewal (fork to fresh session with handoff).

    Generates handoff summary, commits clean work, and forks.
    """
```

### Integration with Existing Features

**Session handshake**: Include energy in the handshake brief.
```
"Welcome back. Session energy is at 55 - I've been running a while.
Quick brief: ..."
```

**Worker spawning**: Orchestrator checks own energy before spawning workers. If degraded, renew first.
```python
energy = agentwire_session_energy()
if energy.total < 50:
    agentwire_say("My context is getting stale. Let me fork to a fresh session before spawning workers.")
    agentwire_session_renew()
```

**Task scheduling**: `agentwire ensure` checks session energy before running tasks. If too low, recreate the session first.

## Configuration

```yaml
# ~/.agentwire/config.yaml
energy:
  enabled: true
  check_interval_seconds: 60

  weights:
    context_headroom: 0.30
    freshness: 0.20
    coherence: 0.20
    debt_level: 0.15
    completion_rate: 0.15

  thresholds:
    suggest_maintenance: 65
    warn: 45
    auto_renew: 25

  auto_renew:
    enabled: false          # Off by default, opt-in
    commit_clean_work: true
    write_handoff: true
    method: fork            # fork | recreate

  history:
    retention_days: 7
```

Per-project overrides in `.agentwire.yml`:

```yaml
energy:
  # Research sessions can run longer before aging
  weights:
    freshness: 0.10
    context_headroom: 0.35

  thresholds:
    auto_renew: 15  # More tolerant for long research sessions
```

## Potential Challenges

1. **Proxy accuracy** - Coherence and context headroom are estimated from indirect signals, not ground truth. The energy score may not always reflect actual session quality. Mitigation: weight the reliable signals (freshness, debt) higher, treat coherence as a loose signal. Calibrate weights based on observed correlation between energy and actual outcome quality.

2. **Agent-specific differences** - Claude Code and OpenCode have different context windows, compression behaviors, and failure modes. The energy model needs per-agent calibration. Mitigation: agent-type parameter in the calculator that adjusts base assumptions (context size, compression impact).

3. **Premature renewal** - Auto-renewing a session that was actually fine wastes the context already built up. Mitigation: default auto-renew to off. Suggestions are cheap (voice prompt); forced renewal should require very low energy with multiple confirming signals.

4. **Handoff fidelity** - When forking to a fresh session, the handoff summary may lose important nuance from the original conversation. Mitigation: structure handoff around concrete artifacts (files changed, decisions made, task list) rather than conversation summary. The new session reads actual files rather than relying on the handoff narrative.

5. **Collection overhead** - Running git status, linters, and output parsers every 60 seconds could add load. Mitigation: stagger checks (git status every 60s, linter every 5min, coherence on-demand). Cache results. Skip checks for idle sessions.

6. **Gaming the score** - An agent could artificially inflate its energy by cleaning debt and succeeding at trivial tasks. Mitigation: this isn't adversarial - the agent benefits from accurate energy reporting. If anything, agents should be incentivized to report honestly so they get renewed when needed.
