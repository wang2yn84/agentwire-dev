# Context Window Gauge: Know When Your Agent Is Running Out of Room

> Visual indicator showing agent context utilization with proactive warnings before quality degrades.

## Problem

Agent context windows are invisible but critical. When they fill up:

- **Silent degradation**: Agent starts forgetting earlier instructions, making inconsistent choices
- **Mysterious failures**: Tasks that "should work" fail because crucial context was evicted
- **Wasted tokens**: You keep feeding context that's already been pushed out
- **No warning**: You only notice after quality has already tanked

Current experience:
```
[Orchestrator]: "Update the auth flow like we discussed"
[Worker]: Makes changes that ignore earlier decisions
[Orchestrator]: "No, we agreed to use JWT, not sessions"
[Worker]: "I don't see prior discussion of that"
← Context was evicted. Worker operating on partial information.
```

This happens invisibly. There's no "low fuel" warning before you hit empty.

### The Token Blindness Problem

Different agents have different context sizes:
- Claude Opus: 200K tokens
- Claude Sonnet: 200K tokens
- GPT-4: 128K tokens
- Local models: 4K-32K tokens

You can't see utilization. You can't plan around it. You're flying blind.

## Proposed Solution

**Context Window Gauge** - real-time visibility into agent context utilization:

1. **Visual indicator** in portal showing % utilization per session/worker
2. **Proactive alerts** when approaching thresholds (70%, 85%, 95%)
3. **Smart suggestions** for reclaiming context (summarize, reset, checkpoint)
4. **Historical tracking** to identify context-hungry patterns

### Dashboard Integration

```
┌────────────────────────────────────────────────────────────┐
│ Session: auth-api                         Context: ▓▓▓▓▓░░ │
│                                                   72% ~35K │
├────────────────────────────────────────────────────────────┤
│ ● Pane 0: Orchestrator    ▓▓▓▓▓▓░░░  68%  Healthy         │
│ ● Pane 1: JWT impl        ▓▓▓▓▓▓▓▓░  89%  ⚠️ High         │
│ ○ Pane 2: Test fixes      ▓▓▓▓░░░░░  45%  Healthy         │
└────────────────────────────────────────────────────────────┘
```

### Alert Tiers

| Threshold | Alert | Action Suggested |
|-----------|-------|------------------|
| 70% | Informational | "Context getting full" |
| 85% | Warning | "Consider summarizing conversation" |
| 95% | Critical | "Agent may start forgetting. Reset recommended" |

### Voice Notifications

```
[System]: "Worker 1 is at 89% context. It may start forgetting earlier
          instructions. Want me to have it summarize and checkpoint?"
[User]: "Yes"
[System]: "Worker 1 is summarizing. Will restart with condensed context."
```

### Context Reclamation Actions

When context is high, offer actionable options:

1. **Summarize & Continue**: Agent writes a summary, conversation resets with summary as seed
2. **Checkpoint & Fork**: Save state, spawn fresh worker with checkpoint context
3. **Prune History**: Remove old tool outputs, keep decisions
4. **Hard Reset**: Nuclear option, fresh context with task description only

### Per-Model Calibration

```yaml
# ~/.agentwire/config.yaml
context_gauge:
  models:
    claude-opus:
      window_size: 200000
      warn_threshold: 0.70
      critical_threshold: 0.85
    claude-sonnet:
      window_size: 200000
      warn_threshold: 0.70
      critical_threshold: 0.85
    gpt-4:
      window_size: 128000
      warn_threshold: 0.70
      critical_threshold: 0.85
    local-llama:
      window_size: 8000
      warn_threshold: 0.50  # Smaller window = earlier warnings
      critical_threshold: 0.70
```

## Implementation Considerations

### Token Counting

The hard part: accurately estimating token usage without API access to actual counts.

**Approach 1: Heuristic Estimation**
```python
def estimate_tokens(text: str, model: str) -> int:
    # Rough heuristic: 4 characters ≈ 1 token for English
    char_count = len(text)
    base_estimate = char_count / 4

    # Adjust for model tokenizer differences
    multipliers = {
        "claude": 1.0,
        "gpt": 1.1,  # Slightly more tokens typically
        "local": 0.9,
    }
    return int(base_estimate * multipliers.get(model, 1.0))
```

**Approach 2: Tokenizer Libraries**
```python
import tiktoken  # For GPT
from anthropic import count_tokens  # If available

def count_tokens_accurate(text: str, model: str) -> int:
    if "gpt" in model:
        enc = tiktoken.encoding_for_model(model)
        return len(enc.encode(text))
    # Fall back to heuristic for others
```

**Approach 3: Output Parsing**
Claude Code sometimes reports token usage. Parse it when available:
```
# Claude sometimes outputs:
# "Context: 45,234 tokens used"
# Parse this from session output
```

### Tracking Conversation History

Need to track what's been sent to each agent:

```python
class ContextTracker:
    session_id: str
    pane_id: int
    messages: list[Message]  # All messages in conversation

    @property
    def estimated_tokens(self) -> int:
        return sum(estimate_tokens(m.content) for m in self.messages)

    @property
    def utilization(self) -> float:
        return self.estimated_tokens / self.model_context_size
```

Challenge: We don't have direct access to agent conversation history. Solutions:
- Parse from captured output (fragile)
- Track what we send via `pane_send` (incomplete - misses agent-generated content)
- Request agents self-report (requires agent cooperation)

