> Living document. Update this, don't create new versions.

# Mission: Leapfrog Agents

**Status:** Retired (code removed, strategy pattern documented)

## What It Was

A rolling session workflow where each session pre-warms for the next phase while the current phase executes. Three skills (`/leapfrog-prime`, `/leapfrog-scout`, `/leapfrog-prune`) managed the handoff chain.

## Outcome

Useful as a **strategy pattern for large initial project setup** (scaffolding many files across phases), but not worth maintaining as code. The overhead of the three-command protocol didn't justify itself for ongoing development work.

## The Strategy Pattern (Still Useful)

When doing a massive initial setup (e.g., scaffolding a new project with many interconnected files), the leapfrog approach works well:

1. **Phase N session** works on current phase
2. While working, spawn **Phase N+1 session** with context about what's planned
3. Phase N+1 pre-loads files, builds a plan, goes idle
4. When Phase N commits, activate Phase N+1 with "begin" + handoff notes
5. Phase N+1 revises plan against reality, then executes

The key insight is **two data points**: speculative (what should be true) and actual (what is true after prior phase). The delta between plan and reality is processed before writing code.

```
[Session 1]                    [Session 2]                    [Session 3]
 ├─ pre-warm with plan          │                               │
 ├─ receive "begin" + handoff   │                               │
 ├─ revise plan vs reality      │                               │
 ├─ spawn Session 2 ───────────► pre-warm                       │
 ├─ EXECUTE phase 1             │ idle, waiting                 │
 ├─ handoff to Session 2 ──────► receive "begin"                │
 └─ exit                        ├─ revise plan                  │
                                ├─ spawn Session 3 ─────────────► pre-warm
                                ├─ EXECUTE phase 2              │
                                └─ handoff ─────────────────────► ...
```

**When to use this manually:** Giant project scaffolding, multi-phase migrations, or any work where each phase produces context the next phase needs. Use `agentwire fork` to carry conversation context between phases.

## Code Removed

- `agentwire leapfrog` CLI command
- `agentwire/roles/leapfrog.md`
- `agentwire/commands/leapfrog-prime.md`
- `agentwire/commands/leapfrog-scout.md`
- `agentwire/commands/leapfrog-prune.md`
