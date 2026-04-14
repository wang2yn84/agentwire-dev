> Living document. Update this, don't create new versions.

# Mission: Phase 4 — Advanced Workflow Patterns

Extend the Phase 2 workflow engine with patterns that require more sophistication: parallel execution, loops with accumulators, human-in-the-loop pauses, cost circuit breakers, and cross-workflow composition.

**Phase of:** `pi-harness-overview.md`
**Status:** planned
**Estimated effort:** 2–3 weeks
**Depends on:** Phase 2, Phase 3 (real-world usage validates what's actually needed)
**Blocks:** none (Phase 5 depends on some patterns for visualization)

## Goal

Unlock workflow patterns that are currently impossible with sequential DAGs: fan-out/fan-in, iterative refinement, human approval gates, and composition.

## Why This Phase Is Later

Phases 1–3 establish the foundation. Only after real scheduler tasks run as workflows for weeks do we know which advanced patterns are actually needed. Premature sophistication = wrong abstractions. This mission should be revisited after Phase 3 has been live for 30+ days.

## Scope

### In Scope

- **Parallel execution:** Nodes without interdependencies run concurrently
- **Fan-out / fan-in:** `for_each` spawns N parallel instances of a node, join barrier collects all
- **Loop with accumulator:** `while` or `until` conditions with state carried between iterations
- **Human-in-the-loop:** Node pauses, sends notification, waits for user response, resumes
- **Cost circuit breakers:** Per-workflow cost cap; abort with cleanup if exceeded
- **Cross-workflow calls:** Node can invoke another workflow as a sub-step
- **Shared state store:** Optional sqlite-backed key-value store for cross-run memory
- **Rollback hooks:** On failure, declared rollback nodes run to undo changes
- **Event-driven triggers:** Workflows triggered by file changes, webhooks, git hooks

### Out of Scope

- Distributed execution across machines (separate mission if needed)
- Real-time collaborative editing of running workflows
- GUI-based workflow authoring (Phase 5 handles visualization, not authoring)

## Approach

### 1. Parallel Execution

Refactor `runner.py` to async. Identify independent nodes per topological layer, execute via `asyncio.gather()`.

```yaml
nodes:
  fetch-a:
    prompt: "Read file A"
    # No depends_on — runs in layer 0
  
  fetch-b:
    prompt: "Read file B"
    # No depends_on — runs in layer 0 (in parallel with fetch-a)
  
  merge:
    depends_on: [fetch-a, fetch-b]
    prompt: "Combine: {{ fetch-a.text }} and {{ fetch-b.text }}"
    # Runs in layer 1 after both complete
```

Key constraints:
- Limit concurrency via `max_parallel` config (default 4 — respect Z.AI rate limits)
- Stream events from all parallel nodes, interleave in event log with node_id tags

### 2. Fan-out / Fan-in

```yaml
nodes:
  list-files:
    prompt: "List all .ts files as JSON array"
    outputs: [files]
  
  check-each:
    depends_on: [list-files]
    for_each: "{{ list-files.files }}"   # Spawns N instances
    as: file                             # Loop variable name
    max_parallel: 3
    prompt: "Check {{ file }} for issues"
    outputs: [issues]
  
  aggregate:
    depends_on: [check-each]
    prompt: |
      All issues found:
      {% for issue in check-each.all_outputs.issues %}
        - {{ issue }}
      {% endfor %}
```

Semantics: `check-each` produces a collection (`all_outputs`) indexed across iterations. Downstream nodes access aggregated view.

### 3. Loops With State

```yaml
nodes:
  iterate:
    loop:
      until: "{{ complexity < 10 or iterations >= 5 }}"
      initial: { complexity: 100, iterations: 0 }
    prompt: |
      Current complexity: {{ state.complexity }}
      Refactor the next-worst function. Report new complexity as JSON: {"complexity": N}
    outputs:
      - name: complexity
        source: jsonpath
        pattern: $.complexity
    loop_update:
      complexity: "{{ outputs.complexity }}"
      iterations: "{{ state.iterations + 1 }}"
```

Bounds: loop max iterations config (default 10) to prevent runaway.

### 4. Human-in-the-Loop

```yaml
nodes:
  propose:
    prompt: "Generate refactoring plan"
    outputs: [plan]
  
  approve:
    depends_on: [propose]
    type: human_gate
    notify:
      channel: slack
      text: |
        Refactor plan ready:
        {{ propose.plan }}
        
        Reply "approve" or "reject" to this thread.
    timeout: 3600       # 1 hour
    expected_responses: [approve, reject]
    on_timeout: reject
  
  execute:
    depends_on: [approve]
    when: "{{ approve.response == 'approve' }}"
    prompt: "Apply the refactorings"
```

Implementation: node writes a "waiting" marker file, scheduler/daemon monitors for response from channel, unblocks.

### 5. Cost Circuit Breaker

```yaml
workflow:
  cost_cap_usd: 2.00           # Abort if exceeds
  cost_cap_action: rollback    # rollback | halt | warn
```

Every node accumulates cost from pi's JSONL `usage` events. Before each node, check cumulative — abort if over.

### 6. Cross-Workflow Calls

```yaml
nodes:
  verify:
    type: workflow_call
    workflow: test-suite
    inputs:
      test_pattern: "src/api/**"
    outputs: [passed, failed_tests]
```

Implementation: `workflow_call` nodes spawn a sub-runner with shared storage. Keep call graph flat — no deep nesting.

### 7. Shared State Store

Simple sqlite-backed k-v store at `~/.agentwire/workflows/state.db`:

```yaml
nodes:
  remember:
    prompt: "Process events"
    state_write:
      last_processed_id: "{{ outputs.latest_id }}"
  
  use-later:
    prompt: "Continue from {{ state.last_processed_id }}"
    state_read: [last_processed_id]
```

Scope of state: per-workflow-name by default, or `shared_state_scope: global`.

### 8. Rollback Hooks

```yaml
nodes:
  modify:
    prompt: "Apply changes"
    on_failure_call: undo-modifications   # Another node id

  undo-modifications:
    prompt: "Git reset --hard"
    tools: [bash]
```

Semantics: if `modify` fails after retries exhausted, `undo-modifications` runs before workflow halts.

### 9. Event-Driven Triggers

New daemon: `agentwire workflow-trigger serve`

Triggers config:
```yaml
# ~/.agentwire/workflow-triggers.yaml
triggers:
  on-pr-opened:
    event: webhook
    endpoint: /pr-opened
    workflow: pr-triage
    inputs:
      pr_url: "{{ event.pr.url }}"
  
  on-file-change:
    event: fswatch
    paths: [docs/]
    debounce: 60
    workflow: doc-drift-check
```

Defer unless there's real demand — don't build speculative infrastructure.

## Files to Change

| File | Changes |
|------|---------|
| `agentwire/workflows/runner.py` | Async refactor, parallel layer execution |
| `agentwire/workflows/node.py` | New node types: LoopNode, HumanGateNode, WorkflowCallNode |
| `agentwire/workflows/state.py` | New: sqlite k-v store |
| `agentwire/workflows/cost.py` | New: cost tracker + circuit breaker |
| `agentwire/workflows/triggers.py` | New: event-driven trigger daemon |
| `agentwire/workflows/cli.py` | New commands: `workflow state ls/get/set`, `workflow pause/resume <run-id>` |
| `docs/workflows-advanced.md` | New doc covering all Phase 4 patterns |

## Success Criteria

- [ ] Parallel nodes demonstrably run concurrently (wall clock < sum of durations)
- [ ] `for_each` works with collection outputs, respects `max_parallel`
- [ ] Loop with `until` terminates on condition and on max iterations
- [ ] Human-in-the-loop: workflow pauses, Slack message received, resumes on reply
- [ ] Cost cap: workflow aborts when cost exceeded, runs rollback nodes
- [ ] `workflow_call` nodes work, sub-workflow outputs accessible in parent
- [ ] State store persists across runs of the same workflow
- [ ] Rollback runs on failure before workflow halts

## Testing Plan

### Parallel / Fan-out
- Workflow with 5 independent nodes — verify wall clock ≈ max(durations), not sum
- `for_each` over 10-item array with `max_parallel: 3` — verify throttling works

### Loops
- Loop terminates on condition
- Loop hits max iterations and halts with proper error
- Loop state updates correctly between iterations

### Human-in-the-Loop
- Mock Slack reply in test, verify workflow resumes
- Timeout without reply, verify `on_timeout` behavior

### Cost
- Workflow with artificial high-cost node, verify cap triggers abort
- Verify partial results are still preserved

## Open Questions

- **Async vs thread-pool:** Pi is a subprocess, so OS-level parallelism works. asyncio.subprocess is cleaner than ThreadPoolExecutor.
- **Z.AI rate limits:** What's the actual concurrent request limit? Default `max_parallel: 4` conservative; may need per-model tuning.
- **Human gate implementation:** Use existing channel bridges (Slack/Discord) with a "waiting workflow" marker, or build a dedicated gate service? Start with bridge + marker file; refactor if noisy.
- **State store consistency:** Single-writer assumption for sqlite is fine until workflows run on multiple machines. Defer multi-machine state.
- **Backwards compat:** Do Phase 2 workflow definitions still work unchanged? They must. All Phase 4 features are opt-in via new YAML fields.

## Risk Mitigation

- **Async bugs:** Async Python has sharp edges. Write tests for cancellation, exception propagation, task cleanup.
- **Cost runaway in loops:** Hard-cap loop iterations, require explicit opt-in above default.
- **Human gate timeouts hanging forever:** Default timeout is mandatory; error out if not set.
- **State race conditions:** Writes within a node are single-threaded; parallel nodes writing to same key is undefined — document + detect in validator.

## Notes

This is the mission that makes pi + workflows genuinely novel in the agent tooling space. Most workflow engines either lack LLM-specific primitives (n8n, Airflow) or lack general-purpose plumbing (Langgraph). Pi + our engine hits both.

Revisit after Phase 3 live data. Some of these patterns may be unnecessary; others we haven't imagined yet may prove essential.
