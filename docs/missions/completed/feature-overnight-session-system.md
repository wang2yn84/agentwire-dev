> Living document. Update this, don't create new versions.

# Mission: Overnight Session System

**Status:** Complete
**Issue:** #72
**Branch:** `feature/overnight-session-system`

## Done When

- [x] `agentwire overnight prepare` captures Claude sessionId + git state
- [x] `agentwire overnight list/status/cancel/priority` queue management
- [x] Orchestrator dispatches during configurable work window
- [x] Dispatch forks Claude conversation context via `--resume --fork-session`
- [x] Agent creates work branch and executes with go prompt
- [x] Completion detection via idle hook system
- [x] Auto-commit, push, draft PR on completion
- [x] Archive to `done/`, morning report
- [x] `overnight start/serve/stop` daemon lifecycle
- [x] 6 MCP tools for agent access
- [x] `OvernightConfig` in config.yaml
- [x] CLAUDE.md, scheduled-workloads.md, agentwire role updated

## Key Files

| File | What |
|------|------|
| `agentwire/overnight.py` | Core module: queue CRUD, dispatch, completion, orchestrator loop |
| `agentwire/config.py` | `OvernightConfig` dataclass |
| `agentwire/__main__.py` | 9 CLI commands + argparse |
| `agentwire/mcp_server.py` | 6 MCP tools |

## Architecture

Separate module from scheduler. Scheduler = recurring predefined YAML tasks. Overnight = one-shot human-prepared sessions with forked conversation context.

Queue files live in `~/.agentwire/overnight/` (active) and `~/.agentwire/overnight/done/` (archived).

## Testing

Full end-to-end test passed: prepare from real Claude session, orchestrator dispatch, agent execution on work branch, completion detection, auto-commit, finalization, morning report.
