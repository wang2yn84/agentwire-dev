# Claude Code Notification Hooks

Findings from testing notification hooks in Claude Code.

## Configuration

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Notification": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/your-handler.sh"
          }
        ]
      }
    ]
  }
}
```

## Notification Types

| Type | Trigger | Matcher |
|------|---------|---------|
| `idle_prompt` | After user prompt + response + 60s idle | `idle_prompt` |
| `permission_prompt` | Tool needs user approval | `permission_prompt` |
| `auth_success` | Authentication completed | `auth_success` |
| `elicitation_dialog` | MCP tool needs parameters | `elicitation_dialog` |

## idle_prompt Behavior (Tested)

**Trigger conditions:**
1. User sends a prompt
2. Claude responds (tool call NOT required, any response works)
3. 60+ seconds of idle time pass

**Firing behavior:**
- Fires once per prompt cycle
- Does NOT repeat automatically while idle
- Fires again after next user prompt + response + 60s idle

**Payload received:**
```json
{
  "session_id": "uuid",
  "transcript_path": "/path/to/transcript.jsonl",
  "cwd": "/current/working/directory",
  "hook_event_name": "Notification",
  "message": "Claude is waiting for your input",
  "notification_type": "idle_prompt"
}
```

**Exit codes:**
- `0` - Allow notification (custom output shown in verbose mode)
- `2` - Suppress notification

## Current Implementation: AgentWire Integration

`~/.claude/hooks/idle-handler.sh` uses a two-pass idle system for worker panes.

**Two-Pass Idle System:**

1. **First idle**: Check if `.agentwire/{session_id}.md` exists
   - If no summary: Send instructions to create one, DON'T exit yet
   - Agent writes summary to `.agentwire/{session_id}.md`

2. **Second idle**: Summary file exists
   - Read the summary content
   - Queue notification with full summary content to parent
   - Kill the worker pane

**Summary file format** (`.agentwire/{session_id}.md`):
```markdown
# Worker Summary

## Task
[What you were asked to do]

## Status
Complete | Blocked | Failed

## What I Did
[Actions taken]

## Files Changed
List files you modified or created with brief descriptions

## What Worked
[Successes]

## What Didn't Work
[Issues and why]

## Notes for Orchestrator
[Context for follow-up]
```

**Features:**
- Uses `agentwire alert` for text-only notifications to parent
- Reads `.agentwire.yml` for voice, parent session config
- Auto-notifies pane 0 when in worker panes
- Notifies parent session if configured (orchestrator hierarchy)
- Skips chatbot sessions (conversational, not task-based)

**Hierarchy:**
```
parent orchestrator ← receives [ALERT from child] with full summary
    ↑ alert --to parent
child orchestrator   ← receives [WORKER SUMMARY pane N] from workers
    ↑ auto-notify pane 0
worker panes         ← write .agentwire/{session_id}.md on first idle
```

**Config example** (`.agentwire.yml`):
```yaml
type: claude-bypass
roles:
  - leader
voice: may
parent: main  # Notify parent session when idle (orchestrator only)
```

**For worker panes:**
- No `parent` needed (they auto-notify pane 0)
- Will write summary on first idle
- Will send summary to pane 0 on second idle, then auto-kill

## OpenCode Support

OpenCode uses an event-bus-aware plugin at `~/.config/opencode/plugins/agentwire-notify.ts`.

### Event Subscriptions

| Event | Purpose |
|-------|---------|
| `session.idle` | Trigger idle handling (gated) |
| `session.status` | Track `busy`/`idle`/`retry` transitions, count busy cycles |
| `message.updated` | Count completed assistant responses (`role=assistant` + `time.completed`) |
| `session.diff` | Detect file changes (non-empty diff array) |
| `session.deleted` | Clean up per-session state |

Note: OpenCode does **not** dispatch `tool.execute.after`, `file.edited`, or `message.part.updated` to plugins. The `message.updated` event with `time.completed` is the primary "real work" signal. The `sessionID` is at `event.properties.info.sessionID` for message events, and `event.properties.sessionID` for session events.

### Activity Tracking

The plugin maintains a `SessionState` per session ID with counters:
- `completedResponses` — from `message.updated` with `role=assistant` + `time.completed`
- `busyCount` — from `session.status` with `type=busy`
- `hasDiffs` — from `session.diff` with non-empty diff array
- `retryCount`, `lastRetryAt`, `inRetryState` — from `session.status` retry events
- `idlePassCount` — how many times idle handler fired
- `summaryRequested` — prevents duplicate summary prompts

### Gate Logic

Before handling idle, two gates must pass:

**Gate A — Rate-limit retry:**
Skip entirely if `inRetryState` is true or `lastRetryAt` is within 10 seconds. Prevents injecting summary prompts during retry cycles (which would cause more API calls, worsening rate limits).

**Gate B — Meaningful work (workers only):**
Requires `completedResponses >= 1` (at least one completed assistant response):
- First idle with no work → grace period (do nothing)
- Second idle with no work → notify `[WORKER FAILED pane N]` + kill pane

Workers that pass both gates get the standard two-pass summary treatment.

### Scheduled Task Support (pane 0)

For orchestrator sessions running `agentwire ensure` tasks:
1. Reads task context from `~/.agentwire/tasks/{session}.json`
2. First idle: increments `idle_count`, sends summary prompt
3. Second idle: if `exit_on_complete: true`, sends `/exit`, cleans up context, kills tmux session

### Enriched Notifications

Parent notifications now include activity context:
- Workers: `[WORKER SUMMARY pane 1] (after work: 5 tool calls, 2 file edits)`
- Failed workers: `[WORKER FAILED pane 1] No meaningful activity detected (3 retries)`
- Orchestrators: `myproject is idle (after work: 12 tool calls, 3 file edits)`

## Notes

- Hook scripts need full paths to executables (PATH may not be set)
- Use `&` to background long-running commands
- Restart Claude Code after changing settings.json
- Debug logs:
  - `/tmp/claude-hook-debug.log` - Claude Code hook
  - `/tmp/opencode-plugin-debug.log` - OpenCode plugin
  - `/tmp/queue-processor-debug.log` - Queue processor
- Summary files: `.agentwire/{session_id}.md` (per-session worker summaries)
