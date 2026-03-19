> Living document. Update this, don't create new versions.

# Mission: Leapfrog Agents

## Status: Prototype Ready

## Summary

A rolling session workflow where each session pre-warms for the next phase while the current phase executes, then self-perpetuates by spawning its own successor. Human role reduces to: kick off the first session, then review + activate at each phase boundary.

## Three Commands

| Command | When | What |
|---------|------|------|
| `/leapfrog-prime <phase> [notes]` | On session start | Load mission files, read source, build plan, go idle |
| `/leapfrog-scout <next-phase> [notes]` | Before/at start of execution | Spawn successor, send it prime, write `.leapfrog` state file |
| `/leapfrog-prune [successor] [notes]` | After phase committed | Write handoff, activate successor with replan message, exit |

## The Pattern

Each session has two data points before executing:

1. **Data point 1 (speculative)** — spawned in parallel with prior execution. Assumes the current phase is complete as designed. Plans, loads files, goes idle when ready.
2. **Data point 2 (actual)** — the "begin" message from the prior session's prune. Contains the real sha + handoff file. Session calibrates plan against reality, then executes.

```
[lf1]                            [lf2]                          [lf3]
 │                                │                               │
 ├─ /leapfrog-prime phase 2       │                               │
 │   load, plan, idle             │                               │
 │                                │                               │
 │◄── "phase 1 done, begin" ──────┤                               │
 │                                │                               │
 ├─ revise plan                   │                               │
 ├─ /leapfrog-scout phase 3 ──────►/leapfrog-prime phase 3        │
 ├─ EXECUTE phase 2               │   load, plan, idle            │
 │                                │                               │
 ├─ /leapfrog-prune ─────────────►│ "phase 2 done, begin"         │
 └─ exit                         ├─ revise plan                  │
                                  ├─ /leapfrog-scout phase 4 ─────►/leapfrog-prime phase 4
                                  ├─ EXECUTE phase 3              │   load, plan, idle
                                  │                               │
                                  ├─ /leapfrog-prune ────────────►│ "phase 3 done, begin"
                                  └─ exit                        └─ ...
```

## Why Two Data Points Matter

Standard cold start: one pass — what you tell it to read right now.

Leapfrog: two passes:
- **Speculative** — what should be true when this phase begins
- **Actual** — what is true, including surprises, deferred items, new patterns

The second pass calibrates the plan to reality. The session processes the delta between plan and execution *before* writing a line of code.

## Usage

**One-time setup:**
```bash
# Optional: add docs/leapfrog.md to project for custom pre-warm instructions
```

**Kick off (human does once per project):**
```bash
agentwire leapfrog myproject-lf1 -p ~/projects/myproject
# Inside the session:
/leapfrog-prime phase 2
```

**Session goes idle → you're notified "ready and waiting"**

**Activate (after prior phase is committed):**
```
"phase 1 committed at abc123, begin"
```

**Session runs `/leapfrog-scout phase 3`, executes, then `/leapfrog-prune` — lf2 activates, lf1 exits.**

**You only need to:**
1. Review completed work
2. Commit
3. Tell the waiting session to begin
4. Repeat

## Artifacts

| File | Written by | Read by |
|------|-----------|---------|
| `.leapfrog` | `/leapfrog-scout` | `/leapfrog-prune` |
| `docs/leapfrog-handoff.md` | `/leapfrog-prune` | Next session during replan |
| `docs/leapfrog.md` | Human (optional) | `/leapfrog-prime` for custom instructions |

## Implemented

- `agentwire leapfrog <session> [-p path]` — CLI command, wraps `new` with leapfrog role injected (session name is positional)
- `agentwire/roles/leapfrog.md` — role that explains the full three-command protocol
- `~/.claude/commands/leapfrog-prime.md` — global skill
- `~/.claude/commands/leapfrog-scout.md` — global skill
- `~/.claude/commands/leapfrog-prune.md` — global skill

## Future

- `ready` session state in dashboard ("⚡ Phase 3 ready") distinct from idle
- `agentwire leapfrog handoff` CLI shortcut for the prune step from outside a session
- Gate: don't let scout/prune run if prime hasn't been called first
