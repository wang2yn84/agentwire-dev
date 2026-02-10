# Worker Fencing: Blast Radius Control for Spawned Workers

> Assign file/directory boundaries to workers so they can only modify what they're supposed to, preventing unintended collateral changes.

## Problem

Workers go rogue. Not maliciously - they just have poor spatial awareness of a codebase they've never seen:

```
Orchestrator: "Fix the login form validation in src/components/auth/LoginForm.tsx"

Worker: *reads LoginForm.tsx*
Worker: "The validation logic is duplicated, let me refactor..."
Worker: *modifies src/lib/validators.ts* (shared utility, 14 other components use it)
Worker: *modifies src/components/auth/RegisterForm.tsx* (while I'm here...)
Worker: *modifies src/hooks/useAuth.ts* (this could be cleaner)
Worker: "Done! I improved the whole auth system."

Orchestrator: *three other features now broken*
```

This happens regularly because:

1. **Workers have no awareness of boundaries** - they see the whole codebase and "helpfully" wander
2. **Orchestrators can't enforce scope** - "only touch LoginForm.tsx" is a suggestion, not a constraint
3. **Shared files are landmines** - modifying a utility used by 14 files has 14x blast radius
4. **Parallel workers conflict** - Worker A and Worker B both "improve" the same shared file
5. **Rollback is painful** - untangling a worker's 7-file change to keep 1 file is tedious

The wider the worker's reach, the more likely it breaks something outside its task.

## Proposed Solution

**Worker Fencing** - declare file/directory boundaries when spawning workers. The system enforces these boundaries, blocking writes outside the fence.

### Fence Declaration

When spawning a worker, specify its allowed scope:

```bash
# CLI
agentwire spawn --roles worker --fence "src/components/auth/*"
agentwire spawn --roles worker --fence "src/api/users.ts,src/api/types.ts"

# MCP
agentwire_pane_spawn(
    roles="worker",
    fence="src/components/auth/*"
)
```

### Fence Syntax

```yaml
# Single file
fence: "src/components/auth/LoginForm.tsx"

# Directory (all files within)
fence: "src/components/auth/"

# Glob pattern
fence: "src/components/auth/*.tsx"

# Multiple paths (comma-separated)
fence: "src/api/users.ts, src/api/types.ts, tests/api/users.test.ts"

# Include + exclude
fence: "src/components/auth/*, !src/components/auth/index.ts"

# Everything under a directory, plus specific files elsewhere
fence: "src/features/auth/**, src/types/auth.ts"
```

### Enforcement Mechanism

A lightweight file-system watcher or git hook that intercepts writes:

```python
class FenceGuard:
    def __init__(self, pane_id: int, fence_patterns: list[str]):
        self.pane_id = pane_id
        self.allowed = compile_patterns(fence_patterns)

    def check_write(self, file_path: str) -> bool:
        """Return True if this write is within the fence."""
        return any(pattern.matches(file_path) for pattern in self.allowed)

    def on_violation(self, file_path: str):
        """Called when a worker tries to write outside its fence."""
        # Revert the change
        restore_file(file_path)

        # Notify orchestrator
        alert_pane_0(
            f"[FENCE] Worker pane {self.pane_id} tried to modify "
            f"{file_path} which is outside its fence. Change reverted."
        )

        # Log for debugging
        log_violation(self.pane_id, file_path)
```

### Implementation Options

**Option A: Git-based (preferred)**

Use a pre-commit hook scoped to the worker's pane:

```bash
#!/bin/bash
# .git/hooks/pre-commit (or damage-control integration)
PANE_ID=$(tmux display-message -p '#{pane_index}' 2>/dev/null)
FENCE_FILE=".agentwire/fences/pane-${PANE_ID}.txt"

if [ -f "$FENCE_FILE" ]; then
    CHANGED_FILES=$(git diff --cached --name-only)
    while IFS= read -r file; do
        if ! grep -qf "$FENCE_FILE" <<< "$file"; then
            echo "FENCE VIOLATION: $file is outside worker's allowed scope"
            echo "Allowed: $(cat $FENCE_FILE)"
            exit 1
        fi
    done <<< "$CHANGED_FILES"
fi
```

**Option B: Filesystem watch (real-time)**

Use `fswatch` or `watchman` to monitor file changes per-pane:

```python
async def watch_fence(pane_id: int, fence: list[str], project_dir: str):
    """Watch for file changes outside the fence."""
    async for event in fs_watch(project_dir):
        if event.pane == pane_id and event.type == "modify":
            if not fence_allows(fence, event.path):
                await revert_change(event.path)
                await alert_orchestrator(pane_id, event.path)
```

**Option C: Agent-level instruction (soft fence)**

Inject fence awareness into the worker's prompt:

```
IMPORTANT: You are fenced to these files only:
- src/components/auth/LoginForm.tsx
- src/components/auth/LoginForm.test.tsx

You may READ any file for context, but you may ONLY WRITE to the files listed above.
If you believe changes are needed outside your fence, report this in your summary
under "Suggested Changes Outside Fence" - do NOT make the changes yourself.
```

### Recommended: Layered Approach

Use all three for defense in depth:

1. **Soft fence** (prompt injection) - guides the agent's behavior
2. **Git hook** (pre-commit) - catches changes at commit time
3. **Alert** (notification) - orchestrator knows when boundaries are tested

### Fence Templates

Common fence patterns for typical tasks:

```yaml
# In .agentwire.yml
fence_templates:
  component:
    pattern: "src/components/{{ name }}/**"
    description: "Single component and its tests"

  feature:
    pattern: "src/features/{{ name }}/**, tests/features/{{ name }}/**"
    description: "Feature module with tests"

  api-endpoint:
    pattern: "src/api/{{ name }}.ts, src/api/types.ts, tests/api/{{ name }}.test.ts"
    description: "API endpoint with types and tests"

  styles-only:
    pattern: "**/*.css, **/*.scss, **/*.module.css"
    description: "Only stylesheets"
```

