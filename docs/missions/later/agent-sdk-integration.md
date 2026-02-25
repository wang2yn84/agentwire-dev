> Living document. Update this, don't create new versions.

# Mission: Agent SDK Integration (Phase 2)

## Status: Later

## Summary

Replace or supplement tmux terminal scraping with the Claude Agent SDK for structured output. Get clean tool events, proper session state, and permission routing instead of regex-matching ANSI terminal output.

## Why

Current approach: spawn Claude Code in tmux → `capture-pane` → regex parse output.

Agent SDK approach: spawn via SDK → receive structured JSON events → know exactly what's happening.

| Current (tmux scraping) | Agent SDK |
|-------------------------|-----------|
| Regex to detect questions | `AskUserQuestion` event with options |
| ANSI escape parsing | Clean text responses |
| Poll `capture-pane` every 500ms | Streaming JSON events |
| Activity heuristics | Explicit tool_use/tool_result events |
| No tool visibility | Know exactly which tools are called |

## Key Technical Details

- `pip install claude-agent-sdk` (Python) or `npm install @anthropic-ai/claude-agent-sdk`
- Works with Max/Pro subscription via `CLAUDE_CODE_OAUTH_TOKEN`
- SDK bundles Claude Code CLI, spawns it as subprocess
- Structured output: assistant messages, tool_use, tool_result, system events
- `canUseTool` callback for permission routing
- Session persistence and resumption

## Scope

- New session backend: `agentwire/backends/sdk.py` alongside existing tmux backend
- Structured event stream for portal WebSocket
- Permission prompts routed to Telegram/browser inline keyboards
- Tool usage visibility in monitor mode
- Session state without terminal scraping

## Dependencies

- Phase 1 (Telegram Bridge) — validates the multi-channel pattern first
- Claude Agent SDK stable release
- Testing with Max subscription OAuth flow
