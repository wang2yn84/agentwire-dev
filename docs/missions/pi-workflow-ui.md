> Living document. Update this, don't create new versions.

# Mission: Phase 5 — Workflow Desktop UI

Add visual workflow authoring / live run monitoring / replay to the agentwire portal. Significantly scoped down on 2026-04-21 after the Phase 6 closeout already shipped the list-view + detail-view + history features.

**Phase of:** `pi-harness-overview.md`
**Status:** **partially shipped — remainder on hold**
**Depends on:** Phase 2/3/6 (all complete)
**Blocks:** nothing

## What already shipped (during Phase 6 closeout)

The core "read + diagnose" UX from the original Phase 5 scope is live:

| Deliverable | Status | Where |
|---|---|---|
| Workflow list view in sidebar | ✅ shipped | `static/js/sidebar/workflows-section.js` — groups runs by workflow, runner badges, polls every 10s while expanded |
| Run detail window (winbox) | ✅ shipped | `static/js/windows/workflow-window.js` — metadata, per-node tool calls, tokens, final text, errors |
| Run history browsing | ✅ shipped | Same endpoints: `/api/workflows/runs`, `/api/workflows/runs/{id}` — `workflows.storage.list_runs` on disk |
| Cross-runner visibility | ✅ shipped | Runner column + colored badges (anthropic / pi / mixed) |
| Status dots, durations, costs, tokens | ✅ shipped | All surfaced in list + detail |

These were originally Phase 5 deliverables that got pulled forward into Phase 6 because real usage of the Anthropic runner demanded diagnostic surface before any advanced authoring.

## What's left from the original Phase 5 plan — and whether each is still wanted

| Original item | Reassessment (2026-04-21) | Verdict |
|---|---|---|
| **DAG canvas visualization** (dagre/ELK + SVG) | Our real workflows are 1-2 nodes. A canvas adds complexity for no readability gain on current shapes. Would earn its keep if Phase 4 lands and we get 5+ node graphs. | **Deferred — gated on Phase 4** |
| **Live node-state updates via WebSocket / SSE** | Originally scoped as the "Phase 6 SSE stretch." Closed by user decision during Phase 6: `agentwire workflow run -v` covers live observation in terminal; history window covers "what happened." No real demand to watch in-browser. | **Deferred — reopen only if friction appears** |
| **Replay mode** (scrub through past events) | Appealing in theory; in practice the detail window already shows full tool calls + final text at rest. Would matter for long multi-node workflows — we have none yet. | **Deferred — gated on Phase 4** |
| **YAML editor** (Monaco + schema validation in-portal) | Explicitly flagged in the original doc as "highest-risk / lowest-value." Users currently edit YAML in their terminal of choice; no complaints. | **Rejected — not worth the complexity** |

## What would reopen the remaining Phase 5 work

Same trigger pattern as Phase 4:

| Trigger | Opens |
|---|---|
| A real workflow with 5+ nodes and/or branching | DAG canvas visualization |
| User watches in-flight runs in the portal multiple times per week and repeatedly re-requests the page | Live WebSocket/SSE updates |
| A long-running workflow fails partway through and diagnosing from the already-persisted events feels inadequate | Replay scrubber |

Nothing in today's workload hits these. Revisit when it does.

## Success criteria (for the parts that already shipped)

- [x] Workflow list appears in sidebar with status indicators
- [x] Clicking a workflow row opens a detail window
- [x] Run detail shows metadata, per-node tool calls, tokens, cost, final text
- [x] Portal uses existing patterns (WinBox windows, sidebar accordion)
- [x] Scrollbar + sidebar layout polish (thin blended scrollbar, single-scroll nesting)
- [x] Runner badge styling per runner (anthropic/pi/mixed)
- [x] Integration tests for the 2 API endpoints (9 tests, all passing)

## Success criteria (for the deferred parts)

Defer stating these until a trigger fires — shipping the right success criteria requires knowing the specific pain point that opened the work.

## Lessons from Phase 5 partial-ship during Phase 6

- **The right UI surface emerged from actually running workflows, not from pre-planning.** The SSE live-stream spec in the original doc never shipped because email + `-v` already covered the live-watching use case.
- **Read-before-edit:** shipping the *view* of past runs was valuable immediately; the *edit* of workflow YAML never became a need.
- **Portal conventions carried the whole implementation.** Sidebar section pattern + WinBox window + REST endpoints on `server.py` — the existing portal scaffolding absorbed the new feature without new frameworks or dependencies.

## Out of scope (permanent)

- Drag-and-drop visual authoring — YAML stays the source of truth
- Collaborative editing
- Workflow marketplace / sharing
- Mobile view (portal is desktop-first)

---

## Revisit checklist

When Phase 4 surfaces a workflow with 4+ nodes, check the DAG-canvas trigger. When users start requesting a way to watch in-flight runs visually, check the live-updates trigger. Otherwise leave this mission dormant.
