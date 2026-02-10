# Checkpoint Commit Protocol

**One-line summary:** Workers create atomic checkpoint commits as they work, enabling step-by-step review, granular rollback, and a clear audit trail before squashing into the final commit.

## Problem It Solves

When workers make changes autonomously, the orchestrator faces a "black box" problem:

1. **Opaque results** - Worker finishes with "Done, 5 files changed" but you don't know what happened in what order
2. **All-or-nothing rollback** - If one part of the work is wrong, you either accept everything or revert everything
3. **Lost reasoning** - Why did the worker change that file? The git log just says "implement feature"
4. **Review friction** - Large diffs are hard to review; breaking changes into logical chunks helps
5. **Debugging difficulty** - When something breaks, unclear which specific change caused it

Currently workers either make one big commit at the end, or make no commits (leaving changes uncommitted). Neither supports incremental review.

## Proposed Solution

**Checkpoint commits** - Workers create small, atomic commits as they work, prefixed with a checkpoint marker. The orchestrator reviews these, then squashes into a clean final commit.

### Core Flow

```
1. Worker spawns, starts task
2. Worker makes change → checkpoint commit "[CP] Add auth middleware skeleton"
3. Worker makes change → checkpoint commit "[CP] Implement token validation"
4. Worker makes change → checkpoint commit "[CP] Add error handling for expired tokens"
5. Worker finishes, signals done
6. Orchestrator reviews checkpoints:
   - All good? → squash into single commit
   - Problem in CP2? → revert CP2-3, spawn new worker for that part
   - Wrong approach? → reset to pre-worker state
7. Clean commit "Add JWT auth middleware" lands on main
```

### Checkpoint Commit Format

```
[CP] <brief description>

<optional longer explanation>

Checkpoint: <checkpoint-id>
Worker: pane-1
Task: add-auth-middleware
```

Example:
```
[CP] Add rate limit constants and types

Defining the rate limit configuration as a separate
constants file to keep values tweakable without
touching logic.

Checkpoint: cp-001-1705312800
Worker: pane-1
Task: add-rate-limiting
```

### Worker Behavior

Workers are instructed (via role) to checkpoint frequently:

```yaml
# In glm-worker role
checkpoint_policy:
  enabled: true
  triggers:
    - after_file_create      # New file = checkpoint
    - after_significant_edit # >20 lines changed = checkpoint
    - after_test_pass        # Tests passing = checkpoint
    - on_logical_boundary    # Worker judgment: "this is a unit of work"

  commit_prefix: "[CP]"
  include_metadata: true     # Add Checkpoint/Worker/Task footer
```

### Orchestrator Review Commands

```bash
# List checkpoints from worker
agentwire checkpoints list --pane 1
# Output:
# cp-001  [CP] Add auth middleware skeleton     +45 -0   2 files
# cp-002  [CP] Implement token validation       +78 -3   1 file
# cp-003  [CP] Add error handling               +23 -5   1 file

# Show specific checkpoint diff
agentwire checkpoints show cp-002

# Accept all and squash
agentwire checkpoints squash --pane 1 --message "Add JWT auth middleware"

# Reject checkpoint (revert it and all after)
agentwire checkpoints revert cp-002  # Reverts cp-002, cp-003

# Accept partial (squash only first N)
agentwire checkpoints squash cp-001 --message "Add middleware skeleton"
# Leaves cp-002, cp-003 as separate commits to rework
```

### Voice Integration

```
[Orchestrator]: "Show me what pane 1 did"
[System]: "Pane 1 made 3 checkpoints: added middleware skeleton,
          implemented token validation, and added error handling.
          Total: 146 lines added across 4 files."

[Orchestrator]: "What's in the second checkpoint?"
[System]: "Token validation - 78 lines in auth slash validate dot ts.
          Imports jsonwebtoken, exports validateToken function,
          handles expired and invalid token errors."

[Orchestrator]: "Squash all three"
[System]: "Squashed into 'Add JWT auth middleware'. Clean history on main."

[Orchestrator]: "Actually, revert the error handling, I want different behavior"
[System]: "Reverted checkpoint 3. Commits 1 and 2 remain.
          Should I spawn a worker to redo error handling?"
```

### MCP Tools

```python
@mcp.tool()
def checkpoints_list(pane: int) -> str:
    """List checkpoint commits from a worker pane.

    Returns checkpoint IDs, messages, and stats (+lines, -lines, files).
    """

@mcp.tool()
def checkpoints_show(checkpoint_id: str) -> str:
    """Show the diff for a specific checkpoint commit.

    Returns the full diff with file names and changes.
    """

@mcp.tool()
def checkpoints_squash(
    pane: int | None = None,
    checkpoint_id: str | None = None,
    message: str | None = None
) -> str:
    """Squash checkpoint commits into a clean commit.

    If pane specified, squash all checkpoints from that worker.
    If checkpoint_id specified, squash up to and including that checkpoint.
    Message is required for the final squash commit.
    """

@mcp.tool()
def checkpoints_revert(checkpoint_id: str) -> str:
    """Revert a checkpoint and all subsequent checkpoints.

    Returns list of reverted checkpoints and current state.
    """
```

## Implementation Considerations

