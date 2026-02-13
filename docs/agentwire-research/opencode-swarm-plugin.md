# opencode-swarm-plugin (joelhooks)

> Research doc. Analysis of the Swarm multi-agent coordination framework for OpenCode.

## Overview

The most ambitious OpenCode plugin -- a monorepo with 60+ source files implementing a full multi-agent coordination framework. Includes its own database, issue tracker, memory system, and file reservation protocol.

## Architecture

### Subsystems

| System | Purpose |
|--------|---------|
| **Swarm Mail** | Actor-model messaging with embedded libSQL event store |
| **Hive** | Git-backed issue tracker (`.hive/` directory) |
| **Hivemind** | Semantic memory with optional Ollama embeddings |
| **Git Worktrees** | Parallel isolation via `git worktree add --detach` |
| **Task Decomposition** | Breaks tasks into CellTree (epic + parallelizable subtasks) |
| **Learning System** | Records outcomes, 3-strike detection, feedback scoring |

### Swarm Mail

- Actor-model messaging between agents
- Embedded libSQL event store for full audit trail
- File reservations with conflict detection
- Event-sourced for reproducibility

### Hive (Issue Tracker)

- Tasks tracked as "cells" in `.hive/` directory
- Status, priority, dependencies
- Git-backed for version control

### Git Worktrees

- Workers get their own worktree via `git worktree add --detach`
- Cherry-pick commits back to main on completion
- Parallel isolation without branch conflicts

### Task Decomposition

- Breaks tasks into CellTree structure
- Epic + parallelizable subtasks with file assignments
- Coordinator broadcasts context updates

### Learning System

- Records outcomes per task
- 3-strike detection for recurring architectural problems
- Feedback scoring for quality tracking

## Communication Pattern

File-based + embedded database via Swarm Mail (libSQL). File reservations prevent conflicts. No direct tmux management -- relies on OpenCode's native `task` tool for worker sessions.

## Key Difference from AgentWire

| Aspect | Swarm | AgentWire |
|--------|-------|-----------|
| Philosophy | Maximalist, batteries-included | Unix-philosophy, thin wrappers |
| Database | Embedded libSQL | None (file-based) |
| Issue tracking | Built-in (.hive/) | External (GitHub) |
| Memory | Semantic with embeddings | None (agent context) |
| Coordination | File reservations + mail | Voice + text alerts |
| Tmux | None (OpenCode native tasks) | Primary container |
| Complexity | 60+ source files | Single plugin file |

Swarm is purely in-process (OpenCode plugin). AgentWire is more Unix-philosophy: thin wrappers around tmux with voice as the coordination layer.

## Relevance to AgentWire

- The git worktree pattern is already implemented in AgentWire (via `agentwire fork`)
- File reservation concept could prevent worker conflicts (but adds complexity)
- The learning/memory system is interesting but out of scope for AgentWire's thin-wrapper philosophy
- Validates that the multi-agent orchestration space is active and growing
