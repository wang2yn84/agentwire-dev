# Session Thermal Throttling

**Graduated automatic intervention when sessions overheat from errors, retries, and context exhaustion.**

## Problem

Agents get stuck. A worker hits a flaky test, retries the same approach 8 times, burns through its context window, and produces nothing useful. An orchestrator spawns a worker with bad instructions, the worker flails, and nobody notices until the API bill arrives. The current system has monitoring (watchdog, heartbeat) and gauges (context window), but nothing that *acts* on degradation in real time.

The failure mode is always the same: the agent keeps going when it should stop, think, or ask for help. Humans do this too - but humans get tired and take a break. Agents don't.

## Proposed Solution

Borrow CPU thermal throttling: sessions have a **temperature** that rises with dysfunction and triggers graduated responses.

### Temperature Signals

Each signal contributes heat. Combined signals compound:

| Signal | Heat Contribution | Detection |
|--------|-------------------|-----------|
| Repeated identical tool calls | +15 per duplicate | Hash recent tool calls, detect runs of 3+ |
| Error outputs (exit code != 0) | +10 per error | Monitor session output for error patterns |
| Context window usage > 70% | +5 continuous | Track token count / model limit |
| Context window usage > 90% | +20 continuous | Critical zone |
| Time without meaningful file change | +3 per minute | Watch `git diff --stat` or file mtimes |
| Same file edited > 4 times | +10 per re-edit | Track edit targets |
| Worker re-spawned for same task | +15 per respawn | Track task-to-worker mapping |

Temperature decays at -5/minute during productive work (successful commands, new file changes, forward progress).

### Throttle Levels

| Temp | Level | Action |
|------|-------|--------|
| 0-30 | Cool | Normal operation |
| 30-50 | Warm | Log warning, add 2s delay between actions |
| 50-70 | Hot | Alert orchestrator, force context summary, suggest alternative approach |
| 70-85 | Critical | Pause session, notify parent/human, require explicit "continue" |
| 85+ | Meltdown | Kill session, preserve state to disk, send voice alert with diagnosis |

### The Diagnosis Report

At Hot+ levels, the system generates a diagnosis:

```markdown
## Thermal Report: worker-pane-2
Temperature: 72 (CRITICAL)

### Heat Sources
- 4x identical `npm test` calls (exit 1) → +40
- Context at 78% capacity → +5 continuous
- No file changes in 6 minutes → +18
- Same test file edited 5 times → +10

### Pattern Detected
Retry loop: running same test without changing approach

### Suggested Actions
1. Read the actual error message (last 3 runs show same TypeError)
2. Check if dependency is missing (package.json unchanged)
3. Ask orchestrator for guidance
```

This report goes to the orchestrator (or human) so they can make an informed decision.

### CLI Integration

```bash
# Check session temperature
agentwire thermal -s myproject
# → myproject: 23°C (cool) | pane-1: 67°C (HOT) | pane-2: 12°C (cool)

# View thermal history
agentwire thermal -s myproject --history
# → Timeline showing temperature over last 30 minutes

# Override throttle (trust the agent)
agentwire thermal -s myproject --override hot
# → Won't pause until critical

# Set custom thresholds per project
# In .agentwire.yml:
thermal:
  hot_threshold: 60        # More aggressive
  critical_threshold: 75
  meltdown_threshold: 90
  decay_rate: 3            # Slower cooldown (cautious)
```

### Portal Integration

The session card in the portal shows a temperature indicator:

- Color-coded thermometer icon (blue → green → yellow → orange → red)
- Clicking shows the live thermal breakdown
- Historical chart shows thermal patterns over the session lifetime
- Push notification to connected devices at Critical+

## Implementation Considerations

### Where It Runs

The thermal monitor runs in the **portal's activity loop** - the same loop that already detects idle sessions. It piggybacks on the existing output polling:

1. `capture-pane` output is already being read for idle detection
2. Add pattern matching for error signals (exit codes, stack traces, "Error:" prefixes)
3. Track tool call hashes from recent output
4. Compute temperature delta and apply throttle level

### State Storage

Temperature state lives in memory (portal process), with periodic snapshots to `/tmp/agentwire-thermal-{session}.json` for crash recovery. No database needed.

### Intervention Mechanism

- **Delays**: Insert `sleep` before sending next command to pane
- **Pause**: Send `Ctrl+C` to interrupt current operation, then hold further sends
- **Kill**: Use existing `agentwire kill --pane N` with state preservation first
- **Alerts**: Use existing `agentwire alert` and `agentwire say` infrastructure

### Integration with Existing Systems

- **Watchdog mode**: Thermal throttling subsumes basic watchdog. Watchdog becomes "is anything happening?" while thermal asks "is what's happening productive?"
- **Context window gauge**: Feeds directly into temperature as a signal
- **Worker heartbeat**: Heartbeat = alive, thermal = effective. Both needed.
- **Task time budgets**: Time budget is a hard cap, thermal is a soft health signal. A session can be within its time budget but thermally critical.

## Potential Challenges

1. **False positives**: Legitimate long-running operations (large test suites, big builds) look like "no progress." Need allowlists for known slow commands, or let the orchestrator mark a session as "expected slow."

2. **Signal accuracy**: Detecting "identical tool calls" from raw terminal output is noisy. Need robust hashing that ignores timestamps and dynamic values while catching actual repetition.

3. **Orchestrator trust**: An orchestrator receiving a thermal alert about its worker needs enough context to decide. The diagnosis report must be concise and actionable, not another wall of text.

4. **Cascading throttles**: If a worker overheats and the orchestrator spawns a replacement that also overheats, the orchestrator itself heats up from re-spawning. Need to handle this cascade without the whole hierarchy freezing.

5. **Tuning per agent type**: Different Claude Code configurations have different output patterns. Error detection regexes and "productive work" heuristics may need agent-specific calibration.
