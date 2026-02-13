# oh-my-opencode-slim (alvinunreal)

> Research doc. Analysis of the oh-my-opencode plugin for OpenCode multi-agent orchestration.

## Overview

OpenCode plugin that creates a "pantheon" of 6 specialist agents (orchestrator, explorer, oracle, librarian, designer, fixer). The orchestrator delegates to specialists via OpenCode's session API.

## Architecture

- **Specialist Agents:** 6 predefined roles with specific capabilities
- **Delegation:** Orchestrator routes tasks to the best-fit specialist
- **Session API:** Uses OpenCode's native `session.create`, `session.prompt`, `session.status`

## Tmux Management

`TmuxSessionManager` listens for `session.created` events and spawns tmux panes:

- Panes created via `tmux split-window -h -d` running `opencode attach <serverUrl> --session <sessionId>`
- Panes named with truncated descriptions and auto-laid-out
- Pane closing on `session.status` events (type: "idle") or via polling fallback
- 10-minute hard timeout, 3x poll interval grace period

**Key insight:** Tmux is purely visual. `opencode attach` connects a TUI to an existing server session. The session lives in OpenCode's server, not in tmux.

## Background Task System

`BackgroundTaskManager` with:

- Start queue with configurable concurrency (max 10)
- Fire-and-forget launch returns task_id immediately
- Completion detection via `session.status` events
- Fallback model chains (if primary fails, tries next model)
- Delegation rules control which agents can spawn which subagents

## Key Difference from AgentWire

| Aspect | oh-my-opencode | AgentWire |
|--------|---------------|-----------|
| Session ownership | OpenCode server owns sessions | AgentWire owns tmux sessions |
| Communication | OpenCode session API | `tmux send-keys` |
| Tmux role | Visual attachment only | Primary session container |
| Coordination | In-process via plugin | Voice + text alerts |

AgentWire owns the tmux session entirely and communicates via `tmux send-keys`. oh-my-opencode uses OpenCode's native session API and tmux is just a viewport.

## Relevance to AgentWire

- Validates the multi-event approach (`session.status`, `session.created`)
- Their tmux management patterns could inform AgentWire's OpenCode integration
- The fallback model chain concept could be useful for rate-limit handling
