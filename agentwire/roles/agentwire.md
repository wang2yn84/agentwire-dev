---
name: agentwire
description: Understands the agentwire session and pane system
---

# AgentWire

You're running inside an agentwire session. You have MCP tools for managing sessions, panes, and communication.

## Sessions

Sessions are tmux sessions running AI agents. You can create, message, and monitor them.

| Tool | What it does |
|------|-------------|
| `sessions_list()` | List all active sessions |
| `session_create(name)` | Create a new session |
| `session_send(session, message)` | Send a prompt to a session |
| `session_output(session, lines)` | Read session output |
| `session_info(session)` | Get session metadata |
| `session_kill(session)` | Kill a session |

## Panes (Workers)

Panes are sub-processes within your session. Pane 0 is you. Panes 1+ are workers.

**Do NOT spawn workers unless the user asks you to, or the task clearly requires parallel work across multiple files/features.** Most tasks are simpler and faster to do yourself. Workers have overhead (session startup, context loading, summary handoff) that isn't worth it for straightforward work.

Workers are for: large refactors touching many files, parallel independent subtasks, long-running operations you want to monitor.

| Tool | What it does |
|------|-------------|
| `pane_spawn(pane_type, roles)` | Spawn a worker pane |
| `pane_send(pane, message)` | Send a task to a worker |
| `pane_output(pane)` | Read worker output |
| `panes_list()` | List all panes |
| `pane_kill(pane)` | Kill a worker pane |

Workers auto-exit when idle. They write summary files before exiting, and you receive the summary via an alert notification.

### Spawn types

| `pane_type` | Agent |
|-------------|-------|
| `claudeglm-bypass` | Claude Code via Z.AI GLM-5 (default — use this) |
| `claude-bypass` | Claude Code via Anthropic (expensive — only if needed) |

## Hierarchy

Sessions can have parent sessions. When you go idle, your parent is notified. Use `notify(text, to=session)` to send text notifications up the chain. Use `reply(text)` to respond to channel users (Discord, Slack, Telegram).

## Overnight Queue

Queue prepared sessions for autonomous overnight execution with full conversation context.

| Tool | What it does |
|------|-------------|
| `overnight_prepare(session, description)` | Queue a session for overnight dispatch |
| `overnight_list()` | List queued/running items |
| `overnight_status()` | Orchestrator state and queue summary |
| `overnight_cancel(item_id)` | Cancel a queued or running item |
| `overnight_priority(item_id, priority)` | Update item priority |
| `overnight_report()` | Morning report of completed items |

## Notifications

| Tool | What it does |
|------|-------------|
| `reply(text)` | Reply to channel user (Discord, Slack, Telegram) |
| `notify(text)` | Text notification to parent session |
| `notify(text, to=name)` | Text notification to specific session |
