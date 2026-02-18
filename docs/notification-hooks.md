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
  - agentwire
  - voice
parent: main  # Notify parent session when idle
```

**For worker panes:**
- No `parent` needed (they auto-notify pane 0)
- Will write summary on first idle
- Will send summary to pane 0 on second idle, then auto-kill

## Claude Code Limitations

The Claude Code hook is a stateless bash script (`idle-handler.sh`):

- **No memory between invocations** — each `idle_prompt` fires a fresh process with no state from prior calls
- **No event bus** — only fires on `idle_prompt` (once per prompt cycle, after 60s idle). Cannot observe retries, busy/idle transitions, or message completions
- **No activity tracking** — cannot count tool calls, file edits, or completed responses

**Possible future improvement:** Parse `transcript_path` from the idle_prompt payload for basic activity detection (check if transcript contains tool calls or file modifications).

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

## Notes

- Hook scripts need full paths to executables (PATH may not be set)
- Use `&` to background long-running commands
- Restart Claude Code after changing settings.json
- Debug logs:
  - `/tmp/claude-hook-debug.log` - Claude Code hook
  - `/tmp/queue-processor-debug.log` - Queue processor
- Summary files: `.agentwire/{session_id}.md` (per-session worker summaries)
