# Graceful Voice Interrupts

**One-line summary:** Interrupt long-running agent operations mid-execution via voice commands like "stop", "wait", or "actually..." without killing the session.

## Problem It Solves

When an agent is deep in execution - running tests, making multi-file edits, or following a long chain of reasoning - the only way to stop it is destructive:

1. **Kill the pane** - Loses all context, worker state, partial progress
2. **Wait it out** - Watch the agent do wrong things for minutes
3. **Spam the terminal** - Hope Ctrl+C propagates through tmux (unreliable)

This is especially painful when:

- You realize you gave wrong instructions 30 seconds into a 5-minute task
- The agent is about to make a destructive change you want to prevent
- You have new information that changes what the agent should do
- The agent is going down a rabbit hole and you want to redirect

Voice is supposed to be the primary interface, but voice can't interrupt. You have to reach for the keyboard and kill things, breaking the voice-first flow.

## Proposed Solution

**Interruptible Operations** - A signal layer that allows voice commands to pause, redirect, or cancel in-flight agent work gracefully.

### 1. Interrupt Signals

Three levels of interruption:

| Voice Command | Signal | Agent Behavior |
|---------------|--------|----------------|
| "Wait" / "Hold on" / "Pause" | `PAUSE` | Complete current tool call, then stop and listen |
| "Stop" / "Cancel" / "Nevermind" | `CANCEL` | Abort current operation, rollback if possible, listen |
| "Actually..." / "No wait..." | `REDIRECT` | Complete current tool, then ask for new direction |

### 2. Implementation Architecture

```
Portal (voice receiver)
    │
    ├─ Detects interrupt phrase
    │
    ▼
Signal File: .agentwire/interrupt-{pane}
    │
    ├─ Agent polls between tool calls
    │
    ▼
Agent checks signal → responds appropriately
```

**Signal file approach** (simplest):
- Portal writes interrupt signal to `.agentwire/interrupt-{session}-{pane}`
- Agents check this file between tool invocations (built into roles)
- Agent responds to signal, clears file, speaks acknowledgment

**Claude Code hook approach** (tighter integration):
- New hook type: `interrupt_check` runs between tool calls
- Hook reads signal file, returns interrupt instruction if present
- Agent's next response handles the interrupt

### 3. Role Integration

Add interrupt handling to leader/worker roles:

```markdown
## Interrupt Handling

Before each tool call, check `.agentwire/interrupt-{pane}`:

- If `PAUSE`: Stop, say "Paused. What's up?", wait for instruction
- If `CANCEL`: Abort current task, say "Cancelled. What would you like instead?"
- If `REDIRECT`: Complete current tool, say "Okay, go ahead", wait for new direction

After handling interrupt, delete the signal file.
```

### 4. Voice Grammar

Portal listens for interrupt phrases continuously (not just during push-to-talk):

```python
INTERRUPT_PHRASES = {
    # Pause
    "wait": "PAUSE",
    "hold on": "PAUSE", 
    "hold up": "PAUSE",
    "pause": "PAUSE",
    "one sec": "PAUSE",
    
    # Cancel
    "stop": "CANCEL",
    "cancel": "CANCEL",
    "nevermind": "CANCEL",
    "never mind": "CANCEL",
    "abort": "CANCEL",
    
    # Redirect  
    "actually": "REDIRECT",
    "no wait": "REDIRECT",
    "wait actually": "REDIRECT",
}
```

### 5. Confirmation and Recovery

When interrupted:

```
[Agent making edits...]

[User]: "Wait"
[System]: *writes PAUSE signal*
[Agent]: *checks signal, stops*
[Agent speaks]: "Paused. What's up?"

[User]: "I realized the test file is in a different folder"
[Agent speaks]: "Got it. Which folder?"
[User]: "tests/integration not tests/unit"
[Agent speaks]: "Okay, resuming with the correct path"
[Agent continues with corrected information]
```

### 6. Rollback on Cancel

For destructive operations, maintain a rollback stack:

```yaml
# .agentwire/rollback-{pane}.yaml
operations:
  - type: file_edit
    file: src/auth.ts
    backup: .agentwire/backups/auth.ts.bak
    timestamp: 2024-01-15T10:30:00
  - type: file_create
    file: src/new-file.ts
    # Delete on rollback
```

