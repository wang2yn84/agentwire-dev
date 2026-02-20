> Living document. Update this, don't create new versions.

# Mission: Model-Specific Worker Roles

**Status:** In Progress
**Goal:** Create worker role variants for different models + project-level constraints

## Completed

### Model-Specific Worker Roles ✓

Created these roles:

| Role | Model | Agent |
|------|-------|-------|
| `claude-worker-sonnet` | Sonnet 4.5 | Claude Code |
| `claude-worker-haiku` | Haiku 4.5 | Claude Code |
| `glm-worker-flash` | GLM-4.7-flash | claudeGLM |

### Leader Variants ✓

Created model-locked leader roles:

| Role | Spawns Only |
|------|-------------|
| `leader-claude` | Claude Code workers |
| `leader-glm` | GLM workers via claudeGLM |

### Model Support in Code ✓

Claude Code supports model from role frontmatter:
- Claude: `--model sonnet` (line 167-168 in `__main__.py`)

---

## Completed (cont.)

### 6. Project-Level Worker Constraints ✓

Add `allowed_workers` to `.agentwire.yml` to constrain which workers a leader can spawn.

#### Schema

```yaml
# .agentwire.yml
type: claude-bypass
roles:
  - leader

# Constraint: only these worker roles are allowed
allowed_workers:
  - claude-worker
  - claude-worker-sonnet
  - claude-worker-haiku
```

If `allowed_workers` is set:
- Leader roles should check before spawning
- Include constraint in leader's system prompt
- MCP pane_spawn should validate (optional enforcement)

#### Implementation

**Step 1: Parse allowed_workers from config**

In `__main__.py`, update `parse_agentwire_yml()` to extract `allowed_workers`:

```python
def parse_agentwire_yml(project_path: Path) -> dict:
    # ... existing parsing ...

    # Parse allowed_workers list
    if 'allowed_workers' in config:
        result['allowed_workers'] = config['allowed_workers']

    return result
```

**Step 2: Include in leader's system prompt**

When building agent command, if `allowed_workers` is set, append to instructions:

```python
if allowed_workers:
    constraint = f"""
## Worker Constraints

This project only allows these worker roles:
{chr(10).join(f'- {w}' for w in allowed_workers)}

When spawning workers, you MUST use one of these roles. Do not spawn workers with other roles.
"""
    # Append to merged instructions
```

**Step 3: (Optional) Enforce in pane_spawn MCP tool**

Add validation to `pane_spawn()` in MCP server:

```python
async def pane_spawn(roles: str = None, ...):
    # Get project config
    config = parse_agentwire_yml(project_path)
    allowed = config.get('allowed_workers')

    if allowed and roles:
        role_list = [r.strip() for r in roles.split(',')]
        for role in role_list:
            if role not in allowed and role not in ['worker']:  # base roles ok
                return {"error": f"Role '{role}' not in allowed_workers"}

    # ... continue with spawn
```

#### Usage Examples

**Claude-only project:**
```yaml
type: claude-bypass
roles:
  - leader
allowed_workers:
  - claude-worker
  - claude-worker-sonnet
  - claude-worker-haiku
```

**Cost-optimized project (no Opus workers):**
```yaml
type: claude-bypass
roles:
  - leader
allowed_workers:
  - claude-worker-sonnet
  - claude-worker-haiku
```

**Mixed project (Claude + GLM):**
```yaml
type: claude-bypass
roles:
  - leader
allowed_workers:
  - claude-worker-sonnet
  - glm-worker
  - glm-worker-flash
```

---

## Remaining Tasks

- [x] Parse `allowed_workers` from `.agentwire.yml`
- [x] Include constraint in leader system prompt
- [ ] (Optional) Enforce in MCP pane_spawn
- [ ] Update CLAUDE.md with examples
- [ ] Test with demo project

## Acceptance Criteria

- [x] `agentwire roles list` shows model-specific variants
- [x] Spawning with `roles="claude-worker-sonnet"` uses Sonnet
- [x] Spawning with `roles="claude-worker-haiku"` uses Haiku
- [x] claudeGLM workers can specify model via role
- [x] `allowed_workers` in config constrains leader
- [x] Leader receives constraint in system prompt
