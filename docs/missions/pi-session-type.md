> Living document. Update this, don't create new versions.

# Mission: Phase 1 — Pi Session Type

Add `pi-zai*` session types to agentwire so users can spawn pi-based interactive sessions the same way they spawn Claude Code sessions today.

**Phase of:** `pi-harness-overview.md`
**Status:** complete (code shipped 2026-04-13)
**Estimated effort:** 2–3 days (actual: 1 day)
**Depends on:** none
**Blocks:** Phase 2 (workflow engine validates pi invocation path)

## Goal

Provide a first-class mechanism for running Z.AI-backed interactive sessions (replacing the removed claudeGLM wrapper). A user should be able to run:

```bash
agentwire new -s myproject --type pi-zai -p ~/projects/myproject
```

...and get a pi session attached to their project, with CLAUDE.md loaded, roles applied, Z.AI models ready.

## Scope

### In Scope

- New session types: `pi-zai`, `pi-zai-restricted`, `pi-zai-readonly`
- `build_agent_command()` extended with pi-zai branch
- Role injection via `--append-system-prompt` (same mechanism as Claude Code)
- Tool restriction via `--tools` flag (translated role tool lists to pi's lowercase names)
- Idle detection updated to recognize pi's `node` process when running
- `.agentwire.yml` validation accepts new types
- Config schema: `pi.default_model` in `config.yaml` (default `glm-5`)
- Documentation: new `docs/pi-zai.md`, updates to `CLAUDE.md`

### Out of Scope (Later Phases)

- Workflow/programmatic mode (Phase 2)
- Scheduler integration (Phase 3)
- Session forking equivalent to `--resume --fork-session` (deferred — pi uses `--session <file> --continue`, needs its own fork helper)
- MCP client for pi (not planned — pi intentionally doesn't ship MCP)
- Claude Code removal / deprecation

## Approach

### 1. Extend `build_agent_command()` in `agentwire/__main__.py`

Add a new branch to handle `pi-zai*` session types (inserted before the `claude` block):

```python
if session_type.startswith("pi-zai"):
    config = load_config()
    zai = config.get("zai", {})
    pi_config = config.get("pi", {})
    
    env_prefix = f"ZAI_API_KEY={shlex.quote(zai.get('api_key', ''))} "
    
    parts = [env_prefix + "pi", "--provider", "zai"]
    
    # Model selection
    default_model = pi_config.get("default_model", "glm-5")
    parts.extend(["--model", model or default_model])
    
    # Permission variants (pi has no permission system, but tool restriction maps)
    if session_type == "pi-zai-restricted":
        parts.extend(["--tools", "read,grep,find,bash"])
    elif session_type == "pi-zai-readonly":
        parts.extend(["--tools", "read,grep,find"])
    # "pi-zai" uses pi's default: read,bash,edit,write
    
    # Role-based flags
    temp_file = None
    if merged and session_type not in ("pi-zai-restricted", "pi-zai-readonly"):
        if merged.tools:
            # Translate Claude tool names to pi tool names (lowercase)
            pi_tools = [t.lower() for t in merged.tools if t.lower() in 
                        ("read", "bash", "edit", "write", "grep", "find", "ls")]
            if pi_tools:
                parts.extend(["--tools", ",".join(pi_tools)])
        
        # Note: pi has no --disallowedTools equivalent
        # Merged disallowed_tools intentionally ignored with warning in log
        
        if merged.instructions:
            f = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
            f.write(merged.instructions)
            f.close()
            temp_file = f.name
            parts.append(f'--append-system-prompt "$(<{temp_file})"')
    
    return AgentCommand(
        command=" ".join(parts),
        temp_file=temp_file,
    )
```

### 2. Update config schema

Add to `agentwire/config.py`:

```python
DEFAULT_CONFIG = {
    # ... existing ...
    "pi": {
        "default_model": "glm-5",
        "binary": "pi",  # override if installed elsewhere
    },
}
```

Add to example `config.yaml` in docs.

### 3. Update idle detection

`agentwire/completion.py` — `_session_has_agent()` currently returns False when pane shows a bare shell. Pi shows as `node` when running, so:

```python
AGENT_PROCESSES = {"claude", "node", "python"}  # Add node for pi
SHELL_PROCESSES = {"zsh", "bash", "sh", "fish", "tcsh", "csh"}

def _session_has_agent(session: str) -> bool:
    # ... existing tmux list-panes logic ...
    # Now checks if any pane runs an agent process (not shell)
```

Need to verify this doesn't break existing Claude Code detection — Claude Code also runs under node on some setups.

### 4. Update `.agentwire.yml` validation

`agentwire/project_config.py` — add to SessionType enum/Literal:

```python
SessionType = Literal[
    "claude-bypass", "claude-auto", "claude-prompted", "claude-restricted",
    "pi-zai", "pi-zai-restricted", "pi-zai-readonly",
    "bare",
]
```

### 5. Documentation

- New file: `docs/pi-zai.md` with setup, config, troubleshooting
- Update `CLAUDE.md` in project root: add pi-zai to session types table
- Update `README.md` if it mentions session types
- Update `agentwire --help` output (argparse choices)

### 6. Dev dependency: check pi is installed

On `agentwire doctor`, check `which pi` and report version. If missing:
```
Pi coding agent not installed. Install with: npm install -g @mariozechner/pi-coding-agent
```

## Files to Change

| File | Changes |
|------|---------|
| `agentwire/__main__.py` | Add `pi-zai*` branch to `build_agent_command()` (lines ~85-199) |
| `agentwire/__main__.py` | Update argparse `--type` choices to include pi-zai variants |
| `agentwire/config.py` | Add `pi` section to DEFAULT_CONFIG |
| `agentwire/project_config.py` | Extend SessionType Literal |
| `agentwire/completion.py` | Update idle detection for node/pi |
| `agentwire/agents/tmux.py` | Review `_format_agent_command()` for pi-specific flags (resume path) |
| `agentwire/doctor.py` | Add pi installation check |
| `docs/pi-zai.md` | New file: setup, examples, config, gotchas |
| `CLAUDE.md` | Add pi-zai to session types docs |
| `tests/test_build_agent_command.py` | New tests for pi-zai branch |
| `tests/test_project_config.py` | Update enum validation tests |

## Success Criteria

- [x] `agentwire new -s test --type pi-zai -p /tmp/test-project` creates a session with pi running
- [x] `.agentwire.yml` with `type: pi-zai` loads correctly, pi inherits roles/instructions
- [x] Sending prompt via `agentwire send -s test "..."` works (interactive)
- [x] Idle detection works (node while running, shell when exited — pre-existing logic handles this)
- [x] Role injection works: spawn with role, pi correctly identifies itself as a worker pane
- [x] `agentwire doctor` reports pi version when installed, clear error when missing
- [x] All existing Claude Code tests still pass
- [x] New tests for pi-zai session type pass (9 test cases added)
- [ ] At least one scheduler task running on `pi-zai` stably for 48h (deferred to follow-up)

## Testing Plan

### Unit Tests
```python
def test_build_agent_command_pi_zai_basic():
    cmd = build_agent_command("pi-zai").command
    assert "pi --provider zai" in cmd
    assert "ZAI_API_KEY=" in cmd

def test_build_agent_command_pi_zai_with_role():
    role = RoleConfig(name="worker", tools=["Read", "Bash"], instructions="Be concise")
    cmd = build_agent_command("pi-zai", [role])
    assert "--tools read,bash" in cmd.command
    assert "--append-system-prompt" in cmd.command

def test_build_agent_command_pi_zai_restricted():
    cmd = build_agent_command("pi-zai-restricted").command
    assert "--tools read,grep,find,bash" in cmd

def test_build_agent_command_pi_zai_readonly():
    cmd = build_agent_command("pi-zai-readonly").command
    assert "--tools read,grep,find" in cmd
    assert "edit" not in cmd and "write" not in cmd

def test_build_agent_command_pi_zai_model_override():
    cmd = build_agent_command("pi-zai", model="glm-5.1").command
    assert "--model glm-5.1" in cmd
```

### Integration Tests
- Spawn session, send message, read output, kill session
- Spawn with role, verify role is applied (grep for known string in output)
- Spawn with invalid API key, verify graceful error
- Spawn in /tmp dir with CLAUDE.md, verify pi loads it

### Manual QA
- Dev for a day using pi-zai session for a real task
- Voice summary test — does alert/notify work when pi session goes idle?
- Fork test — copy session JSONL, start new pi with `--continue`, verify context preserved

## Open Questions

- **Idle detection edge case:** If user runs `node` for something unrelated in the pane (not pi), will we falsely detect it as an agent? Answer: probably yes, but pane 0 in agentwire sessions is always the agent, so this is fine. Document limitation.
- **Binary location:** Pi is installed via nvm (`~/.nvm/versions/node/v20.19.4/bin/pi`). What if user has it elsewhere? Config `pi.binary` override should handle this.
- **`--disallowedTools` gap:** Some roles use disallowed tool lists. For pi-zai, we lose this. Log a warning when merged.disallowed_tools is non-empty for pi-zai sessions. Consider: translate to tool whitelist minus disallowed.
- **Session fork:** Claude Code has `--resume <id> --fork-session`. Pi has `--session <file> --continue`. Fork (create new session that starts from existing) needs a helper that copies the JSONL file first. Defer to a follow-up task or Phase 2.

## Rollout

1. Merge — session types are additive, no behavior change to existing sessions
2. Update one personal scheduler task (low stakes) to pi-zai
3. Monitor output/cost for 48h
4. Confirm CLAUDE.md marks pi-zai as the canonical Z.AI path

## Notes

Pi v0.66.1 is the tested baseline. Pin expectations to that version in docs until we know upgrade compatibility. Pi is pre-1.0 — breaking changes possible between minors.
