# Periodic Voice Briefings: Your Agent Fleet Radio

> The system synthesizes what all active sessions are doing and delivers a brief spoken summary at regular intervals — like a project manager who walks over and tells you what's happening.

## Problem

When you have 3-5 sessions running across projects, you lose ambient awareness. Right now you either:

1. **Manually check each session** — Open the portal, click through sessions, read outputs. Tedious, breaks flow, requires screen time.
2. **Wait for alerts** — Only hear about errors or completions. No sense of progress. Long silences feel anxious: "Is it working? Is it stuck?"
3. **Ask explicitly** — Voice-command each session: "What's your status?" Multiplied by N sessions, this gets old fast.

The result: you're either over-monitoring (checking constantly) or under-monitoring (surprised by failures 20 minutes in). Neither is the calm, ambient computing experience AgentWire enables.

## Proposed Solution

**Scheduled voice briefings** — every N minutes, the system captures the state of all active sessions, synthesizes a concise summary, and speaks it aloud. Think of it like a radio DJ doing traffic updates: brief, relevant, then back to your regularly scheduled program.

### What a Briefing Sounds Like

```
"Quick update. Three sessions active.

Auth-service is running tests after implementing the JWT middleware —
looking healthy so far.

The docs worker on agentwire-website just finished and is idle.

CI-fixer has been stuck on the same TypeScript error for about
eight minutes — might need your attention.

That's it. Next update in ten minutes."
```

Key traits:
- **30 seconds or less** — respect attention budget
- **Prioritized** — problems first, then progress, then idle/done
- **Conversational** — not a data dump, a spoken summary a human would give
- **Skippable** — say "skip" mid-briefing to stop it

### How It Works

```
┌─────────────────────────────────────────┐
│           Briefing Scheduler            │
│  (configurable interval, e.g. 10 min)  │
└────────────────┬────────────────────────┘
                 │ tick
                 ▼
┌─────────────────────────────────────────┐
│          State Collector                │
│  For each active session:              │
│  - Capture last 20 lines of output     │
│  - Check idle/active status            │
│  - Note time since last status change  │
│  - Check for error patterns            │
└────────────────┬────────────────────────┘
                 │ raw state
                 ▼
┌─────────────────────────────────────────┐
│        Summary Synthesizer              │
│  LLM call (haiku-tier, cheap):         │
│  "Given these session states,           │
│   generate a 30-second spoken           │
│   briefing prioritizing problems."      │
└────────────────┬────────────────────────┘
                 │ briefing text
                 ▼
┌─────────────────────────────────────────┐
│           TTS + Delivery                │
│  agentwire_say(text=briefing)           │
│  Routes to active device via portal     │
└─────────────────────────────────────────┘
```

### Synthesizer Prompt

The LLM synthesizer gets a structured snapshot and produces natural speech:

```
You are a project status reporter. Given the state of active agent sessions,
produce a spoken briefing under 30 seconds. Rules:
- Lead with anything that needs human attention (errors, stuck sessions)
- Then progress updates (what changed since last briefing)
- Skip sessions with no meaningful change since last update
- Use casual, spoken English — this will be read aloud via TTS
- End with count of next update interval
- If nothing meaningful changed, say so in one sentence and stop
```

### Diff-Aware Updates

Briefings track what was reported last time and only mention changes:

```python
class BriefingState:
    last_reported: dict[str, SessionSnapshot]  # session_name → last state

    def compute_delta(self, current: dict[str, SessionSnapshot]) -> list[Delta]:
        """Only surface what changed since last briefing."""
        deltas = []
        for name, snap in current.items():
            prev = self.last_reported.get(name)
            if not prev:
                deltas.append(Delta(name, type="new", snap=snap))
            elif snap.status != prev.status:
                deltas.append(Delta(name, type="status_change", snap=snap, prev=prev))
            elif snap.has_errors and not prev.has_errors:
                deltas.append(Delta(name, type="new_error", snap=snap))
            elif snap.idle_duration > 300 and prev.idle_duration < 300:
                deltas.append(Delta(name, type="went_idle", snap=snap))
        # Sessions that disappeared
        for name in self.last_reported:
            if name not in current:
                deltas.append(Delta(name, type="ended"))
        return deltas
```

If no deltas exist: "All quiet. No changes since the last update. Next check in ten minutes."

### Smart Scheduling

Not just a dumb timer — adapt to what's happening:

