# Cross-Session Event Bus

> Sessions publish and subscribe to events, enabling reactive multi-project workflows without orchestrator bottlenecks.

## Problem

Today, coordinating work across sessions requires a human or parent orchestrator to manually relay information:

```
[API session]: "Auth endpoints done, tests passing"
[Human]: *reads summary, switches to frontend session*
[Human]: "The auth endpoints are ready, integrate them now"
[Frontend session]: *starts integration work*
```

This creates several pain points:

1. **Orchestrator bottleneck** - The parent session must manually ferry information between child sessions, burning expensive tokens on relay work
2. **Latency** - Minutes or hours pass between one session completing and the dependent session starting, because the human has to notice and act
3. **Lost context** - When relaying, the orchestrator summarizes, losing details the consuming session actually needs (endpoint signatures, schema changes, test results)
4. **No reactive workflows** - There's no way to say "when the API is ready, start the frontend work" and walk away

This matters most in multi-project setups where related repositories evolve together: API + frontend, library + consumers, infra + services.

## Proposed Solution

**A lightweight event bus** that lets sessions publish typed events and other sessions subscribe to them. Events carry structured payloads, enabling sessions to react autonomously.

### Event Schema

```yaml
# Event structure
event:
  type: "endpoint.ready"           # Namespaced event type
  source: "api-server"             # Publishing session
  timestamp: "2025-01-15T10:30:00Z"
  payload:                         # Arbitrary structured data
    method: "POST"
    path: "/api/auth/login"
    schema: |
      { email: string, password: string } → { token: string, expires_at: string }
    tests: "passing"
    breaking: false
```

### Publishing Events

Sessions publish events via CLI or MCP:

```bash
# CLI
agentwire event publish "endpoint.ready" \
  --payload '{"method":"POST","path":"/api/auth/login","breaking":false}'

# MCP tool (for agents in sessions)
agentwire_event_publish(
    type="endpoint.ready",
    payload={"method": "POST", "path": "/api/auth/login", "breaking": false}
)
```

Events can also be published automatically from task lifecycle hooks:

```yaml
# .agentwire.yml
tasks:
  build-api:
    prompt: "Build the auth endpoints"
    on_complete:
      publish:
        - type: "endpoint.ready"
          payload:
            endpoints: "{{ summary }}"
```

### Subscribing to Events

Sessions declare subscriptions in their `.agentwire.yml`:

```yaml
# frontend/.agentwire.yml
subscribe:
  - event: "endpoint.ready"
    from: "api-server"           # Optional: filter by source
    action: prompt               # What to do when event fires
    prompt: |
      The API session published new endpoints:
      {{ event.payload | json }}

      Integrate these into the frontend. Update API client types,
      add any new pages/forms needed, and run tests.

  - event: "schema.changed"
    from: "api-server"
    action: prompt
    prompt: |
      Database schema changed: {{ event.payload.migration }}
      Update TypeScript types and any affected queries.

  - event: "deploy.complete"
    from: "infra"
    action: alert                # Just notify, don't prompt
    message: "Staging deploy complete: {{ event.payload.url }}"
```

### Event Delivery

```
api-server publishes "endpoint.ready"
        │
        ▼
   Event Bus (portal)
        │
        ├──► frontend (subscribed) → receives prompt with payload
        ├──► docs (subscribed) → receives prompt to update API docs
        └──► mobile (not subscribed) → ignored
```

The portal maintains an in-memory event bus with file-backed persistence:

```
~/.agentwire/events/
├── bus.jsonl              # Append-only event log
├── subscriptions.json     # Active subscription registry
└── dead-letters.jsonl     # Events that failed delivery
```

### Delivery Semantics

| Behavior | Default | Configurable |
|----------|---------|--------------|
| Delivery | At-least-once | - |
| Ordering | Per-source FIFO | - |
| Retention | 24 hours | `event_retention_hours` |
| Max payload | 10KB | `max_event_payload_kb` |
| Delivery when session idle | Queue until active | `queue_while_idle: true` |
| Delivery when session absent | Dead letter | `dead_letter: true` |

### Event Types (Conventions)

Standard event types that sessions can publish:

