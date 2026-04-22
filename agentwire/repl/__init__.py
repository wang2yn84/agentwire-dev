"""Agentwire REPL — interactive harness built on claude-agent-sdk.

See docs/missions/agentwire-repl.md for the full mission scope.

This package is the implementation of the `sdk-bypass`, `sdk-prompted`, and
`sdk-restricted` session types. It is invoked by build_agent_command when a
session is spawned via `agentwire new --type sdk-*`; users do not call it
directly.

Phase 1 (this PR) — scaffolding only. Proves session-type plumbing works.
SDK integration, streaming, tools, and the real REPL loop land in PR 2+.
"""
