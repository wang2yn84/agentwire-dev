> Living document. Update this, don't create new versions.

# Mission: Pi Harness Integration — Overview

Integrate [pi coding agent](https://github.com/badlogic/pi-mono) as the harness for all Z.AI work. Keeps Claude Code pure for Anthropic subscription and unlocks new capabilities (dual-mode REPL + programmable).

**Context:** The previous claudeGLM wrapper (env var override into the `claude` binary) was removed from main on 2026-04-12 — Claude Code ignores inline `ANTHROPIC_*` env vars when OAuth auth is active, so the wrapper silently stopped working. Pi replaces that approach with a proper standalone binary that has native Z.AI provider support.

**Decision made 2026-04-13** after hands-on evaluation. See `~/.agentwire/wiki/wiki/research/pi-coding-agent-zai-harness.md` for the full evaluation.

## Why Pi

| Benefit | Detail |
|---------|--------|
| Native Z.AI provider | `--provider zai` built-in — no env var hacks |
| Minimal tool surface | 4 tools (read/write/edit/bash), smaller system prompt, more context |
| Print mode (`-p`) | Non-interactive execution, exits when done — perfect for automation |
| JSON event stream | `--mode json` emits JSONL for programmatic parsing |
| RPC protocol | `--mode rpc` for bidirectional control (future) |
| CLAUDE.md aware | Loads CLAUDE.md/AGENTS.md from cwd automatically |
| `--append-system-prompt` | Identical flag to Claude Code — role injection works unchanged |
| MIT licensed | No subscription tied to anyone's binary |
| Thinking control | `--thinking off\|low\|medium\|high\|xhigh` per task |

## The Architecture

Pi unlocks a **dual-mode** model that Claude Code can't cleanly provide:

```
┌─────────────────────────────────────────────────────────┐
│  Claude Code (Anthropic subscription)                   │
│  ├─ Human-directed interactive sessions                 │
│  ├─ Orchestrator sessions (need MCP tools)              │
│  └─ Overnight queue dispatch                            │
├─────────────────────────────────────────────────────────┤
│  Pi REPL Mode (Z.AI subscription)                       │
│  ├─ Interactive coding, cost-sensitive                  │
│  ├─ Worker panes that take multiple tasks               │
│  └─ Live exploration / debugging                        │
├─────────────────────────────────────────────────────────┤
│  Pi Programmable Mode (Z.AI subscription)               │
│  ├─ Workflow action nodes                               │
│  ├─ Scheduler task nodes                                │
│  └─ Chainable automation DAGs                           │
└─────────────────────────────────────────────────────────┘
```

## Mission Phases

Each phase is its own mission doc. Execute sequentially — each validates the next.

| # | Mission | Doc | Status |
|---|---------|-----|--------|
| 1 | Pi Session Type | `pi-session-type.md` | **complete (2026-04-13)** |
| 2 | Pi Workflow Engine | `pi-workflow-engine.md` | **complete (2026-04-14, v1.22.0)** |
| 3 | Scheduler Workflows | `pi-scheduler-workflows.md` | **complete (2026-04-16, v1.23.0)** |
| 6 | Agent-SDK Workflow Runner (parallel to pi) | `anthropic-sdk-runner.md` | **next** |
| 4 | Advanced Workflow Patterns | `pi-workflow-advanced.md` | planned — **gated on Phase 6 reaching parity** |
| 5 | Workflow Desktop UI | `pi-workflow-ui.md` | planned — **gated on Phase 6 reaching parity** |

**Reorder note (2026-04-16)**: After Anthropic confirmed subscription-mode Agent SDK usage is supported, Phase 6 was added and prioritized ahead of 4 and 5. Advanced patterns (parallelism, loops, HITL, cost caps) and the desktop UI need to work uniformly across runners — building them on pi alone would force a retrofit later. See `anthropic-sdk-runner.md` for the rationale.

**Completion target:** Phases 1–3 by 2026-05-15. Phase 4–5 as needed.

## Dependencies Across Phases

```
Phase 1 (Session Type)
  │
  ├──► Phase 2 (Workflow Engine) ──► Phase 3 (Scheduler Workflows)
                                         │
                                         └──► Phase 6 (Agent-SDK Runner, parallel to pi)
                                                │
                                                ├──► Phase 4 (Advanced Patterns — runner-agnostic)
                                                └──► Phase 5 (Desktop UI — runner-agnostic)
```

Phase 1 validates pi works in our stack. Phase 2 builds the engine. Phase 3 wires the engine into the scheduler. Phase 6 adds a second runner (Anthropic SDK) alongside pi so higher-level features can be built on a runner-agnostic abstraction. Phases 4–5 extend the engine on top of that abstraction.

## Key Non-Goals

- **Not replacing Claude Code entirely** — Claude Code remains the primary harness for Anthropic-subscription work and anything requiring the agentwire MCP client
- **Not adding MCP to pi** — pi intentionally doesn't ship MCP; workflow nodes can call agentwire CLI via bash when needed

## Migration Plan (High Level)

1. Phase 1 ships: `pi-zai` session type is the sole Z.AI path (claudeGLM already gone from main)
2. Scheduler migrates tasks to `pi-zai` as Phase 3 workflow engine lands
3. Phases 4–5 extend pi for advanced patterns and UI

## Open Questions Across All Phases

- Pi RPC protocol docs are sparse — does it stabilize before we depend on it?
- How do we handle pi binary upgrades? npm global install, version pinning?
- Should workflow nodes support non-pi engines (Claude Code fallback)?
- What's the right event/state persistence store for long-running workflows?
