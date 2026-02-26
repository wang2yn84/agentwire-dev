> Living document. Update this, don't create new versions.

# Mission: Agent SDK Integration (Phase 2)

## Status: Completed

## Summary

Replaced tmux terminal scraping with the Claude Agent SDK for structured output. SDK sessions run as pure Python async processes in the portal, providing structured JSON message events instead of terminal scraping.

## What Was Built

### Step 1: SDK Agent Backend
- `agentwire/agents/sdk.py` — `SdkAgent` backend with `ClaudeSDKClient` per session
- `agentwire/agents/base.py` — Extended `AgentBackend` ABC with non-abstract SDK methods
- Session types: `sdk-bypass`, `sdk-prompted`, `sdk-restricted`

### Step 2: Portal Integration
- SDK sessions live in portal memory (no tmux)
- Portal routes via `_get_backend_for_session()` / `_is_sdk_session()` helpers
- Chat-like UI with message list + input bar (`mode: "sdk"`)
- CLI creates SDK sessions via portal API (`POST /api/create`)

### Step 3: Frontend
- CSS type tags use purple for SDK types
- SDK session window with structured message display

### Step 4: Task Runner
- `cmd_ensure` support for SDK sessions

### Step 5: Hierarchy (Phase 2B)
- Parent-child session relationships with `parent_session` tracking
- 6 MCP tools: `sdk_child_spawn`, `sdk_child_send`, `sdk_child_status`, `sdk_child_result`, `sdk_children_list`, `sdk_child_kill`
- 2 portal API endpoints: `POST /api/session/{name}/spawn`, `GET /api/session/{name}/children`
- Kill cascade: killing parent kills all children
- Auto-kill on completion with `child_completed` notifications to parent
- Session persistence and dedup in listings
- Dashboard visualization: hierarchy tags, parent-child sort grouping

## Key Technical Details

- `pip install claude-agent-sdk` (Python)
- Works with Max/Pro subscription via `CLAUDE_CODE_OAUTH_TOKEN`
- SDK bundles Claude Code CLI, spawns it as subprocess
- Structured output: assistant messages, tool_use, tool_result, system events
- `canUseTool` callback for permission routing
- Session persistence and resumption

## Dependencies

- ~~Phase 1 (Telegram Bridge) — validates the multi-channel pattern first~~ (not required)
- Claude Agent SDK stable release ✓
- ~~Testing with Max subscription OAuth flow~~ (using API key auth)
