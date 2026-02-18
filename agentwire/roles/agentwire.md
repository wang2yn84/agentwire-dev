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
| `claude-bypass` | Claude Code (skip permissions) |
| `opencode-bypass` | OpenCode / GLM-5 |

### Example

```
pane_spawn(pane_type="claude-bypass", roles="worker")
pane_send(pane=1, message="Add pagination to the posts API")
# Wait for worker alert with summary
```

## Hierarchy

Sessions can have parent sessions. When you go idle, your parent is notified. Use `alert(text, to=session)` to send text notifications up the chain.

## Notifications

| Tool | What it does |
|------|-------------|
| `alert(text)` | Text notification to parent session |
| `alert(text, to=name)` | Text notification to specific session |