On `CANCEL`, agent can offer: "I made 2 edits. Want me to roll them back?"

### 7. Continuous Listening Mode

For interrupts to work, portal needs always-on listening for trigger phrases:

```python
# Lightweight wake word detection
# Only full STT when interrupt phrase detected

async def interrupt_listener():
    """Background listener for interrupt phrases."""
    while True:
        # Low-power audio monitoring
        audio = await capture_ambient(duration_ms=500)
        
        # Quick local detection (not full STT)
        if contains_interrupt_phrase(audio):
            phrase = await full_transcribe(audio)
            signal = INTERRUPT_PHRASES.get(normalize(phrase))
            if signal:
                await write_interrupt_signal(signal)
```

## Implementation Considerations

### Agent Polling Frequency

Agents need to check for interrupts frequently enough to be responsive:

- **After every tool call** - Most responsive, minimal overhead
- **Time-based** - Check every 5 seconds during execution
- **Batch-based** - Check after every N operations

Recommend: After every tool call via role instructions.

### Interrupt Latency

Target: <2 seconds from voice to agent pause

- Voice capture: ~300ms
- STT (local whisper): ~500ms
- Signal write: ~50ms
- Agent poll: ~0-1000ms (depends on current tool)
- Agent response: ~200ms

### Tool Call Atomicity

Some operations shouldn't be interrupted mid-execution:

- Database transactions
- Multi-file atomic writes
- Git commits

Agent should complete atomic units, then check for interrupts.

### Multi-Worker Scenarios

When user says "stop", which pane(s) get interrupted?

Options:
1. **All panes** - Simple, but might stop innocent workers
2. **Focused pane** - Requires tracking which pane user is "talking to"
3. **Recent speaker** - Interrupt whoever spoke most recently
4. **Explicit targeting** - "Stop pane 2" / "Stop the auth worker"

Recommend: Recent speaker default, explicit targeting available.

## Potential Challenges

1. **False Positives**
   - "Wait" in regular conversation triggers interrupt
   - Mitigation: Require phrase + silence, or specific intonation
   - Consider: "Hey Agent, wait" as the trigger pattern

2. **Race Conditions**
   - Interrupt arrives while agent is mid-tool-call
   - Mitigation: Queue interrupts, agent handles after current operation

3. **State Corruption**
   - Cancel during partial multi-step operation
   - Mitigation: Transaction-style checkpoints, rollback capability

4. **Always-On Listening Privacy**
   - Constant audio monitoring feels intrusive
   - Mitigation: Local-only processing, no cloud for interrupt detection
   - Alternative: Physical interrupt button on desk

5. **Agent Compliance**
   - Agent might not check interrupt file
   - Mitigation: Build into role system, add interrupt awareness to all roles
   - Fallback: Harder interrupt via SIGINT to process

## CLI Commands

```bash
# Manual interrupt (from separate terminal)
agentwire interrupt -s session --pane 1 --signal pause
agentwire interrupt -s session --all --signal cancel

# View pending interrupts
agentwire interrupt status -s session

# Clear stale interrupts
agentwire interrupt clear -s session
```

## MCP Tools

```python
@mcp.tool()
def interrupt_send(
    pane: int | None = None,
    session: str | None = None,
    signal: Literal["pause", "cancel", "redirect"] = "pause"
) -> str:
    """Send interrupt signal to a pane.
    
    Use when you need to stop or redirect another pane's work.
    """

@mcp.tool()
def interrupt_check() -> str | None:
    """Check if an interrupt signal was received.
    
    Call this between operations. Returns signal type or None.
    """
```

## Success Metrics

- Time from "stop" to agent acknowledgment: <2 seconds
- Successful interrupt rate: >95% (vs killed sessions)
- User satisfaction: "feels responsive to voice"
- Reduced pane kills for mid-task corrections

## Relationship to Existing Features

- **Worker auto-kill on idle**: Orthogonal - interrupts are for active workers
- **Voice handoff**: Complementary - interrupt before handoff if needed
- **Task pivot protocol**: Similar goal, interrupts are faster/simpler for quick corrections
