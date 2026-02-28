> Living document. Update this, don't create new versions.

# Pixel Agents Office Improvements

Proposals for enhancing the pixel agents office visualization in the portal.

## Proposal #1: Always-Visible Session Name Labels

**Status:** In Progress

Show session names below each character at all times (not just on hover). The `AgentLabels` component already exists but isn't rendered in `App.tsx`.

**Changes:**
- Add `AgentLabels` to `App.tsx` rendering
- Fix `var(--vscode-foreground)` to `var(--pixel-text)` in AgentLabels
- Show `ch.folderName` (session name) instead of `Agent #${id}`
- Style: small text below character, always visible, non-interactive

## Proposal #2: Processing Prompt Tool Activity

**Status:** In Progress

Show "Processing prompt..." as tool activity when a message is sent to a session. The portal emits `session_processing` events which we now translate to both `agentStatus` (typing animation) and `agentToolStart` (hover shows "Processing prompt..."). TTS events already show "Speaking...".

**Future:** The engine supports reading vs typing animations via `isReadingTool()` in `characters.ts`. When per-tool events become available (e.g., via SDK sessions), we can send tool names in `agentToolStart` status to trigger reading animations for Read/Grep/Glob tools.

**Changes:**
- Enhanced `session_processing` handler in `office-window.js` to also send `agentToolStart`/`agentToolsClear`
- Hover tooltip shows "Processing prompt..." while agent works on a prompt

## Proposal #3: Permission Bubbles

Show a permission bubble (speech bubble with "?" icon) above characters when their session needs tool approval. The engine already has `showPermissionBubble()` and `clearPermissionBubble()`.

**Changes:**
- Listen for `session_permission` events in office-window.js
- Send `agentToolPermission` and `agentToolPermissionClear` messages
- Characters will show the built-in permission bubble animation

## Proposal #4: Sub-Agent Visualization

When an agent spawns worker panes, show them as smaller sub-agent characters near the parent. The engine supports sub-agents with `addSubagent()`/`removeSubagent()`.

**Changes:**
- Listen for `pane_created`/`pane_closed` events
- Send `agentToolStart` with `Subtask:` prefix to trigger sub-agent spawning
- Sub-agents appear with matrix effect near parent's desk

## Proposal #5: Waiting Bubble on Idle

Show a speech bubble when a session goes idle/waiting. The engine has `showWaitingBubble()` which displays a "..." thought bubble. Currently wired but could be enhanced with idle duration or last action context.

## Proposal #6: Custom Character Skins per Session

Allow sessions to have persistent character appearances. Currently uses hash-based palette assignment. Could use `.agentwire.yml` to define preferred character skin/color per project.

## Proposal #7: Desk Labels / Room Organization

Show project names on desks or organize characters into rooms by project type. The layout editor supports custom furniture placement - could auto-assign desks by project.

## Proposal #8: Activity History Trail

Show a fading trail or recent activity log per character. When hovering, show the last N tool calls as a scrollable list instead of just the current status.

## Proposal #9: Sound Effects

Add optional pixel-art sound effects for events: matrix spawn/despawn, keyboard typing, notification chime for permission requests. Respect portal's existing audio system.
