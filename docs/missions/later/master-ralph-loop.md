> Living document. Update this, don't create new versions.

# Mission: Master AgentWire Ralph Loop

## Concept

A top-level Ralph loop task that runs on the agentwire-dev project itself — an orchestrator-of-orchestrators that periodically checks on all active sessions, monitors system health, and coordinates work across the fleet.

## Open Questions

- What should each iteration do? (check session health, review worker summaries, rebalance work, etc.)
- What cadence? (loop_delay between iterations)
- Should it spawn/kill sessions based on what it finds?
- Should it produce artifacts (dashboard, status report)?
- How does it interact with the existing parent/child hierarchy?
- Should it own the scheduled task configs for child sessions?

## Related

- `docs/missions/later/ralph-loop-use-cases.md` — brainstormed loop task ideas
- `loop_delay` feature (implemented) — pacing control between iterations
- `[From:]` in session_send — enables two-way inter-agent communication
