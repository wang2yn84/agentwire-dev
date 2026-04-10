> Living document. Update this, don't create new versions.

# Mission: Portal Sidebar Redesign

Replace the bottom taskbar + top menu bar + windowed list panels (Sessions, Machines, Projects, Artifacts, Scheduler, Config) with a single auto-hiding **left sidebar** containing accordion sections. Reclaim vertical desktop real estate, simplify the mental model ("the sidebar IS the workspace navigator"), and remove a class of UX friction (find/restore the Sessions window to open a session).

## Why

- **Vertical space** — current top menu bar (28px) + bottom taskbar (40px) + per-window titlebar adds up.
- **Mental model** — list panels are navigators, not workspaces. They shouldn't behave like draggable WinBox windows.
- **Friction** — current flow to open a session: open Sessions window → click session. Future flow: hover sidebar → expand Sessions accordion → click session. One less click *and* the navigator is always one hover away.
- **Restore-on-refresh complexity** — fewer "panel" record kinds in the saved state schema. Sidebar accordions are a UI affordance, not persisted window state.

## Constraints

- **No floating buttons.** Everything lives in the sidebar or in window titlebars.
- **Window header bars stay** for session/artifact windows so minimize/close controls remain accessible.
- **No regression in voice / activity / connection / clock affordances.**
- **Pre-launch** — no backwards compatibility shims. Old `taskbar-state` localStorage values can be migrated in-place or wiped on first load.

## Inventory (current state)

### Top menu bar — `agentwire/templates/desktop.html:12-44`
- `.menu-bar` container
  - `.menu-left`: `#machinesMenu`, `#projectsMenu`, `#sessionsMenu`, `#artifactsMenu`, `#schedulerMenu` (lines 14-18)
  - `.menu-right`: `#connectionStatus` (21-24), `#sessionCount` (25-27), `#menuTime` clock (28), `#settingsMenu` gear (29-42) with Config / Reset Windows dropdown items

### Bottom taskbar — `desktop.html:59-68`
- `.taskbar > #taskbarWindows` (taskbar buttons)
- `.taskbar-tray > #globalPtt` (🎤 button) + `#voiceIndicator` (idle/processing/generating/playing states)

### Taskbar JS — `agentwire/static/js/desktop.js`
- `addTaskbarButton`, `removeTaskbarButton`, `updateTaskbarActive`, `addPlaceholderTaskbarButton`, `materializePlaceholder`, `_lookupWindowInstance`, `recordTaskbarEntry`, `unrecordTaskbarEntry`, `loadTaskbarState`, `saveTaskbarState`, `restoreTaskbarState`, `bindTaskbarDragover` (~lines 537-761)
- localStorage key: `taskbar-state` → `{ tabs: [{kind, id, ...args, minimized}], activeId }`

### Per-session push-to-talk — `agentwire/static/js/session-window.js`
- Markup at lines 268-270 (only in terminal mode), CSS `.ptt-button` at `desktop.css:2027-2094` (absolute, bottom 40px right 12px, 44px circle)
- Setup: `_setupPTT()` (~1069), `_startRecording()` (~1140), `_stopRecording()` (~1175), `_setPTTState()` (~1232)
- State: `this.pttButton`, `this.mediaRecorder`, `this.audioChunks`, `this.pttState`

### Activity indicator in WinBox titlebar — `session-window.js:1255-1312`
- `_setupActivityIndicator()` finds `.wb-title` and appends `<div class="session-activity-indicator">` directly. No formal `addControl()` API — direct DOM append.

### List window classes (to migrate)
| Window | File | LOC | Complexity | Notes |
|--------|------|-----|-----------|-------|
| Sessions | `windows/sessions-window.js` | 411 | MEDIUM | Activity indicators, icon picker, hierarchy, WebSocket updates |
| Machines | `windows/machines-window.js` | 437 | MEDIUM | Status polling, machine grouping, session creation UI |
| Projects | `windows/projects-window.js` | 1341 | **HIGH** | Drill-down detail view, log viewer, multi-machine grouping |
| Artifacts | `windows/artifacts-window.js` | 90 | LOW | Simple file list |
| Config | `windows/config-window.js` | 102 | LOW | Read-only display |
| Scheduler | `windows/scheduler-window.js` | 1105 | **VERY HIGH** | Custom 3-tab UI (Queue/History/Tasks), WebSocket push, drill-down |

All except Scheduler extend `ListWindow` (`list-window.js`).