### Git Branch Strategy

Workers operate on temporary branches:

```
main
  └── worker-pane-1-add-auth    ← checkpoints land here
  └── worker-pane-2-add-tests   ← separate branch
```

On squash, the worker branch is merged/rebased to main and deleted.

```python
def setup_worker_branch(pane: int, task: str) -> str:
    """Create isolated branch for worker checkpoints."""
    branch_name = f"worker-pane-{pane}-{slugify(task)}"

    # Create branch from current HEAD
    run(f"git checkout -b {branch_name}")

    return branch_name

def squash_worker_branch(branch: str, message: str):
    """Squash worker branch into single commit on main."""
    # Switch to main
    run("git checkout main")

    # Merge with squash
    run(f"git merge --squash {branch}")

    # Commit with clean message
    run(f"git commit -m '{message}'")

    # Cleanup
    run(f"git branch -D {branch}")
```

### Checkpoint Metadata Storage

Track checkpoint → worker mapping:

```
.agentwire/checkpoints/
  pane-1.json
  pane-2.json
```

```json
{
  "task": "add-auth-middleware",
  "branch": "worker-pane-1-add-auth",
  "checkpoints": [
    {
      "id": "cp-001-1705312800",
      "commit": "abc123",
      "message": "[CP] Add auth middleware skeleton",
      "timestamp": "2024-01-15T10:00:00",
      "stats": { "added": 45, "removed": 0, "files": 2 }
    }
  ]
}
```

### Worker Role Instructions

Add to `glm-worker` role:

```markdown
## Checkpoint Commits

As you work, create checkpoint commits at logical boundaries:

1. **When to checkpoint:**
   - After creating a new file
   - After completing a logical unit (function, class, feature slice)
   - After tests pass for a section
   - Before moving to a different part of the task

2. **Checkpoint format:**
   ```
   git commit -m "[CP] <brief description>"
   ```

3. **Good checkpoint granularity:**
   - ✅ "[CP] Add user validation function"
   - ✅ "[CP] Create API route handlers"
   - ❌ "[CP] Change line 5" (too small)
   - ❌ "[CP] Implement entire feature" (too large)

4. **Checkpoints are for review, not final history.**
   The orchestrator will squash them into clean commits.
```

### Conflict with Worktrees

For git worktree setups (parallel workers on same repo):

- Each worktree already has its own branch
- Checkpoints work naturally within the worktree
- Squash happens within the worktree before PR/merge

No special handling needed.

### Integration with Worker Summaries

Worker summaries should reference checkpoints:

```markdown
# Worker Summary

## Task
Add JWT auth middleware

## Status
─── DONE ───

## Checkpoints
1. cp-001: Add middleware skeleton (+45 lines, 2 files)
2. cp-002: Implement validation (+78 lines, 1 file)
3. cp-003: Add error handling (+23 lines, 1 file)

## Files Changed
- `src/middleware/auth.ts` (created) - main middleware
- `src/middleware/types.ts` (created) - types
- `src/utils/token.ts` (modified) - added validation helper
```

## Potential Challenges

1. **Checkpoint Overhead**
   - Workers spending time on commits vs actual work
   - Mitigation: Make checkpointing fast, don't require detailed messages

2. **Merge Conflicts During Squash**
   - If main moved during worker execution
   - Mitigation: Rebase worker branch before squash, alert on conflicts

3. **Checkpoint Granularity**
   - Too many small checkpoints = noise, too few = back to black box
   - Mitigation: Guidelines in role, configurable triggers

4. **Workers Forgetting to Checkpoint**
   - GLM especially might skip commits
   - Mitigation: Reminder in role, post-edit hook that suggests checkpointing

5. **Branch Proliferation**
   - Many abandoned worker branches
   - Mitigation: Auto-cleanup of branches older than 24h without activity

6. **Squash Message Quality**
   - Orchestrator might write poor squash messages
   - Mitigation: Suggest message based on checkpoint messages, require description

## Success Metrics

- Orchestrators review checkpoints before accepting work (not blind trust)
- Partial reverts used (indicates granular review working)
- Time to debug worker issues decreases (checkpoints isolate problems)
- Final commit history remains clean (no [CP] commits on main)

## CLI Commands

```bash
# Checkpoint management
agentwire checkpoints list [--pane N]        # List checkpoints
agentwire checkpoints show <id>              # Show checkpoint diff
agentwire checkpoints squash [--pane N] -m "msg"  # Squash to clean commit
agentwire checkpoints revert <id>            # Revert checkpoint and later
agentwire checkpoints cleanup                # Remove stale checkpoint branches

# Worker spawn with checkpointing
agentwire spawn --roles glm-worker --checkpoints  # Enable checkpointing (default)
agentwire spawn --roles glm-worker --no-checkpoints  # Disable for quick tasks
```

## Future Extensions

- **Checkpoint approval UI**: Visual diff review in portal
- **Auto-squash on success**: If tests pass, auto-squash without review
- **Checkpoint annotations**: Orchestrator can add notes to specific checkpoints
- **Cross-worker checkpoint ordering**: Manage dependencies between worker checkpoints
- **Checkpoint-level rollback in production**: Deploy per-checkpoint for easier rollback
