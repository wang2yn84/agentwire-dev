> Living document. Update this, don't create new versions.

# Mission: Phase 5 — Workflow Desktop UI

Add visual workflow authoring, live run monitoring, and replay capability to the agentwire portal. Make workflows approachable for users who prefer GUIs over YAML, and make debugging failed runs drastically easier.

**Phase of:** `pi-harness-overview.md`
**Status:** planned
**Estimated effort:** 2–3 weeks
**Depends on:** Phase 2 (engine), Phase 3 (real runs to visualize), Phase 4 (parallel/loop patterns to render)
**Blocks:** none

## Goal

Ship a workflow canvas view in the portal where users can:
1. See all workflows in the system
2. Open a workflow and view its DAG visually
3. Watch a workflow run live: nodes light up as they execute
4. Replay past runs from the event log
5. Edit workflow YAML with syntax help (not full visual authoring — that's overkill)

## Why This Is Phase 5

YAML-first is correct. Users who author workflows are devs who prefer text. But **debugging** is where GUI shines — staring at a JSONL event log is awful; a visual DAG with per-node status + drill-down is transformative.

This phase ships when there are enough workflows in real use that the UX friction from pure-CLI debugging becomes a real pain point.

## Scope

### In Scope

- **Workflow list view** in sidebar (like existing sessions/services sections)
- **Workflow canvas view** — opened as a desktop window (WinBox)
  - DAG visualization with nodes (rectangles) and edges (dependencies)
  - Node color by status: pending (gray), running (blue pulse), success (green), failure (red), skipped (dashed)
  - Click node → detail pane with prompt, outputs, events
- **Live run monitor** — subscribes to WebSocket events, updates node states in real time
- **Run history** — list of past runs per workflow with status/duration/cost
- **Replay mode** — step through events in a past run, node states evolve as if live
- **YAML editor** — Monaco editor inline in the portal with JSON schema validation for workflow files

### Out of Scope

- Drag-and-drop visual authoring (overkill — YAML is fine)
- Collaborative editing
- Workflow marketplace / sharing (far future)
- Mobile view (portal is desktop-first)

## Approach

### 1. Backend: WebSocket Workflow Events

Extend `agentwire/server.py`:

```python
# New WebSocket endpoint: /ws/workflow-events
# Clients subscribe to run IDs, receive event stream

# On workflow runner event, broadcast to subscribers:
{
  "type": "node_start",
  "run_id": "abc-123",
  "node_id": "analyze",
  "timestamp": "..."
}
```

Runner emits these via a channel the server listens on (asyncio queue or file watch on events.jsonl).

### 2. New REST endpoints

```
GET  /api/workflows                      # List all workflow definitions
GET  /api/workflows/:name                # Load definition (YAML + parsed)
GET  /api/workflows/:name/runs           # Run history for a workflow
GET  /api/workflow-runs/:id              # Run metadata + context
GET  /api/workflow-runs/:id/events       # Full event log (JSONL)
GET  /api/workflow-runs/:id/nodes/:nid   # Single node detail
POST /api/workflows/:name/run            # Trigger a run (returns run_id)
POST /api/workflows/:name                # Save definition (YAML)
```

### 3. Frontend: Workflow Sidebar Section

`static/js/sidebar/workflows-section.js`:
- Lists workflows (names from `/api/workflows`)
- Each item shows latest run status indicator
- Click: opens canvas window

### 4. Frontend: Canvas Window

`static/js/windows/workflow-canvas.js`:
- Renders with a lightweight DAG library (e.g., dagre for layout + vanilla SVG for rendering — avoid heavy deps like react-flow)
- Responsive to workflow complexity (grid layout for small, force-directed for large)
- Event subscription: connects WebSocket, updates node statuses
- Detail pane: right side of canvas, shows selected node info

### 5. Run Detail Drill-Down

Click node during/after a run:
- Left column: prompt template + rendered prompt
- Middle column: pi event log (timeline of tool calls, thinking, response)
- Right column: outputs, cost, duration

Similar UX to browser devtools: collapsible, searchable, resizable.

### 6. YAML Editor

For workflow authoring:
- Monaco editor (already used in the portal for session config?)
- JSON schema validation with inline errors
- Autocomplete for node fields
- "Save" button persists to `~/.agentwire/workflows/defs/<name>.yaml`
- "Validate" button calls `agentwire workflow validate <path>`

### 7. Replay Mode

For debugging failed runs:
- Scrubber bar at the bottom of the canvas
- As you scrub, node states evolve to match that point in the event log
- Pause/play/step controls
- Detail pane shows state at that scrubbed moment

## Files to Change

### Backend
| File | Changes |
|------|---------|
| `agentwire/server.py` | New REST endpoints for workflows |
| `agentwire/server.py` | WebSocket endpoint `/ws/workflow-events` |
| `agentwire/workflows/runner.py` | Emit events to server queue as they occur |
| `agentwire/workflows/storage.py` | Add API for querying run history |

### Frontend
| File | Changes |
|------|---------|
| `static/js/sidebar/workflows-section.js` | New sidebar section |
| `static/js/windows/workflow-canvas.js` | New canvas window component |
| `static/js/workflow-events.js` | WebSocket subscriber for live events |
| `static/js/workflow-yaml-editor.js` | Monaco-based editor |
| `static/css/workflow.css` | Styles for canvas, node states, detail pane |

### Dependencies
- `dagre` or `elkjs` for DAG layout (npm — included via esbuild or CDN)
- Monaco editor (likely already available)

## Success Criteria

- [ ] Workflow list appears in sidebar with status indicators
- [ ] Clicking a workflow opens a canvas window showing the DAG
- [ ] Running a workflow live updates node colors in real time
- [ ] Clicking a node shows prompt, events, outputs
- [ ] Replay mode scrubs through a completed run accurately
- [ ] YAML editor validates on save, surfaces errors inline
- [ ] Workflow UI uses same patterns as existing portal (WinBox windows, sidebar accordion)
- [ ] Performance: canvas handles workflows with 20+ nodes without lag

## Testing Plan

### Manual
- Open a simple 3-node workflow, run it, watch live updates
- Open a 10-node workflow with branching, verify layout is readable
- Load a completed run, replay it, verify state matches expectation
- Edit a workflow YAML, introduce a syntax error, verify editor catches it
- Run 50+ workflows, verify history list stays performant

### Automated
- Puppeteer / Playwright: render workflow, simulate WebSocket events, assert node colors update
- API contract tests for all new endpoints

## Open Questions

- **DAG layout engine:** dagre is simpler and smaller; ELK is more sophisticated but heavier. Start with dagre.
- **Authoring depth:** Do we want a true "click to add node" mode, or keep authoring in YAML only? Keep YAML-first.
- **Multi-run comparison:** Can users diff two runs of the same workflow side-by-side? Nice-to-have, defer.
- **Canvas interactivity:** Zoom, pan, fit-to-screen standard. Minimap? Only if workflows grow large.
- **Permission model:** Anyone with portal access can trigger workflows? Same as current session trigger permissions — no new model.

## Risk Mitigation

- **Rendering performance:** Large DAGs (50+ nodes) may lag. Mitigation: virtualize offscreen nodes, use SVG not canvas.
- **Event flood:** High-frequency events from pi (tool calls, thinking chunks) could overwhelm WebSocket. Mitigation: server-side throttling, client-side batching.
- **Frontend complexity creep:** A workflow canvas wants to become an IDE. Resist feature creep. Ship "read + replay" first, add "write" incrementally.

## Prior Art Reference

- **n8n canvas** — nice node-and-edge UI, but too GUI-centric for our authoring ethos
- **Airflow DAG view** — good for monitoring, weak for auth
- **GitHub Actions run view** — inspiration for per-node event drill-down
- **Temporal Web UI** — good example of replay from event log
- **Langsmith** — LLM-specific observability, good UX for prompt/output inspection

Borrow UX patterns, avoid their complexity.

## Rollout

1. Ship the sidebar section + list view (stub detail for now)
2. Ship the canvas window with static layout (no live updates yet)
3. Add WebSocket live updates
4. Add run history + replay
5. Add YAML editor last — it's the highest-risk / lowest-value piece of UX and can wait until real demand

## Notes

If Phases 3–4 data shows workflow adoption is strong, this phase is high-value — it 10x's debuggability and approachability. If adoption is slow, this phase may not be worth building; invest in better CLI output instead.

Decide whether to proceed with Phase 5 after Phase 3 has been live for 60+ days.
