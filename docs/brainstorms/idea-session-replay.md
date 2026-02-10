# Session Replay: Time-Travel for Agent Sessions

> Record, replay, and learn from agent sessions like watching a coding stream.

## Problem

When agents work autonomously (especially overnight or during meetings), you return to:
- A completed task with no visibility into *how* it was done
- A failed session where you need to debug what went wrong
- Worker panes that already exited, taking their context with them

You have summaries and git diffs, but no way to understand the *journey*. The decision-making process, the rabbit holes, the recoveries—all lost.

## Proposed Solution

**Session Replay** records everything happening in a session and lets you play it back at variable speeds, like watching a coding stream.

### What Gets Recorded

1. **Terminal output** - Full ANSI output with timestamps (already captured via tmux)
2. **Voice events** - TTS utterances and user voice input with timestamps
3. **Agent state transitions** - Idle, working, thinking indicators
4. **Worker lifecycle** - Spawn, task assignment, completion, exit
5. **Tool calls** - Which tools were invoked (file reads, edits, bash commands)

### Recording Format

```yaml
# ~/.agentwire/recordings/{session}-{timestamp}.replay
metadata:
  session: myproject
  started: 2025-02-03T10:00:00Z
  ended: 2025-02-03T10:45:00Z
  duration_seconds: 2700
  worker_count: 3

events:
  - ts: 0
    type: voice_in
    text: "Add user authentication to the API"

  - ts: 2
    type: voice_out
    text: "I'll spawn two workers for this - one for the auth middleware, one for the routes"

  - ts: 5
    type: worker_spawn
    pane: 1
    task: "Auth middleware implementation"

  - ts: 5
    type: worker_spawn
    pane: 2
    task: "Auth route handlers"

  - ts: 8
    type: terminal
    pane: 1
    output: "Reading src/middleware/..."

  # ... thousands of events
```

### Playback Interface

```bash
# CLI playback (terminal-based)
agentwire replay myproject-20250203-100000
agentwire replay --speed 4x myproject-20250203-100000  # Fast forward
agentwire replay --skip-idle myproject-20250203-100000  # Skip waiting periods

# Jump to interesting moments
agentwire replay --at 15:30 myproject-20250203-100000
agentwire replay --event "worker_error" myproject-20250203-100000
```

Portal web UI playback:
- Timeline scrubber showing activity density
- Speed controls (1x, 2x, 4x, 8x)
- Event markers (worker spawns, errors, completions)
- Picture-in-picture for multiple worker panes
- Voice playback with waveform visualization

### Smart Features

**Activity heatmap** - Timeline shows where the action was. Skip long idle periods.

**Event search** - "Show me when it edited auth.ts" jumps to that moment.

**Branch comparison** - Compare two replay sessions that took different approaches.

**Annotated exports** - Export as markdown with screenshots at key moments for documentation.

## Implementation Considerations

### Recording (Low Overhead)

Recording should be nearly free:
- tmux already buffers output; periodically dump to file
- Voice events already flow through the portal; tap the stream
- Worker lifecycle events already exist; add a recorder hook

```python
# In portal/server.py
class ReplayRecorder:
    def __init__(self, session_name: str):
        self.events = []
        self.start_time = time.time()

    def record(self, event_type: str, data: dict):
        self.events.append({
            "ts": time.time() - self.start_time,
            "type": event_type,
            **data
        })

    def save(self):
        # Compress and save to ~/.agentwire/recordings/
```

### Playback Engine

Terminal playback could use a simple approach:
- Parse events chronologically
- Print terminal output with appropriate delays (scaled by playback speed)
- Display voice text inline or speak it
- Show worker status in a header bar

### Storage Management

Recordings can get large. Mitigation:
- Compress with zstd (terminal output compresses well)
- Configurable retention (keep last N days/GB)
- Automatic cleanup of successful short sessions
- Keep failed/long sessions longer (more interesting)

```yaml
# config.yaml
replay:
  enabled: true
  retention_days: 30
  max_storage_gb: 10
  keep_failures: true  # Always keep failed sessions
```

## Potential Challenges

1. **Storage size** - Long sessions with verbose output could be large
   - Mitigation: Compression, sampling for very long sessions, configurable detail levels

2. **Privacy** - Recordings might capture secrets typed in terminal
   - Mitigation: Optional recording, auto-redact patterns, encrypted storage

3. **Playback fidelity** - ANSI escape sequences, terminal dimensions
   - Mitigation: Record terminal dimensions, use xterm.js for faithful replay

4. **Multi-pane complexity** - Showing 4+ worker panes simultaneously
   - Mitigation: Focus mode (one pane at a time) + overview mode (all panes small)

## Use Cases

- **Morning catch-up**: "What did the overnight task do?" Watch at 8x speed.
- **Debugging failures**: Scrub to the error, see what led up to it.
- **Learning**: New team member watches how the agent approaches problems.
- **Demos**: Record a session, export as annotated video for stakeholders.
- **Prompt improvement**: See where agents get confused, improve instructions.

## Future Extensions

- **Shared replays** - Upload to a URL for team viewing
- **AI-generated summaries** - "This session added auth in 3 phases: middleware, routes, tests"
- **Replay diffing** - Compare two approaches to the same task
- **Training data** - Use successful sessions to fine-tune agent behavior