Usage:
```bash
agentwire spawn --roles worker --fence-template component --fence-var name=auth/LoginForm
```

### Read vs Write Fencing

Workers need to read broadly but write narrowly:

```yaml
fence:
  read: "**/*"                           # Can read anything for context
  write: "src/components/auth/Login*"     # Can only modify these
```

This is the default: read is unrestricted, write is fenced. The orchestrator can restrict reads too for sensitive areas:

```yaml
fence:
  read: "src/**, !src/secrets/**, !.env*"
  write: "src/components/auth/Login*"
```

### Fence Reporting

Workers report fence interactions in their summaries:

```markdown
# Worker Summary

## Fence
Allowed: `src/components/auth/LoginForm.tsx`, `src/components/auth/LoginForm.test.tsx`

## Files Changed
- `src/components/auth/LoginForm.tsx` (modified) - Added email validation

## Suggested Changes Outside Fence
- `src/lib/validators.ts` - The `validateEmail` function has a bug
  (missing TLD check). Recommend fixing separately.
- `src/components/auth/RegisterForm.tsx` - Has the same validation
  issue. Should be updated after validators.ts is fixed.
```

The orchestrator now has actionable intel without collateral damage.

### Conflict Prevention for Parallel Workers

When multiple workers run simultaneously, fences prevent conflicts:

```
Worker 1 fence: src/components/auth/**
Worker 2 fence: src/components/settings/**
Worker 3 fence: src/api/**

# No overlap → no conflicts → parallel execution is safe
```

If the orchestrator tries to spawn overlapping fences:

```
$ agentwire spawn --roles worker --fence "src/api/**"
WARNING: Fence overlaps with Worker 1 (pane 1) on src/api/auth.ts
Options:
  1. Proceed (workers may conflict)
  2. Narrow this fence to exclude overlap
  3. Cancel
```

## CLI Integration

```bash
# Spawn with fence
agentwire spawn --roles worker --fence "src/auth/**"

# View active fences
agentwire fences list
# Pane 1: src/components/auth/**
# Pane 2: src/api/users.ts, src/api/types.ts

# Check if a file is inside any fence
agentwire fences check src/lib/validators.ts
# Not fenced by any active worker

# Modify a running worker's fence (expand scope)
agentwire fences expand --pane 1 "src/lib/validators.ts"

# View violations
agentwire fences violations
# [10:30] Pane 2 tried to modify src/hooks/useAuth.ts (outside fence)
```

## MCP Tools

```python
@mcp.tool()
def pane_spawn(
    roles: str | None = None,
    pane_type: str | None = None,
    fence: str | None = None,
    fence_template: str | None = None,
) -> str:
    """Spawn worker with optional fence.

    fence: Comma-separated file/glob patterns the worker can modify.
    fence_template: Named template from .agentwire.yml fence_templates.
    """

@mcp.tool()
def fences_list(session: str | None = None) -> str:
    """List active fences for all workers in the session."""

@mcp.tool()
def fences_expand(pane: int, additional_paths: str) -> str:
    """Expand a worker's fence to include additional paths."""
```

## Implementation Considerations

### Damage Control Integration

Fencing naturally extends the existing damage-control system. The damage-control hooks already intercept dangerous commands - fencing adds path-based restrictions:

```python
# In damage_control.py
def check_file_write(pane_id: int, file_path: str) -> bool:
    """Check if this pane is allowed to write this file."""
    fence = load_fence(pane_id)
    if fence is None:
        return True  # No fence = unrestricted
    return fence.allows_write(file_path)
```

### Worker Awareness

Inject fence info into the worker's system prompt so the agent knows its boundaries before it starts:

```
You are a focused worker. Your modifications are limited to:
- src/components/auth/LoginForm.tsx
- src/components/auth/LoginForm.test.tsx

If you need changes outside these files, describe them in your summary
under "Suggested Changes Outside Fence" instead of making them directly.
```

### Fence Persistence

Fences live only as long as the worker pane:

```
~/.agentwire/fences/
  pane-1.json   # Created on spawn, deleted on pane exit
  pane-2.json
```

```json
{
  "pane_id": 1,
  "session": "api-server",
  "created": "2024-01-15T10:30:00Z",
  "write_patterns": ["src/components/auth/LoginForm*"],
  "read_patterns": ["**/*"],
  "violations": []
}
```

### Performance

Fence checking is lightweight:
- Glob matching against a short pattern list is microsecond-level
- Only triggers on file writes, not reads
- No continuous filesystem scanning needed if using git hooks

## Potential Challenges

1. **Agent circumvention**: A determined agent could bypass fences by writing to temp files and moving them. Mitigation: fence enforcement at the git level catches this at commit time. The soft prompt + hard enforcement layered approach makes bypass unlikely.

2. **Too-narrow fences**: Orchestrator doesn't know all files a task will need. Mitigation: workers can request fence expansion via their summary, and orchestrators can expand fences on running workers. Start broader and tighten over time.

3. **Build/generated files**: Worker modifies source, but build output changes outside fence. Mitigation: exclude common build directories (dist/, .next/, node_modules/) from fence checking by default.

4. **Test file discovery**: Worker needs to create a new test file that doesn't exist yet. Mitigation: fence patterns support globs, so `tests/auth/**` covers new files too.

5. **Emergency override**: Sometimes workers genuinely need to fix something outside their fence to complete their task. Mitigation: provide `--no-fence` flag for orchestrators to explicitly override, with a log entry for accountability.
