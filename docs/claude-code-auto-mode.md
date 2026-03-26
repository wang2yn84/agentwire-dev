> Living document. Update this, don't create new versions.

# Claude Code Auto Mode

Released March 24, 2026. Auto mode is a permission level that sits between
`claude-prompted` (manual approval for everything) and `claude-bypass` (no checks).
Before each tool call, a separate Claude Sonnet 4.6 classifier reviews the transcript
and decides whether to approve or block the action automatically.

## How It Works

**Two-layer safety:**
1. **Input guard** — scans tool outputs (file reads, shell results, web fetches) for prompt injection before Claude sees them
2. **Output classifier** — evaluates each proposed tool call using:
   - Stage 1: fast single-token approve/block decision
   - Stage 2: detailed reasoning if Stage 1 flags it

**What the classifier sees:** user messages + tool calls + your CLAUDE.md content.
It deliberately does NOT see Claude's reasoning or tool outputs (prevents manipulation).

**Decision order:**
1. Explicit `allow`/`deny` permission rules resolve immediately
2. Read-only actions → auto-approved
3. File edits inside the working directory → auto-approved
4. Everything else → classifier

**Auto-blocked examples:**
- `curl | bash`, wget piped to execution
- Pushing directly to `main` or force-pushing
- Mass deletion on cloud storage
- Granting IAM or repo permissions
- Production deploys and migrations
- Sending data to external endpoints

**Fallback:** if the classifier blocks 3 consecutive actions or 20 total in a session,
auto mode pauses and falls back to manual prompts. In headless (`-p`) mode, repeated
blocks abort the session entirely.

**Performance (Anthropic's internal tests):**
- False positive rate: ~0.4% (very few safe actions incorrectly blocked)
- False negative rate: ~6–17% (some unsafe actions get through — not a guarantee)

## CLI Flags

```bash
claude --permission-mode auto       # Start session in auto mode
claude --enable-auto-mode           # Alternative flag
```

Cycle through modes interactively with **Shift+Tab**:
`default → acceptEdits → plan → auto`

## Constraints

| Constraint | Detail |
|------------|--------|
| **Anthropic API only** | Classifier runs on Claude Sonnet 4.6, requires Anthropic auth. Z.AI / GLM-5 sessions cannot use auto mode. |
| **Plan required** | Team, Enterprise, or API plan. Free tier excluded. Admin must enable it on Team/Enterprise first. |
| **Model requirement** | Session must use Claude Sonnet 4.6 or Opus 4.6. Not available on Haiku or claude-3 models. |
| **Not a safety guarantee** | Classifier is imperfect. Don't run on production systems without backups. |

## Comparison to Existing Modes

| Mode | agentwire type | Permission behavior |
|------|---------------|---------------------|
| `bypassPermissions` | `claude-bypass` | No checks — full automation |
| `auto` | `claude-auto` *(planned)* | Classifier approves/blocks |
| `default` | `claude-prompted` | Manual approval for each tool call |
| `plan` | `claude-restricted` | Read-only, no writes |

## Agentwire Integration

### Adding `claude-auto` session type

Auto mode maps cleanly to a new session type. Implementation would be:

**`agentwire/project_config.py`** — add to `SessionType`:
```python
CLAUDE_AUTO = "claude-auto"   # Claude with --permission-mode auto
```

**`to_cli_flags()`** — add case:
```python
elif self == SessionType.CLAUDE_AUTO:
    return ["--permission-mode", "auto"]
```

**`.agentwire.yml` usage:**
```yaml
type: claude-auto
roles:
  - agentwire
```

**`build_agent_command()`** in `__main__.py** — add handling:
```python
if session_type == "claude-auto":
    parts.append("--permission-mode auto")
```

### When to use `claude-auto` vs `claude-bypass`

| Scenario | Recommended type |
|----------|-----------------|
| Overnight scheduled tasks on local machine | `claudeglm-bypass` (current default — Z.AI, cheaper) |
| Overnight tasks on shared/CI infra where you want a safety net | `claude-auto` |
| Interactive sessions where you trust the task direction | `claude-auto` |
| Fully trusted local dev, maximum speed | `claude-bypass` |
| Sensitive repos / customer data in context | `claude-prompted` |

### Scheduling consideration

The scheduler currently defaults to `claudeglm-bypass` for all tasks. For tasks that:
- Touch production-adjacent branches (e.g., `pr_target: main`)
- Run in shared environments
- Have `starting_session` context from sensitive conversations

...`claude-auto` would provide a safety net without slowing down low-risk tasks.

Scheduler YAML could specify it per-task:
```yaml
tasks:
  risky-migration-task:
    type: claude-auto   # classifier safety net for this task
    ...
```

### GLM-5 limitation

Z.AI's GLM-5 sessions (`claudeglm-bypass`, `claudeglm-prompted`) **cannot** use
auto mode. The classifier is Anthropic infrastructure and requires an Anthropic API key.

If auto mode safety guarantees matter for your workload, you'd need to use
`claude-auto` with Anthropic billing — the classifier calls count as additional
Sonnet 4.6 usage on top of your session model.

## Open Questions

1. **Is `--permission-mode auto` the correct flag?** Need to verify exact flag name
   once Anthropic publishes Claude Code changelog entry for the release version
   that shipped auto mode. (`--enable-auto-mode` may be the flag instead.)

2. **Non-interactive / headless auto mode** — when the classifier blocks in headless
   mode (`-p` flag or agentwire `ensure`), it aborts the session. This may interfere
   with the task lifecycle. Worth testing before relying on it for scheduled tasks.

3. **Cost** — each classifier call is a Sonnet 4.6 round-trip. For long tasks with
   many tool calls, this adds non-trivial cost and latency. Not yet benchmarked against
   agentwire's typical task profiles.

## References

- [Anthropic Engineering: Claude Code Auto Mode](https://www.anthropic.com/engineering/claude-code-auto-mode)
- [Claude Blog: Auto Mode Announcement](https://claude.com/blog/auto-mode)
- [Simon Willison write-up](https://simonwillison.net/2026/Mar/24/auto-mode-for-claude-code/)
