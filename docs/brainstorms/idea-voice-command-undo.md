# Voice Command Undo

> "Undo that" - reverse voice commands and their effects, not just edits.

## Problem

Voice commands are fire-and-forget. Once you say "add rate limiting to the API," the agent starts working, files get changed, and there's no clean way back:

1. **Misheard commands** - STT transcribes "add rate limiting" as "add rate limping" and the agent tries to interpret nonsense
2. **Wrong target** - You meant the auth API, but the agent modified the payments API
3. **Scope creep** - Asked for a simple fix, agent refactored three files
4. **Changed mind** - Halfway through, you realize this isn't the right approach
5. **Cascade effects** - Agent spawned workers, started tests, deployed to staging - all from one wrong command

Currently, recovery requires:
- Manual git operations (`git checkout`, `git stash`)
- Killing workers one by one
- Hoping you remember the exact state before the command
- Often just starting a fresh session

**Voice-first interactions should have voice-first recovery.**

## Proposed Solution

**Command Undo System** - each voice command creates an undo point. "Undo" reverses the command's effects intelligently.

### Core Concepts

#### 1. Command Boundaries

Every voice command becomes a discrete unit with:
- Timestamp and transcript
- Pre-command state snapshot (lightweight)
- List of effects (files, workers, git operations)
- Status (executing, completed, undone)

```yaml
command:
  id: cmd_20240115_103045
  transcript: "Add rate limiting to the API endpoints"
  started: "2024-01-15T10:30:45"
  completed: "2024-01-15T10:32:12"
  effects:
    files_modified:
      - path: src/middleware/rateLimit.ts
        action: created
        backup: ~/.agentwire/undo/cmd_20240115_103045/rateLimit.ts.orig
      - path: src/routes/api.ts
        action: modified
        backup: ~/.agentwire/undo/cmd_20240115_103045/api.ts.orig
    workers_spawned:
      - pane: 1
        status: completed
    git_operations:
      - type: commit
        hash: abc123
    processes_started: []
```

#### 2. Undo Invocation

Voice-first undo:

```
[User]: "Undo"
[System]: "Undoing rate limiting addition. Restoring 2 files, removing 1 commit."

[User]: "Undo the last two commands"
[System]: "Undoing rate limiting and the test fix before it."

[User]: "Undo the auth changes"
[System]: "Found command 'update auth flow' from 10 minutes ago. Undo that?"
[User]: "Yes"
```

Or via CLI:

```bash
agentwire undo                    # Undo last command
agentwire undo 2                  # Undo last 2 commands
agentwire undo --list             # Show recent commands
agentwire undo cmd_20240115_103045  # Undo specific command
agentwire undo --dry-run          # Preview undo effects
```

#### 3. Undo Depth

Not all effects are equally reversible:

| Effect | Reversibility | Method |
|--------|---------------|--------|
| File modifications | Full | Restore from backup |
| File creation | Full | Delete file |
| File deletion | Full | Restore from backup |
| Git commits (unpushed) | Full | `git reset` |
| Git commits (pushed) | Partial | Revert commit |
| Worker spawn | Full | Kill worker |
| External API calls | None | Cannot undo |
| Database changes | Depends | Requires migration/restore |
| Deployed changes | None | Requires redeploy |

System warns about irreversible effects:

```
[User]: "Undo"
[System]: "I can undo the file changes and git commit, but the Slack message
          I sent cannot be unsent. Proceed with partial undo?"
```

#### 4. Undo Groups

Related commands group together:

```
[User]: "Fix the login bug"
  → Agent: reads files, makes changes, runs tests, commits
  = All actions grouped under one undo boundary

[User]: "Undo"
  → Entire "fix login bug" sequence undone together
```

Workers inherit their orchestrator's undo group - undoing the orchestrator's command also undoes worker effects.

### State Capture

#### Lightweight Snapshots

Full workspace snapshots are expensive. Instead, capture:

