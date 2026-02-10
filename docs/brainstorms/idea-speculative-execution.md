# Speculative Execution: Predict and Pre-Work

> Start working on what the user probably needs next, before they ask.

## Problem It Solves

Voice-first interaction has inherent latency: push-to-talk → STT processing → agent thinking → execution → TTS response. Even with a fast system, this round-trip feels slow for common follow-up patterns.

Users develop predictable workflows:
- After "spawn a worker", they almost always "send it a task"
- After "check pane 1", they often "check pane 2" or "read the summary"
- After a worker completes, they usually "test the changes" or "review the diff"
- After fixing a bug, they typically "run tests" or "commit"

The agent sits idle during voice input, waiting. What if it used that time to prepare for likely next steps?

## Proposed Solution

**Speculative Execution** - A prediction layer that identifies likely next actions and pre-executes them in the background, making responses feel instantaneous when predictions hit.

### How It Works

```
┌─────────────────────────────────────────────────────────────┐
│                    User's Perspective                        │
│                                                              │
│  [User]: "Check pane 1"                                      │
│  [System]: "Pane 1 finished the auth fix. Modified 3 files." │
│                                                              │
│  [User]: "Show me the diff"     ← System already has it!     │
│  [System]: "Here's the diff..." ← Instant response           │
└─────────────────────────────────────────────────────────────┘

Behind the scenes:
┌──────────────────────────────────────────────────────────────┐
│ After "check pane 1" completes, predictor fires:             │
│                                                              │
│ Likely next actions (by probability):                        │
│   85% - "show diff" / "what changed"    → pre-fetch diff     │
│   60% - "check pane 2"                  → pre-capture output │
│   45% - "run tests"                     → pre-warm test env  │
│   30% - "send to pane 1: ..."           → no pre-work needed │
│                                                              │
│ Execute top 2-3 predictions speculatively                    │
└──────────────────────────────────────────────────────────────┘
```

### Prediction Sources

**1. Sequential Patterns**
Track command sequences and their frequencies:

```yaml
patterns:
  - sequence: ["spawn worker", "send to worker"]
    probability: 0.92

  - sequence: ["check pane N", "check pane N+1"]
    probability: 0.67

  - sequence: ["worker completes", "run tests"]
    probability: 0.78

  - sequence: ["fix bug", "commit"]
    probability: 0.85
```

**2. Context Signals**
Use current state to predict needs:

```python
def predict_from_context(state: SessionState) -> list[Prediction]:
    predictions = []

    # Worker just completed → likely want to see results
    if state.recent_event == "worker_idle":
        predictions.append(Prediction(
            action="fetch_worker_summary",
            pane=state.event_pane,
            probability=0.90
        ))
        predictions.append(Prediction(
            action="git_diff",
            probability=0.75
        ))

    # Multiple workers running → might check each
    if len(state.active_workers) > 1:
        for pane in state.active_workers:
            if pane != state.last_checked_pane:
                predictions.append(Prediction(
                    action="capture_output",
                    pane=pane,
                    probability=0.5
                ))

    return predictions
```

**3. Time-of-Day Patterns**
Learn user habits:

```yaml
temporal_patterns:
  morning:
    - "check all sessions"
    - "what happened overnight"
  end_of_day:
    - "commit changes"
    - "push to remote"
  after_lunch:
    - "where was I"
    - "session status"
```

**4. Project-Specific Patterns**
Different projects have different workflows:

```yaml
# Frontend project
patterns:
  after_edit: ["check browser", "screenshot"]

# API project
patterns:
  after_edit: ["run tests", "check logs"]
```

### Speculative Actions

Not all actions can be speculated. Categorize by safety:

| Category | Examples | Speculatable? |
|----------|----------|---------------|
| **Read-only** | git diff, capture output, file read | ✅ Yes |
| **Idempotent** | warm up test env, pre-compile | ✅ Yes |
| **Reversible** | stage files (can unstage) | ⚠️ Careful |
| **Destructive** | commit, push, delete | ❌ Never |

```python
SAFE_SPECULATIONS = {
    "capture_pane_output",
    "git_diff",
    "git_status",
    "read_file",
    "read_worker_summary",
    "list_panes",
    "warm_test_runner",
    "pre_compile",
    "fetch_logs",
}
```

### Cache Management

Speculative results are cached briefly:

```python
class SpeculativeCache:
    def __init__(self, ttl_seconds: int = 30):
        self.cache: dict[str, CachedResult] = {}
        self.ttl = ttl_seconds

    def store(self, action: str, result: Any, confidence: float):
        self.cache[action] = CachedResult(
            result=result,
            timestamp=now(),
            confidence=confidence
        )

    def get(self, action: str) -> CachedResult | None:
        cached = self.cache.get(action)
        if cached and (now() - cached.timestamp) < self.ttl:
            return cached
        return None

    def invalidate_on_state_change(self, change: StateChange):
        """Invalidate cache entries affected by state change."""
        # e.g., file edit invalidates git diff cache
        for action, entry in list(self.cache.items()):
            if change.affects(action):
                del self.cache[action]
```

### Request Matching

When user speaks, check cache first:

```python
async def handle_request(request: str) -> Response:
    # Parse intent
    intent = parse_intent(request)

    # Check speculative cache
    cached = speculative_cache.get(intent.action)
    if cached and cached.confidence > 0.7:
        metrics.record("speculation_hit")
        return Response(
            result=cached.result,
            latency_saved=cached.compute_time
        )

    # Cache miss - execute normally
    metrics.record("speculation_miss")
    result = await execute(intent)
    return Response(result=result)
```

