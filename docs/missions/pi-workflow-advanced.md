> Living document. Update this, don't create new versions.

# Mission: Phase 4 — Advanced Workflow Patterns

Extend the workflow engine only as real usage surfaces friction — parallel execution, loops, HITL gates, cost caps, etc. Deliberately **not** building ahead of demand.

**Phase of:** `pi-harness-overview.md`
**Status:** **on hold — awaiting real-usage trigger**
**Depends on:** Phase 2 (engine), Phase 3 (scheduler), Phase 6 (runner abstraction) — all complete
**Blocks:** nothing (Phase 5 can advance independently on its own scoped UI work)

## Where this mission stands (2026-04-21)

The original Phase 4 plan (written 2026-04) proposed parallel execution, fan-out/fan-in, loops with accumulators, human-in-the-loop gates, cost circuit breakers, cross-workflow calls, shared state, rollback hooks, and event-driven triggers. About 8 major new capabilities.

Since Phase 6 landed (2026-04-16 through 2026-04-21), 6 real workflows have been running in production:

- `daily-book-report` — 2 nodes, sequential
- `stargazing-forecast` — 1 node
- `ai-morning-briefing` × 3 variants — 1 node each
- `roblox-digest` — 1 node

**None of them would have benefited from any Phase 4 feature.** Real demand for:

- Parallel execution: **0** — workflows are 1-2 nodes, bottleneck is LLM latency, not DAG shape
- Fan-out / fan-in: **0**
- Loops: **0**
- Human-in-the-loop: **0** — these run autonomously to email
- Cost caps: **0** — subscription auth, nominal $0 actual billing
- Cross-workflow calls: **0**
- Shared state: **0**
- Event-driven triggers: **0** — scheduler cron is sufficient

Building any of the above now would be speculative infrastructure with no consumer. The original mission explicitly flagged this risk: *"premature sophistication = wrong abstractions."*

## What real usage *did* reveal as friction (and where each went)

| Friction | Addressed by |
|---|---|
| "I want to compare two runners/models for the same workflow" | `--runner` CLI override (Phase 6) + workflow-file duplication pattern (ai-morning-briefing × 3) |
| "Claude's built-in `WebSearch` is bad for research" | `agentwire brave` helper (Phase 6 closeout PR) |
| "I want to see past runs without digging through email" | Portal workflow history window (Phase 6 closeout PR) |
| "Email should go to multiple recipients" | Email `--to action=append` (Phase 6 closeout PR) |
| "I want action-only workflows with no notification" | `examples/silent-save.yaml` + "notification is prompt-level" doc pattern |

None required engine-level primitives. All shipped as tooling around the existing engine.

## Triggers that would reopen Phase 4

Each bullet below is a concrete condition that, if hit, unblocks the corresponding Phase 4 feature. Until then, stay on hold.

| Trigger | Opens |
|---|---|
| A workflow with 3+ independent nodes where wall-clock > 5 min matters | Parallel execution (`asyncio.gather` layer) |
| Need to run the same prompt across N inputs (e.g., "summarize each of these 20 URLs") | `for_each` fan-out |
| Iterative refinement task where the loop count is unknown at authoring time | `while`/`until` loop + accumulator state |
| A destructive workflow (e.g., "apply refactor to main branch") that needs review before commit | Human-in-the-loop gate via Slack/Discord bridge |
| A workflow hits Anthropic's rate limit or the $X/month soft-ceiling | Per-run cost cap + abort |
| We compose workflows where one workflow's output is another's input | `workflow_call` node type |

Original scoped-out design for each still lives in git history (commit of the original `pi-workflow-advanced.md`) — pull it back when a trigger fires, refine to the specific case.

## Design principles if/when work resumes

- **Additive only.** Every Phase 4 feature must be opt-in via new YAML fields. Existing workflows keep running unchanged.
- **Trigger-driven, not speculative.** Build the exact thing a concrete workflow demands, not the generalized abstraction.
- **One feature per release.** Parallel first if/when it lands; resist bundling.
- **Documentation before code.** Each feature gets a section in `docs/workflows.md` + a minimal example in `workflows/examples/` *before* it ships — if the example is contrived, the feature probably isn't needed.

## Open questions (relevant if a trigger fires)

- **Async refactor.** `runner.py` is synchronous. Parallelism requires `asyncio` subprocess for pi and/or deeper SDK integration for anthropic. Worth a focused prototype before committing.
- **State store scope.** If shared state becomes needed, sqlite at `~/.agentwire/workflows/state.db` is the likely shape — but defer until a use case names exactly what needs persistence.
- **Human-gate implementation.** Bridge (Discord/Slack) + marker file vs. dedicated gate service. Start with the bridge; refactor if noisy.
- **Cross-runner compatibility.** Any new node type must work under both pi and anthropic runners. The runner abstraction covers this — validation-at-parse-time can reject unsupported combinations.

## Not in scope (ever)

- Distributed execution across machines (separate mission if ever needed)
- Real-time collaborative workflow editing
- GUI-based visual authoring (YAML stays the source of truth; Phase 5 visualizes, doesn't author)

---

## Revisit checklist

Every ~30 days of production workflow usage, walk the Triggers table and count how many workflows would have benefited. If 2+ distinct workflows want the same primitive, that's the signal.
