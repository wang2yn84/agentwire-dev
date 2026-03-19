> Living document. Update this, don't create new versions.

# Mission: Leapfrog Agents

## Status: Later

## Summary

A rolling session workflow where each session pre-warms for the next phase while the current phase executes, then self-perpetuates by spawning its own successor. Human role reduces to review + approve at phase boundaries — no context-switching, no re-explaining state.

## The Pattern

Each session has two data points before executing:

1. **Data point 1 (speculative)** — spawned in parallel with the prior phase's execution. Reviews mission files and assumes the current phase is complete as designed. Enters plan mode, loads relevant files, resolves ambiguities. Goes idle when "ready."
2. **Data point 2 (actual)** — after human approves the prior phase, receives the real commit sha + handoff notes. Calibrates plan against reality. Spawns its own successor (giving it data point 1), then begins executing.

```
[Session N]
  │
  ├─ PRE-WARM (data point 1)
  │    Assume phase N-1 done. Review phase N plan. Load files.
  │    Enter plan mode. Resolve blockers. → idle = "ready"
  │
  ├─ SPAWN Session N+1 + send pre-warm prompt (parallel with execution)
  │
  ├─ EXECUTE phase N → notify human
  │
  └─ Human approves
       → send Session N+1 data point 2: actual sha + handoff notes
       → Session N closes
       → Session N+1 confirms plan, spawns Session N+2, begins executing
```

## Why Two Data Points Matter

A cold-start session has one pass at understanding: what you tell it to read right now.

A leapfrog session has two:
- **Speculative** — what should be true when this phase begins
- **Actual** — what is true, including surprises, deferred items, new patterns introduced

The second pass calibrates the plan to reality. The session isn't just correct-in-theory — it's already processed the delta between plan and execution before writing a single line of code.

## New Concepts Required

### Session States

Agentwire currently treats idle as "not busy." Leapfrog needs a `ready` state — a session that's fully pre-warmed and waiting for a go signal. Dashboard should show this distinctly (e.g., "⚡ Phase 3 ready").

### Handoff Notes

When an exec session finishes, it writes a brief structured file before going idle:

```
~/.agentwire/handoffs/{session-name}.md
- What was completed
- What was deferred to next phase
- Decisions that affect the upcoming plan
- New files, patterns, or interfaces introduced
```

The successor reads this as its data point 2.

### Pre-warm and Data Point 2 Prompt Templates

Defined per-project in `.agentwire.yml`:

```yaml
leapfrog:
  phase_file: docs/missions/phase-tracker.md

  pre_warm_prompt: |
    You are being pre-warmed for phase {next_phase}.
    Review {phase_file} and assume phase {current_phase} is complete as designed.
    Load all relevant files, enter plan mode, identify blockers, reach "ready" state.
    When ready, go idle — you'll receive actual results before executing.

  data_point_2_prompt: |
    Phase {current_phase} is committed at {sha}.
    Handoff notes: {handoff_notes}
    Confirm your plan is still correct, adjust if needed, then begin executing.
    Before executing: spawn the pre-warm session for phase {next_phase}.
```

### Phase Tracker File

Shared state both sessions can read. Single source of truth for where things stand.

```markdown
# Phase Tracker

## Current: Phase 2
- Status: executing
- Branch: feature/phase-2
- Started: 2026-03-18

## Next: Phase 3
- Status: pre-warming
- Plan ready: false

## Completed
- Phase 1: abc1234 (2026-03-17)
```

## Prototype (No New Code Required)

The prototype uses only existing primitives. The session does the heavy lifting if given good enough instructions — human only touches it at kick-off and approval.

### What's Needed

1. **Prompt templates** — two templates per project, stored in `docs/leapfrog.md` (or inline in `.agentwire.yml`)
2. **A `leapfrog` role** — bakes the protocol into the session: spawn successor, write handoff, notify human

### Prototype Steps (One Test Run)

**Setup (once per project):**
```bash
# Create docs/leapfrog.md in your project with the two prompt templates
# Add leapfrog role to .agentwire.yml:
#   roles: [agentwire, voice, leapfrog]
```

**Kick off (human does once):**
```bash
agentwire new -s myproject-lf1 -p ~/projects/myproject
# Send pre-warm prompt:
agentwire send -s myproject-lf1 "$(cat docs/leapfrog.md | section pre_warm) — pre-warm for phase 2"
```

**Session runs autonomously:**
- Loads files, enters plan mode, goes idle → you're notified "ready"
- You approve previous phase work (commit, push)
- Send data point 2:
```bash
agentwire send -s myproject-lf1 "Phase 1 committed at $(git rev-parse --short HEAD). [brief notes]. Begin executing. Spawn myproject-lf2 with the pre-warm prompt for phase 3."
```
- Session spawns `myproject-lf2` via MCP `session_create` + `session_send`, then executes
- Notifies you when done

**Approve and roll:**
```bash
# Review work, commit
git add . && git commit -m "..."
# Tell lf1 to hand off and close:
agentwire send -s myproject-lf1 "Approved. Write handoff notes to docs/leapfrog-handoff.md and close."
# lf2 is already pre-warmed — send it data point 2:
agentwire send -s myproject-lf2 "Phase 2 committed at $(git rev-parse --short HEAD). See docs/leapfrog-handoff.md. Begin."
```

### Leapfrog Role (to build)

`agentwire/roles/leapfrog.md` — instructs the session to:
- On pre-warm: read mission files, enter plan mode, go idle when ready
- On data point 2: read handoff file, confirm/adjust plan, spawn successor with pre-warm, execute
- On execution complete: write `docs/leapfrog-handoff.md`, alert human via `say`

### Prompt Templates (per project, `docs/leapfrog.md`)

```markdown
## pre_warm
You are being pre-warmed for {phase}.
Assume the prior phase is complete as designed.
Read all mission files in docs/missions/. Load relevant source files.
Enter plan mode. Identify any gaps or blockers.
When you have a solid plan and feel ready, go idle.
You will receive actual results from the completed phase before you execute.

## data_point_2
The prior phase is committed at {sha}.
Handoff notes: see docs/leapfrog-handoff.md
Review the actual changes vs your plan. Adjust if needed.
Before executing: spawn session {next_session} and send it the pre_warm prompt for {next_phase}.
Then begin executing your phase.
When done: write docs/leapfrog-handoff.md summarizing what was done, what was deferred, and any decisions that affect the next phase. Then say "Phase {phase} complete — ready for review."
```

## CLI Interface (Future)

```bash
# Kick off leapfrog for a project
agentwire leapfrog --project myproject --phase 2

# When phase N execution completes, hand off to the pre-warmed successor
agentwire leapfrog handoff --from myproject-2 --to myproject-3 --sha abc123
```

## State Machine

```
spawned → pre-warming → ready → [waiting for go]
                                       │
                              human approval + data point 2
                                       │
                              confirming → spawns successor → executing → done
                                                                           │
                                                               notify human
```

## Dependencies

- `session_create` + `session_send` MCP tools — sessions can already spawn and message successors
- Idle notifications — already wired for human approval gate
- Future: `leapfrog` role, `ready` session state in dashboard, `leapfrog` CLI commands
