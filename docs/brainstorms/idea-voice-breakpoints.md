# Voice Breakpoints

> Debugger-style breakpoints for agent sessions, with voice confirmation before critical actions.

## Problem

Agents operate autonomously, which is powerful but sometimes scary. Users often want to:

1. **Learn how agents work** - Watch the decision-making process unfold step by step
2. **Catch mistakes early** - Stop before a file is written, not after
3. **Guide without micromanaging** - Let the agent run freely on safe ops, pause on dangerous ones
4. **Build trust incrementally** - Start with many breakpoints, reduce as confidence grows

Currently, the only options are full autonomy (bypass mode) or per-action approval (prompted mode). Nothing in between.

## Proposed Solution

### Breakpoint Types

```yaml
# .agentwire.yml
breakpoints:
  # Action-based breakpoints
  - action: file_write
    pattern: "*.sql"  # Only SQL files
    mode: voice       # voice | alert | auto-approve

  - action: git_commit
    mode: voice

  - action: bash_command
    pattern: "rm *|docker *|npm publish"
    mode: voice

  # State-based breakpoints
  - condition: files_changed > 5
    mode: voice
    message: "About to modify {count} files"

  - condition: token_usage > 50000
    mode: alert
    message: "Token usage high, continue?"

  # Time-based breakpoints
  - trigger: every_5_minutes
    mode: voice
    message: "Still working on {current_task}"
```

### Voice Interaction Flow

When a breakpoint triggers:

1. **Agent pauses** and captures pending action details
2. **Voice announcement**: "About to write schema.sql with 47 lines. Approve, show diff, or cancel?"
3. **User responds** via push-to-talk:
   - "Approved" / "Go ahead" / "Yes" → Continue
   - "Show me" / "What's in it?" → Read summary aloud
   - "Skip this one" → Skip without disabling breakpoint
   - "Cancel" / "Stop" → Abort operation
   - "Disable this breakpoint" → Turn off for rest of session
4. **Agent resumes** or aborts based on response

### Quick Breakpoint Commands

```bash
# Set breakpoints from CLI
agentwire break -s myproject --on file_write
agentwire break -s myproject --on git_commit
agentwire break -s myproject --on "bash:rm *"

# List active breakpoints
agentwire break -s myproject --list

# Clear breakpoints
agentwire break -s myproject --clear

# Voice command while session is running
"Set a breakpoint on database writes"
"Pause before any git operations"
"Remove all breakpoints"
```

### Breakpoint Dashboard

The portal shows:
- Active breakpoints with hit counts
- Recent breakpoint triggers (approved/denied/skipped)
- Suggested breakpoints based on risk analysis
- "Trust meter" showing session's track record

## Implementation Considerations

### Hook Integration

Breakpoints hook into the existing permission system:

```python
# In safety layer
def check_breakpoint(action: str, context: dict) -> BreakpointResult:
    """Check if action triggers a breakpoint."""
    for bp in session.breakpoints:
        if bp.matches(action, context):
            if bp.mode == "auto-approve":
                return BreakpointResult.APPROVED
            elif bp.mode == "alert":
                notify_and_continue(bp, action, context)
                return BreakpointResult.APPROVED
            else:  # voice
                return wait_for_voice_approval(bp, action, context)
    return BreakpointResult.NO_BREAKPOINT
```

### Voice Recognition for Approvals

Pre-trained intents for breakpoint responses:
- Approval: "yes", "go", "approved", "continue", "do it"
- Denial: "no", "stop", "cancel", "abort", "don't"
- Query: "show", "what", "details", "explain"
- Modify: "skip", "disable", "remove breakpoint"

### Timeout Behavior

```yaml
breakpoints:
  default_timeout: 30  # seconds
  timeout_action: deny  # deny | approve | escalate
```

If user doesn't respond within timeout:
- **deny**: Safe default, agent waits or aborts
- **approve**: For low-risk breakpoints where silence = consent
- **escalate**: Send alert to parent session or notification

## Potential Challenges

### Voice Recognition Accuracy

Misinterpreting "no" as "go" would be bad. Mitigations:
- Require explicit phrases ("approved" not just "yes")
- Confirmation for denials: "Canceling the write. Correct?"
- Visual confirmation in portal if connected
- Fallback to text input if voice confidence is low

### Breakpoint Fatigue

Too many breakpoints = user ignores them. Solutions:
- Smart grouping: "About to write 3 config files" not 3 separate prompts
- Learning mode: Track which breakpoints user always approves, suggest disabling
- Risk scoring: Only pause on genuinely novel/risky operations
- Session warmup: More breakpoints at start, fewer as trust builds

### Async Context Loss

Agent might lose context while waiting for approval. Mitigations:
- Capture full context at breakpoint time
- Resume with context injection: "User approved writing schema.sql. Continuing..."
- Timeout with context save if user is AFK

### Multi-Worker Complexity

Multiple workers hitting breakpoints simultaneously:
- Queue breakpoints, process one at a time
- Visual indicator of pending breakpoints in portal
- Option to "approve all from worker 2"
- Priority levels (worker breakpoints queue behind orchestrator)

## Success Metrics

- **Time to first approved action** - How quickly users start trusting agents
- **Breakpoint reduction curve** - Users should disable breakpoints over time
- **Mistake catch rate** - Percentage of errors caught by breakpoints
- **User engagement** - Do users actively manage breakpoints or ignore them?

## Related Ideas

- Complements damage-control hooks (breakpoints are interactive, hooks are automatic)
- Could integrate with session replay for "what would have happened"
- Natural extension of the prompted vs bypass permission model