**Pre-command:**
- List of files that might be touched (from agent's plan)
- Copy only those files to undo storage
- Current HEAD commit hash

**During execution:**
- Log every file operation with original content
- Track spawned workers and their operations
- Record git operations

**Storage structure:**
```
~/.agentwire/undo/
  cmd_20240115_103045/
    manifest.yaml           # Command metadata
    files/
      src/middleware/rateLimit.ts.orig
      src/routes/api.ts.orig
    git_state.yaml          # Commit hashes, branch state
```

#### Retention Policy

```yaml
# config.yaml
undo:
  enabled: true
  retention:
    commands: 50           # Keep last 50 commands
    age: "24h"             # Or last 24 hours
    max_size: "500MB"      # Cap total storage
  auto_cleanup: true       # Clean up old undo data
```

### Undo Execution

When "undo" triggers:

```python
async def undo_command(command_id: str, dry_run: bool = False) -> UndoResult:
    """Reverse a command's effects."""
    command = load_command(command_id)

    # Check what can be undone
    reversible = []
    irreversible = []

    for effect in command.effects:
        if effect.can_undo():
            reversible.append(effect)
        else:
            irreversible.append(effect)

    if dry_run:
        return UndoResult(reversible=reversible, irreversible=irreversible)

    # Kill any active workers from this command
    for worker in command.effects.workers_spawned:
        if worker.is_active():
            await kill_worker(worker.pane)

    # Restore files
    for file_mod in command.effects.files_modified:
        restore_file(file_mod.path, file_mod.backup)

    # Undo git operations (reverse order)
    for git_op in reversed(command.effects.git_operations):
        await undo_git_operation(git_op)

    # Mark command as undone
    command.status = "undone"
    save_command(command)

    return UndoResult(success=True, undone=reversible)
```

### Integration with Voice Flow

#### Cancel vs Undo

Two distinct operations:

**Cancel** (interrupt): "Stop" / "Cancel"
- Halts execution immediately
- Partial effects may remain
- Used when command is still running

**Undo** (reverse): "Undo" / "Undo that"
- Reverses completed effects
- Works on completed commands
- Restores previous state

```
[User]: "Add logging to all endpoints"
[Agent]: *starts working*
[User]: "Stop"  ← Cancel (partial work may remain)

vs.

[User]: "Add logging to all endpoints"
[Agent]: "Done - added logging to 12 endpoints"
[User]: "Undo"  ← Undo (full reversal)
```

#### Confirmation for Large Undo

```
[User]: "Undo the last 5 commands"
[System]: "That would revert 23 file changes and 4 commits. Are you sure?"
[User]: "Yes"
```

Thresholds configurable:

```yaml
undo:
  confirm_threshold:
    files: 10
    commits: 3
    commands: 3
```

### MCP Tools

```python
@mcp.tool()
def undo(
    count: int = 1,
    command_id: str | None = None,
    dry_run: bool = False
) -> str:
    """Undo recent voice commands.

    Args:
        count: Number of commands to undo (default: 1)
        command_id: Specific command ID to undo
        dry_run: Preview changes without applying

    Returns:
        Summary of what was (or would be) undone.
    """

@mcp.tool()
def undo_list(limit: int = 10) -> str:
    """List recent commands that can be undone.

    Returns command ID, transcript, timestamp, and status.
    """

@mcp.tool()
def undo_status(command_id: str) -> str:
    """Get detailed status of a command's effects.

    Shows files modified, workers spawned, git operations, and reversibility.
    """
```

### CLI Commands

```bash
# Basic undo
agentwire undo                     # Undo last command
agentwire undo 3                   # Undo last 3 commands
agentwire undo --command <id>      # Undo specific command

# Preview
agentwire undo --dry-run           # Show what would be undone
agentwire undo --list              # List recent undoable commands
agentwire undo --status <id>       # Show command's effects

# Management
agentwire undo --cleanup           # Clean old undo data
agentwire undo --disable           # Disable undo tracking temporarily
agentwire undo --storage           # Show undo storage usage
```

### Voice Commands

| Command | Action |
|---------|--------|
| "Undo" | Undo last command |
| "Undo that" | Same as undo |
| "Undo the last two" | Undo last 2 commands |
| "Undo the auth changes" | Search and undo by keyword |
| "What did I just do?" | List recent commands |
| "Can I undo that?" | Check if last command is reversible |
| "Redo" | Re-apply undone command |

## Implementation Considerations

### Git Integration

For git operations, need careful handling:

```python
async def undo_git_operation(op: GitOperation):
    if op.type == "commit":
        if is_pushed(op.hash):
            # Create revert commit instead
            await run(f"git revert --no-edit {op.hash}")
        else:
            # Safe to reset
            await run(f"git reset --soft HEAD~1")

    elif op.type == "branch":
        await run(f"git branch -d {op.branch}")

    elif op.type == "merge":
        # Complex - may need manual intervention
        raise UndoRequiresManualIntervention("Merge undo needs review")
```

### Worker Undo Coordination

When undoing an orchestrator command that spawned workers:

1. Kill active workers (they're now invalid)
2. Read worker summaries for effect tracking
3. Undo worker effects in reverse order
4. Undo orchestrator effects

Workers should log their file operations to their summary:

```markdown
# Worker Summary

## Files Changed
- `src/auth/login.ts` (modified)
- `src/auth/types.ts` (modified)
```

### File Backup Strategy

**Option A: Copy-on-Write**
- Copy file before first modification
- Simple, guarantees restore
- Higher storage use

**Option B: Git Stash**
- Stash changes before command
- Lower storage
- Requires clean working tree

**Option C: Diff-based**
- Store reverse diffs
- Minimal storage
- More complex restore

Recommendation: Option A for simplicity. Storage is cheap, reliability matters.

### Concurrent Commands

If user issues commands while previous is executing:

```
[User]: "Add logging"
[Agent]: *working...*
[User]: "Also add metrics"  ← New command
```

Two approaches:
1. **Sequential boundaries** - Each command is separate, undo reverses in order
2. **Merged boundaries** - Concurrent commands merge into one undo unit

Recommendation: Sequential. Clearer mental model, easier to undo specific work.

## Potential Challenges

### 1. Imperfect Effect Tracking

Not all agent actions are observable:
- Agent might curl an external API
- Agent might modify files outside project
- Database changes through application code

**Mitigation:**
- Track what we can, warn about what we can't
- Sandbox commands to known directories
- Require explicit "external effect" markers for non-reversible actions

### 2. State Divergence

After undo, agent's context is stale (it "remembers" making changes that no longer exist).

**Mitigation:**
- Notify agent of undo: "Your previous changes were undone. Current state: ..."
- Consider undo as context event in agent conversation

### 3. Undo Conflicts

User modifies files manually after agent, then tries to undo:

```
Agent: creates auth.ts
User: manually edits auth.ts
User: "Undo"  ← Would delete user's manual edits
```

**Mitigation:**
- Detect file changes since command completed
- Warn: "auth.ts was modified since the command. Undo anyway?"
- Offer merge: "Keep your changes and just undo the other files?"

### 4. Undo Cascade Complexity

Command A spawns worker B which triggers command C...

**Mitigation:**
- Build effect graph, not just list
- Undo walks graph in reverse topological order
- Surface complexity to user before undoing

### 5. Storage Growth

Active development could generate gigabytes of undo data.

**Mitigation:**
- Aggressive retention limits (24h, 50 commands)
- Deduplicate identical file backups
- Compress old undo data
- Clear undo data on explicit "checkpoint" commands

## Success Metrics

1. **Recovery time** - From "oh no" to "back to normal" under 30 seconds
2. **Undo usage** - Users actually use it (not just git checkout)
3. **Undo success rate** - >95% of undos fully restore state
4. **Reduced session restarts** - Fewer "let me start fresh" moments
5. **Confidence** - Users feel safer issuing ambitious commands knowing they can undo

## Future Extensions

- **Redo** - Re-apply undone commands (useful for A/B comparisons)
- **Branching undo** - Undo to a point, try something else, then "undo the undo"
- **Collaborative undo** - Undo another team member's command
- **Undo across sessions** - Session B undoes effects from session A
- **Time-travel debugging** - Explore state at any undo point interactively
- **Undo analytics** - Track what users commonly undo, improve agent behavior
