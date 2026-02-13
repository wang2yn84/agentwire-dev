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

## Claude Code Limitations

The Claude Code hook is a stateless bash script (`idle-handler.sh`). Key differences from OpenCode:

- **No memory between invocations** — each `idle_prompt` fires a fresh process with no state from prior calls
- **No event bus** — only fires on `idle_prompt` (once per prompt cycle, after 60s idle). Cannot observe retries, busy/idle transitions, or message completions
- **No Gate A (retry detection)** — will inject summary prompts even during rate-limit retry cycles
- **No Gate B (meaningful work detection)** — workers always get the summary prompt on first idle, regardless of whether they did any actual work
- **No activity tracking** — cannot count tool calls, file edits, or completed responses
- **No enriched notifications** — parent notifications are plain text without activity context

**Possible future improvement:** Parse `transcript_path` from the idle_prompt payload for basic activity detection (check if transcript contains tool calls or file modifications).

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
3. Second idle: if `exit_on_complete: true`, sends `/exit`, deletes context file, kills tmux session

**Ensure/Hook Coordination:**

The `ensure` command and idle hook coordinate through two files:

```
ensure                              idle hook (bash)
──────                              ──────────────
1. Write context file
   ~/.agentwire/tasks/{session}.json
2. Send task prompt
3. Poll for completion...
                                    4. First idle → read context file
                                       increment idle_count
                                       send summary prompt to agent

                                    [agent writes summary file]

                                    5. Second idle → read context file
                                       send /exit to agent
                                       DELETE context file  ← cleanup signal
                                       kill tmux session

6. Detect: summary file EXISTS
   AND context file DELETED
   → task complete, proceed to
   on_task_end / post phase
```

**Hook owns context file lifecycle.** The `ensure` command polls for both the summary file AND the context file being deleted. Context file deletion is the "cleanup complete" signal — it means the hook has finished sending `/exit` and killing the session. This prevents a race where `ensure` would proceed (and delete the context file itself) before the hook's second idle pass.

**TASK-ORPHAN safety net (pane 0 only):** If no context file exists but a recent session-scoped summary file is found (within 5 minutes), the hook assumes a scheduled task lost its context. It sends `/exit`, cleans up the orphan summary, and kills the session. This handles edge cases where the context file was deleted prematurely.

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