### Confidence Thresholds

Don't waste resources on low-probability predictions:

```yaml
speculation:
  min_confidence: 0.5      # Don't speculate below 50%
  max_concurrent: 3        # Max parallel speculations
  max_compute_cost: 0.01   # Don't speculate expensive ops
  cache_ttl: 30            # Seconds to keep results
```

### Learning Loop

Track hit rates and adjust:

```python
def update_pattern_weights(prediction: Prediction, hit: bool):
    """Reinforce or weaken patterns based on accuracy."""
    pattern = prediction.source_pattern

    if hit:
        pattern.weight *= 1.1  # Strengthen
        pattern.hits += 1
    else:
        pattern.weight *= 0.95  # Weaken
        pattern.misses += 1

    # Decay patterns that aren't useful
    if pattern.hit_rate < 0.3 and pattern.samples > 20:
        patterns.deprecate(pattern)
```

## Implementation Considerations

### Integration Points

```
┌─────────────────────────────────────────────────────────────┐
│                      Request Flow                            │
│                                                              │
│  Voice Input → STT → Intent Parser → Cache Check → Execute   │
│                           ↑              │                   │
│                           │              ↓                   │
│                    Pattern Matcher ← State Monitor           │
│                           │                                  │
│                           ↓                                  │
│                    Speculation Engine                        │
│                           │                                  │
│                           ↓                                  │
│              Background Execution Pool                       │
└─────────────────────────────────────────────────────────────┘
```

### State Monitoring

Watch for events that trigger speculation:

```python
@on_event("command_completed")
async def speculate_after_command(event: CommandEvent):
    predictions = predictor.predict(
        last_command=event.command,
        session_state=get_session_state(),
        time_of_day=now().hour
    )

    for pred in predictions[:3]:  # Top 3
        if pred.action in SAFE_SPECULATIONS:
            if pred.probability >= config.min_confidence:
                await speculation_pool.submit(pred)

@on_event("worker_idle")
async def speculate_after_worker_idle(event: WorkerIdleEvent):
    # High confidence: user will want the summary
    await speculation_pool.submit(Prediction(
        action="read_worker_summary",
        pane=event.pane,
        probability=0.95
    ))
```

### Resource Limits

Prevent speculation from overloading the system:

```python
class SpeculationPool:
    def __init__(self, max_concurrent: int = 3):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.running: set[str] = set()

    async def submit(self, prediction: Prediction):
        if prediction.action in self.running:
            return  # Already speculating this

        async with self.semaphore:
            self.running.add(prediction.action)
            try:
                result = await execute_speculation(prediction)
                speculative_cache.store(
                    prediction.action,
                    result,
                    prediction.probability
                )
            finally:
                self.running.discard(prediction.action)
```

### Voice Feedback

Optionally hint at speculation:

```
[User]: "Check pane 1"
[System]: "Pane 1 finished. Modified auth.ts.
          I've got the diff ready if you want to see it."
          ↑ Hints at pre-fetched content
```

### Metrics Dashboard

Track speculation effectiveness:

```
Speculation Stats (Last 24h)
─────────────────────────────────────
Total predictions:     847
Cache hits:           523 (62%)
Avg latency saved:    1.2s per hit
Total time saved:     10.4 minutes

Top Patterns:
  worker_idle → read_summary    89% hit rate
  check_pane → git_diff         73% hit rate
  spawn_worker → send_task      91% hit rate

Wasted compute:
  Misses:        324
  Compute cost:  ~$0.02 (negligible read ops)
```

## CLI / MCP

```bash
# View speculation stats
agentwire speculation stats
agentwire speculation patterns  # Show learned patterns

# Configure
agentwire speculation enable
agentwire speculation disable
agentwire speculation set --min-confidence 0.6
```

```python
@mcp.tool()
def speculation_status() -> str:
    """Show speculation cache and recent predictions."""

@mcp.tool()
def speculation_hint(likely_next: str) -> str:
    """Manually hint at likely next action to pre-compute."""
```

## Potential Challenges

1. **Stale Cache**
   - Speculated results become invalid after state changes
   - Solution: Aggressive invalidation on any file/git/pane changes

2. **Wasted Compute**
   - Low hit rates mean wasted work
   - Solution: Adaptive thresholds, disable for patterns with <30% hit rate

3. **Intent Matching**
   - User might phrase request differently than predicted
   - Solution: Semantic matching, not exact string matching

4. **Prediction Complexity**
   - Some workflows are unpredictable
   - Solution: Focus on high-confidence patterns, don't force speculation

5. **Privacy/Security**
   - Pre-reading files user might not want read
   - Solution: Only speculate within current session's scope, respect .gitignore

## Success Metrics

- **Cache hit rate > 50%** - More hits than misses
- **Perceived latency reduction** - Users report system "feels faster"
- **Time saved** - Track cumulative latency savings
- **Pattern accuracy** - Learned patterns improve over time
- **Resource efficiency** - Speculation compute cost < benefit

## Why This Matters

Voice interaction is fundamentally slower than typing. You can't "type ahead" with voice. Speculative execution compensates by having the system think ahead instead. When it works, the system feels telepathic - it already knows what you need.

Even a 50% hit rate means half your follow-up questions are answered instantly. That's a massive UX improvement for voice-first workflows.