| Condition | Interval Adjustment |
|-----------|-------------------|
| All sessions idle | Pause briefings until activity resumes |
| Error detected in any session | Deliver immediate out-of-cycle briefing |
| User just spoke to a session | Delay next briefing by 5 min (they're engaged) |
| Late night / outside work hours | Pause unless critical errors |
| Only 1 session active | Reduce to simple "still working" pings |

```python
def should_brief_now(state: SystemState) -> bool:
    if all(s.idle for s in state.sessions):
        return False  # nothing to report
    if state.last_user_voice < timedelta(minutes=2):
        return False  # user is actively talking, don't interrupt
    if state.has_critical_error and not state.error_already_reported:
        return True   # immediate briefing for new errors
    return state.time_since_last_briefing >= state.configured_interval
```

### Urgency Tiers

Briefings use different voice tones based on content:

| Tier | Trigger | Voice Style |
|------|---------|-------------|
| **Routine** | Scheduled interval, no issues | Calm, low energy |
| **Notable** | Session completed or new error | Normal energy |
| **Urgent** | Session stuck 10+ min, repeated errors | Slightly faster, direct |

```python
# Tone tag selection
if any(d.type == "new_error" for d in deltas):
    briefing = f"[serious] Heads up. {briefing_text}"
elif any(d.type == "ended" for d in deltas):
    briefing = f"[cheerful] Good news. {briefing_text}"
else:
    briefing = briefing_text  # neutral
```

## CLI & Configuration

### Config

```yaml
# In ~/.agentwire/config.yaml
briefings:
  enabled: true
  interval_minutes: 10        # base interval
  min_interval_minutes: 3     # floor (even during urgent periods)
  max_sessions_detailed: 5    # summarize rest as "and N others"
  quiet_hours:                # pause non-critical briefings
    start: "22:00"
    end: "07:00"
  voice: null                 # use default voice, or specify one
```

### CLI Commands

```bash
# Manual briefing right now
agentwire briefing now

# Enable/disable
agentwire briefing on
agentwire briefing off

# Set interval
agentwire briefing interval 5m

# View last briefing text
agentwire briefing last

# Voice control
# "Give me an update" → triggers immediate briefing
# "Skip" during briefing → stops TTS playback
# "Pause updates" → disables until "resume updates"
```

### MCP Tool

```python
@mcp.tool()
def briefing_now() -> str:
    """Trigger an immediate voice briefing of all active sessions."""

@mcp.tool()
def briefing_configure(enabled: bool | None = None, interval: int | None = None) -> str:
    """Configure periodic briefing settings."""
```

## Implementation Considerations

### LLM Cost

Each briefing requires one cheap LLM call (Haiku-tier) to synthesize natural language from structured state. At 10-minute intervals over an 8-hour day, that's ~48 calls — negligible cost (~$0.02/day with Haiku).

### TTS Interruption

Briefings should never interrupt:
- Active user voice input (PTT in progress)
- Another TTS utterance still playing
- A session that just received user attention

Queue the briefing and deliver at the next quiet moment.

### Portal UI Integration

The portal could show a "Next briefing in: 7:32" countdown and a toggle switch. Past briefings could appear as collapsible cards in a sidebar — text version of what was spoken, with links to jump to each session mentioned.

### Relationship to Existing Alerts

Briefings complement, not replace, existing idle/error alerts:
- **Alerts** = immediate, event-driven, single session ("Worker 2 errored")
- **Briefings** = periodic, synthesized, cross-session ("Here's everything")

If an alert already covered an error, the briefing should reference it: "As I mentioned, CI-fixer is still stuck on that TypeScript error."

## Potential Challenges

1. **Annoying repetition**: If nothing changes, hearing "no updates" every 10 minutes gets old fast.
   - Solution: Intelligent suppression — skip briefings when nothing meaningful changed. Increase interval dynamically during quiet periods.

2. **Information density vs. brevity**: 5 active sessions with nuanced states is hard to compress into 30 seconds.
   - Solution: Prioritize ruthlessly. Problems > progress > idle. Cap at 3 detailed mentions, summarize rest. "Plus two other sessions running normally."

3. **Synthesis accuracy**: The LLM might misinterpret raw session output (e.g., test output that looks like errors but isn't).
   - Solution: Feed structured state (status enum, error boolean, idle duration) not raw output. Use raw output only for color/detail.

4. **Timing conflicts with voice commands**: User says "deploy to staging" right as a briefing starts.
   - Solution: PTT always takes priority. Cancel queued briefing if PTT activates. Re-queue for after the interaction settles.

5. **Multi-machine latency**: Gathering state from remote machines adds delay.
   - Solution: Cache last-known state per machine. Briefings use cached state if fresh enough (<30s). Stale machines get noted: "Haven't heard from gpu-server in a few minutes."