### Agent Self-Reporting

Best accuracy comes from agents reporting their own state:

```python
# In agent role instructions
"""
When context exceeds 70%, proactively report:
[CONTEXT_REPORT tokens=45234 percent=72]

This helps the orchestrator manage resources.
"""
```

Parse these reports from pane output. Agents have accurate token counts.

### UI Components

**Gauge Widget**
```tsx
function ContextGauge({ utilization, threshold }: Props) {
  const color = utilization > 0.85 ? 'red'
              : utilization > 0.70 ? 'yellow'
              : 'green';

  return (
    <div className="context-gauge">
      <div
        className="gauge-fill"
        style={{ width: `${utilization * 100}%`, backgroundColor: color }}
      />
      <span>{Math.round(utilization * 100)}%</span>
    </div>
  );
}
```

**Alert Component**
```tsx
function ContextAlert({ pane, utilization }: Props) {
  if (utilization < 0.70) return null;

  return (
    <Alert variant={utilization > 0.85 ? 'destructive' : 'warning'}>
      Pane {pane} at {utilization}% context.
      <Button onClick={() => summarizeAndReset(pane)}>
        Summarize & Reset
      </Button>
    </Alert>
  );
}
```

### WebSocket Events

```typescript
// Portal → Client
interface ContextUpdate {
  type: 'context_update';
  session: string;
  panes: {
    id: number;
    tokens_estimated: number;
    utilization: number;
    status: 'healthy' | 'warning' | 'critical';
  }[];
}

// Emit on:
// - Every pane_send (new content added)
// - Every captured output parse (if agent reports)
// - Periodic polling (every 30s)
```

### Summarization Protocol

When user triggers summarization:

```python
async def summarize_and_reset(session: str, pane: int):
    # 1. Ask agent to summarize
    await pane_send(pane, """
        Summarize this conversation for context preservation:
        - Key decisions made
        - Current task state
        - Important constraints/requirements
        - Files modified and why

        Write to .agentwire/context-checkpoint.md
    """)

    # 2. Wait for summary file
    summary = await wait_for_file(f".agentwire/context-checkpoint.md")

    # 3. Kill pane, respawn with summary as initial context
    await pane_kill(pane)
    await pane_spawn(roles="worker")
    await pane_send(new_pane, f"""
        Continuing from checkpoint:

        {summary}

        Resume the task from where the previous worker left off.
    """)
```

## Integration with Existing Features

### Worker Health Dashboard

Context gauge integrates naturally with worker health:

```
┌─────────────────────────────────────────────────────────────┐
│ ● Pane 1: Auth endpoints  ▁▂▃▅▇  Working  12s   CTX: 72%  │
│   "Adding JWT middleware..."                    ▓▓▓▓▓▓▓░░  │
└─────────────────────────────────────────────────────────────┘
```

### Voice Macros

Add context-related macros:

```yaml
macros:
  context:
    expand: "report context utilization for all workers"

  checkpoint:
    pattern: "checkpoint {pane}"
    expand: "have pane {pane} summarize and restart with fresh context"
```

### Notification Escalation

Context warnings can escalate:
1. Visual indicator in dashboard
2. Voice notification to orchestrator
3. Email alert if critical for >10 minutes

## Potential Challenges

1. **Accuracy without API access**: We're estimating tokens, not measuring. Solution: Calibrate heuristics against known examples, encourage agent self-reporting.

2. **Tool output bloat**: Agents often receive huge tool outputs (file contents, grep results). These consume context fast but aren't visible in our tracking. Solution: Track tool calls separately, warn about large tool responses.

3. **Different models, different tokenizers**: Token counts vary by model. Solution: Per-model configuration, conservative estimates.

4. **Agent cooperation required**: Best accuracy needs agents to self-report. Solution: Add to role instructions, make it part of worker protocol.

5. **Summarization quality**: Bad summaries lose critical context. Solution: Structured summary template, human review option for critical sessions.

6. **False comfort**: Users may think "80% is fine" and ignore until too late. Solution: Make 70% the first alert, normalize earlier intervention.

## Future Extensions

- **Context forecasting**: "At current rate, you'll hit 90% in ~15 minutes"
- **Per-file context tracking**: "auth.ts contents consuming 12% of context"
- **Context diff**: Show what's taking space, what could be pruned
- **Automatic checkpointing**: Auto-summarize at thresholds without user intervention
- **Cross-session context sharing**: Share summaries between related sessions

## Success Metrics

- Reduced "agent forgot our discussion" incidents
- Earlier intervention (users act at 70% vs discovering at 100%)
- Fewer wasted tokens on evicted context
- User confidence: "I know when to reset"

## Example Day-in-the-Life

```
# Working normally
[Dashboard shows]: Pane 1 at 45%, Pane 2 at 38%

# After lengthy implementation
[Dashboard shows]: Pane 1 at 78% ⚠️
[Voice]: "Worker 1 context at 78%. Consider checkpointing before it forgets
         earlier decisions."

# User acts proactively
[User]: "Checkpoint one"
[System]: "Checkpointing worker 1... Summary saved. Respawning with
          fresh context..."
[Dashboard shows]: Pane 1 at 12% ✓

# Work continues with full context fidelity
```