### Voice indicator — `desktop.js:359-386`
- States: idle / processing / generating / playing
- Triggered by `tts_start`, `audio`, `audio_ended`, `session_processing`, `session_activity` events on `desktop` event bus

### CSS variables — `desktop.css:3-34`
- `--menu-height: 28px`, `--taskbar-height: 40px`, `--spacing-base: 8px`
- `--background`, `--accent` (green #4ade80), `--chrome` (#1a1a1a), `--chrome-border`, `--hover`, `--text`, `--text-muted`, `--error`
- Orb colors for voice states

### localStorage keys
- `taskbar-state` (this mission migrates/replaces)
- `agentwire_window_<id>` (window positions/sizes — preserved)
- `agentwire_icons_sessions|machines|projects` (icon assignments — preserved)

---

## Phase 1 — Sidebar shell + utility chrome migration

**Goal:** Build the auto-hide sidebar and move clock + global PTT + voice indicator into it. Move per-session PTT into the WinBox titlebar. Bottom bar and top menu bar **remain functional** for now — this phase swaps real estate but breaks nothing.

### Deliverables

#### Markup — `agentwire/templates/desktop.html`
- Add `.sidebar-hotzone` (6px wide, fixed left, 100vh, z-index above desktop area)
- Add `.sidebar` container (fixed left, 280px wide, slides in via transform, z-index above hotzone)
  - `.sidebar-header` — clock + pin toggle
  - `.sidebar-section` placeholders for Phase 2/3 accordions (start with one placeholder for "Open Windows")
  - `.sidebar-footer` — global PTT button + voice indicator
- Hotzone and sidebar both append to `.desktop-container` (siblings of `.menu-bar`/`.desktop-area`/`.taskbar`)

#### CSS — `agentwire/static/css/desktop.css`
- New section for sidebar styles
- Variables: `--sidebar-width: 280px`, `--sidebar-hotzone: 6px`, `--sidebar-transition: 180ms ease`
- `.sidebar-hotzone` — fixed left, full height, 6px, transparent, captures mouseenter
- `.sidebar` — fixed left, full height, 280px, transformed off-screen by default (`translateX(calc(-1 * var(--sidebar-width)))`), `transition: transform var(--sidebar-transition)`
- `.sidebar.open` — `transform: translateX(0)`
- `.sidebar.pinned` — same effect, plus body class adjusts `.desktop-area` to `margin-left: var(--sidebar-width)` so content reflows instead of being covered
- Background `--chrome`, border-right `--chrome-border`, internal padding `var(--spacing-base)`
- Sidebar sections use accordion styling (collapsible header + expandable body)
- Sidebar footer: pinned to bottom of sidebar, holds PTT + voice indicator + (eventually) per-section status
- Move existing `.ptt-button-titlebar` styles for the new in-titlebar per-session PTT (small, inline, ~24×24, no animations stripped)

#### JS — new file `agentwire/static/js/sidebar.js`
- `export const sidebar` — small singleton
  - `sidebar.init()` — wires hotzone hover-in, sidebar mouseleave, ESC key, pin toggle
  - `sidebar.open() / close() / toggle() / pin() / unpin()`
  - Tracks state in localStorage key `sidebar-pinned`
  - Mouse-enter hotzone → open. Mouse-leave sidebar → close (unless pinned). ESC closes (unless pinned).
- Builds the sidebar's persistent footer affordances by calling existing setup helpers (clock, global PTT, voice indicator) but pointing them at the new DOM IDs
- Exports nothing else — Phase 2/3 add accordion section APIs

#### JS — `agentwire/static/js/desktop.js`
- Import and call `sidebar.init()` early in `init()`
- Move clock element ID references — `setupClock()` now updates `#sidebarClock` instead of `#menuTime`
- Move global PTT setup — `setupGlobalPtt()` binds to `#sidebarGlobalPtt` instead of `#globalPtt`
- Move `updateVoiceIndicator()` to target `#sidebarVoiceIndicator`
- Delete the now-orphaned elements from the menu bar / taskbar tray markup
- **Keep** the bottom taskbar + top menu nav links functional in this phase

#### JS — `agentwire/static/js/session-window.js`
- Move PTT button markup out of the terminal-mode container template into the WinBox titlebar (similar to `_setupActivityIndicator()` pattern — find `.wb-title` and prepend an icon button)
- Strip the floating-button positioning from CSS for the in-titlebar variant
- `_setupPTT()` now finds the button via the WinBox titlebar reference instead of the container
- All recording state machinery (`_startRecording`, `_stopRecording`, `_setPTTState`) unchanged
- Activity indicator logic untouched (also lives in titlebar, side-by-side)

### Acceptance criteria — Phase 1 — DONE
- [x] Hovering the left ~30px of the desktop slides the sidebar in within ~180ms (tuned from 6→18→30 during testing)
- [x] Mouse leaving the sidebar slides it out (unless pinned)
- [x] Pin toggle persists across refresh; pinned state reflows desktop area instead of overlaying
- [x] Clock shows correct time inside sidebar
- [x] Global PTT button works from sidebar (mousedown to record, mouseup to stop, Cmd+Space global hotkey still works)
- [x] Voice indicator transitions through idle/processing/generating/playing inside the sidebar
- [x] Per-session PTT button now appears in the SessionWindow titlebar (not floating); recording works identically
- [x] Bottom taskbar and top menu bar still function (Phase 2/3 will remove them)
- [x] No console errors on load

---

## Phase 2 — "Open Windows" accordion + delete bottom taskbar

**Goal:** Move the bottom-bar tab functionality into a sidebar accordion section. Once it works, **delete the bottom taskbar entirely**.

### Deliverables

#### JS — `agentwire/static/js/sidebar.js`
- Add `addOpenWindowEntry(id, windowInstance)`, `removeOpenWindowEntry(id)`, `updateActiveOpenWindow(id)`, `addPlaceholderOpenWindowEntry(rec)`, `materializeOpenPlaceholder(btn, rec)` — sibling functions to the deleted taskbar versions, but rendering vertical list items in the "Open" section instead of horizontal tabs
- Drag-to-reorder reused via the existing HTML5 DnD pattern, vertical instead of horizontal (insert-before/after based on midpoint of rect.height instead of width)
- Each row: window kind icon + title + active indicator + small `×` close button
- Click → focus/restore. Click `×` → close window.
- "Open" section is always expanded by default and lives at the top of the accordion stack, above all other sections

#### JS — `agentwire/static/js/desktop.js`
- Replace all references to `addTaskbarButton`/`removeTaskbarButton`/`updateTaskbarActive`/`addPlaceholderTaskbarButton`/`materializePlaceholder` with the sidebar equivalents
- Delete `bindTaskbarDragover`, `addTaskbarButton`, `removeTaskbarButton`, `updateTaskbarActive`, `addPlaceholderTaskbarButton`, `materializePlaceholder`
- `restoreTaskbarState` keeps its name and schema but populates the sidebar Open section instead of the taskbar
- Keep `taskbarRecords` Map and `localStorage['taskbar-state']` schema unchanged for now (rename in a follow-up; bigger blast radius)

#### Markup — `agentwire/templates/desktop.html`
- **Delete** `.taskbar` and everything inside it (`#taskbarWindows`, `.taskbar-tray`, `#globalPtt`, `#voiceIndicator` — those moved to the sidebar in Phase 1)

#### CSS — `agentwire/static/css/desktop.css`
- Delete `.taskbar`, `.taskbar-windows`, `.taskbar-btn`, `.taskbar-tray` rules
- Add `.sidebar-open-list`, `.sidebar-open-item`, `.sidebar-open-item.active`, `.sidebar-open-item.minimized`, `.sidebar-open-item.dragging`, `.sidebar-open-item-close`
- Remove `--taskbar-height` variable (or repurpose if needed)
- Adjust `.desktop-area` to use full viewport height minus only the top menu bar

### Acceptance criteria — Phase 2
- [ ] Bottom bar gone; desktop area extends to bottom of viewport
- [ ] Open windows appear as items in the sidebar's "Open" section in the order they were opened
- [ ] Clicking an item focuses or restores its window
- [ ] Clicking `×` closes the window
- [ ] Drag-to-reorder works vertically; order persists across refresh
- [ ] Active window highlighted in the list
- [ ] Refresh restores the same set of open windows + active state (no regression from current behavior)
- [ ] Lazy placeholder logic still works (only the active window is constructed on refresh)

---

## Phase 3 — Migrate list panels into sidebar accordions; delete WinBox versions

**Goal:** Each of the six list panels becomes an accordion section in the sidebar. The WinBox-based versions are deleted entirely. Top menu bar nav links and `desktop_open_panel` MCP tool are wired to expand/scroll the matching section instead of opening a window.

### Strategy

Migrate one section at a time, in order of complexity (simplest first to validate the pattern):
1. **Config** (~102 LOC, read-only) — proves the pattern
2. **Artifacts** (~90 LOC) — adds delete/open actions
3. **Machines** (~437 LOC) — adds polling + status indicators
4. **Sessions** (~411 LOC) — adds activity indicators, icon picker, real-time updates
5. **Projects** (~1341 LOC) — adds drill-down detail view (may need a sub-pane that pushes the accordion content aside)
6. **Scheduler** (~1105 LOC) — custom 3-tab UI; may stay as a window in Phase 3 and migrate in a follow-up if needed

### Deliverables (per panel)

For each panel:
- Add a new `agentwire/static/js/sidebar/<panel>-section.js` that exports `mountSection(container)` and `unmountSection(container)`
- Move the data fetching, rendering, and event handling from the existing `windows/<panel>-window.js` into the section module, adapted to the sidebar's narrow width and vertical layout
- Update the sidebar in `sidebar.js` to register the new section's accordion entry
- Delete the old `windows/<panel>-window.js`
- Delete the corresponding `openXxxWindow` function and import in `desktop.js`
- Remove the panel from the `panelMap` in `restoreTaskbarState` (or migrate any persisted `panel`-kind records to a no-op)

### Wire up entry points

- Top menu bar: `#machinesMenu` etc. now call `sidebar.expand('machines')` (which opens the sidebar pinned, scrolls to the section, and expands it)
- MCP `desktop_open_panel` server-side handler still emits `desktop_open_window` events with `window_type: 'panel'`. Frontend handler in `desktop.js` translates that to `sidebar.expand(panel_name)` instead of opening a WinBox.
- Top menu bar may eventually be removed entirely once everything routes through the sidebar — but keep it in Phase 3 as a discoverability aid. Removing it can be a follow-up issue.

### Drill-down handling

- **Projects** has a detail view. Use a sub-pane that slides in from the right of the accordion (or a separate full-height sidebar overlay) to display detail without leaving the sidebar context.
- **Sessions** icon picker is a modal — keep it modal.
- **Scheduler** is the most complex. Acceptable Phase 3 outcomes: (a) full migration to a wider-when-expanded sidebar accordion, or (b) leave it as a window for now and revisit. Default to (b) unless time allows.

### Acceptance criteria — Phase 3
- [ ] Config section renders inside the sidebar; no separate window
- [ ] Artifacts section renders inside the sidebar; open/delete work
- [ ] Machines section renders inside the sidebar; status polling works
- [ ] Sessions section renders inside the sidebar; activity updates real-time; clicking a session opens its window
- [ ] Projects section renders inside the sidebar; detail/drill-down work via sub-pane
- [ ] Scheduler either migrated or explicitly deferred
- [ ] All `windows/*-window.js` files for migrated panels deleted
- [ ] `panelMap` and any panel-kind records in `taskbar-state` cleaned up
- [ ] Top menu bar nav links still expand the correct sidebar section (or top menu bar removed cleanly)
- [ ] `desktop_open_panel` MCP tool still functional (now expands sidebar instead of opening window)

---

## Risks & open questions

- **Sidebar width** vs information density. 280px may feel cramped for Sessions/Projects rows. Mitigation: tune widths, allow sidebar to grow when pinned.
- **Hotzone vs WinBox edge resize** — WinBox grab handles on the left edge of windows might conflict with the hotzone. Mitigation: hotzone z-index above desktop area but below modals, and only intercepts when sidebar is closed.
- **Scheduler complexity** — its 1105 LOC + WebSocket-driven 3-tab UI may not fit cleanly in a 280px accordion. Phased deferral built into the plan.
- **Mobile / narrow viewports** — sidebar should auto-pin on touch devices, or use a tap-target instead of hover. Out of scope for v1; record as follow-up.
- **Accessibility** — focus management when sidebar opens, ESC to close, keyboard navigation through accordion sections. Add to Phase 1.
- **Hotzone activation latency** — hover detection should be near-instant. If users need to "fight" the hotzone, add a second activation method (keyboard shortcut, e.g., Cmd+B to toggle).

## Out of scope

- Removing the top menu bar entirely (separate cleanup issue once Phase 3 lands)
- Mobile/touch UX
- Renaming `taskbar-state` localStorage key (cosmetic; do as part of broader cleanup)
- Migrating Scheduler if it doesn't fit cleanly (defer to follow-up)

## Tracking

- Phase 1: Sidebar shell — **DONE**
- Phase 2: Open Windows + delete taskbar — _not started_
- Phase 3: List accordions — _not started_

When complete, move to `docs/missions/completed/sidebar-redesign.md` per the project's documentation philosophy.