| Event | When | Typical Payload |
|-------|------|-----------------|
| `build.complete` | Build/compile succeeds | `{artifact, duration}` |
| `build.failed` | Build/compile fails | `{error, file, line}` |
| `test.complete` | Test suite finishes | `{passed, failed, skipped}` |
| `endpoint.ready` | New API endpoint available | `{method, path, schema}` |
| `schema.changed` | Database migration applied | `{migration, tables_affected}` |
| `deploy.complete` | Deployment finishes | `{environment, url, version}` |
| `dependency.updated` | Package version bumped | `{package, from, to}` |
| `task.complete` | Named task finishes | `{task, status, summary}` |

Custom events use reverse-domain or slash notation: `myapp/cache-invalidated`, `ci/pipeline-green`.

### MCP Tools

```python
# Publish an event
agentwire_event_publish(type="endpoint.ready", payload={...})

# List recent events (for debugging)
agentwire_event_list(since="1h", type="endpoint.*")

# Check subscriptions
agentwire_event_subscriptions()
```

### CLI Commands

```bash
# Publish
agentwire event publish "build.complete" --payload '{"artifact":"dist/"}'

# List recent events
agentwire event list --since 1h
agentwire event list --type "endpoint.*" --from api-server

# Manage subscriptions
agentwire event subs list
agentwire event subs add --event "deploy.complete" --action alert

# Replay events (for debugging or catch-up)
agentwire event replay --type "endpoint.ready" --since 2h --to frontend
```

## Implementation Considerations

### Event Bus Architecture

Keep it simple - no need for Kafka or Redis:

1. Portal holds the bus in memory (dict of event type → subscriber list)
2. On publish: iterate subscribers, deliver via existing `session_send` or `alert` mechanisms
3. Persist to `bus.jsonl` for replay/debugging
4. Load subscriptions from each project's `.agentwire.yml` on session creation

### Subscription Loading

When a session starts, the portal:
1. Reads `.agentwire.yml` from the session's project directory
2. Registers subscriptions in the bus
3. When session dies, unregisters subscriptions

### Delivery to Idle Sessions

If the target session is idle (no active agent), events queue in `~/.agentwire/queues/{session}-events.jsonl`. When the session becomes active, queued events deliver in order. This reuses the existing queue processor infrastructure.

### Payload Templating

Event payloads are injected into subscriber prompts using the existing `{{ variable }}` template system from tasks. The `event` object is available in templates:

- `{{ event.type }}` - Event type string
- `{{ event.source }}` - Publishing session name
- `{{ event.payload }}` - Full payload (auto-formatted)
- `{{ event.payload.field }}` - Specific payload field
- `{{ event.timestamp }}` - When the event was published

### Preventing Loops

Guard against A publishes → B subscribes → B publishes → A subscribes → infinite loop:

1. Events carry a `trace_id` (UUID) and `depth` counter
2. Events triggered by other events increment depth
3. Max depth of 3 (configurable) before events are dead-lettered
4. Log warnings when depth > 1

### Security

- Sessions can only publish events from their own session name (enforced by portal)
- Subscriptions are declared in project config, not dynamically at runtime (prevents rogue subscriptions)
- Payload size limits prevent memory abuse

## Potential Challenges

1. **Agent compliance** - Agents need to reliably publish events at the right moments. Solution: Integrate publishing into task lifecycle hooks (`on_complete`) so events fire automatically without agent cooperation.

2. **Payload schema drift** - Publisher changes payload format, subscriber breaks. Solution: Start without schemas, add optional JSON Schema validation later if needed. For now, subscribers should be resilient to missing fields.

3. **Timing sensitivity** - Frontend session receives "endpoint ready" but the API hasn't actually deployed yet. Solution: Events should represent completed states, not intentions. Publish after the action is verified.

4. **Debugging complexity** - When something goes wrong in a reactive chain, tracing cause-and-effect across sessions is hard. Solution: The `bus.jsonl` log provides a complete audit trail. `agentwire event list` with filters makes tracing feasible.

5. **Subscription sprawl** - Complex projects could end up with many subscriptions creating unexpected interactions. Solution: `agentwire event subs list` shows all active subscriptions across sessions. Keep subscriptions in `.agentwire.yml` (visible in code review) rather than runtime registration.
