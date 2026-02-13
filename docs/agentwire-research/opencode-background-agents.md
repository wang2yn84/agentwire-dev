# opencode-background-agents (kdcokenny)

> Research doc. Analysis of the background delegation plugin for OpenCode.

## Overview

Minimal single-file plugin (~1300 lines) focused purely on async delegation. No tmux integration. Three tools: `delegate`, `delegation_read`, `delegation_list`.

## Architecture

- **Delegate tool:** Agent calls `delegate(prompt, agent)`, gets human-readable ID immediately
- **Child sessions:** Created via `session.create` with `parentID`, prompted via `session.prompt`
- **Anti-recursion:** Disables `delegate`, `task`, `todowrite` tools in child sessions
- **Completion:** Detected via `session.idle` event, result extracted from session messages

## Delegation Flow

1. Agent calls `delegate(prompt, agent)` → returns ID immediately
2. Plugin creates child session via `session.create` with `parentID`
3. Fires prompt via `session.prompt` with restricted tool set
4. Listens for `session.idle` to detect completion
5. Extracts result from session messages
6. Persists to markdown file
7. Sends batched notification to parent session (`noReply` until all complete)

## Persistence

Results written to `~/.local/share/opencode/delegations/<projectId>/<delegationId>.md`. Survives context compaction because the compaction hook injects delegation context.

## Access Control

- Only read-only agents can use `delegate`
- Write-capable agents routed to OpenCode's native `task` tool instead

## Key Difference from AgentWire

| Aspect | background-agents | AgentWire |
|--------|-------------------|-----------|
| Tmux | None | Primary container |
| Voice | None | Core feature |
| Scope | Pure async delegation | Full session orchestration |
| Communication | OpenCode session messages | tmux send-keys + alerts |

This plugin is complementary -- it could theoretically run inside an agentwire-managed OpenCode session for in-process delegation alongside AgentWire's tmux-level coordination.

## Relevance to AgentWire

- Their `session.idle` completion detection has the same problems we're solving (non-deterministic, fires before background tasks)
- The anti-recursion pattern (disabling tools in child sessions) is worth considering
- Batched notifications (wait for all to complete) could inform AgentWire's queue system
