> Living document. Update this, don't create new versions.

# Mission: Phase 3 — Scheduler Workflows

Integrate the pi workflow engine (Phase 2) with the existing scheduler so scheduled tasks can be workflows instead of single prompts.

**Phase of:** `pi-harness-overview.md`
**Status:** complete (code shipped 2026-04-16)
**Estimated effort:** 1 week (actual: 1 day)
**Depends on:** Phase 1, Phase 2
**Blocks:** Phase 4 (advanced patterns use scheduler as a driver)

## Goal

Scheduled tasks in `~/.agentwire/scheduler.yaml` can reference a workflow instead of (or in addition to) a single prompt. When the scheduler fires the task, it runs the workflow and reports status.

## Why This Matters

Scheduler tasks today are single-prompt monoliths. Fragile prompts fail in opaque ways. With workflow-backed tasks:
- Each step is independently testable
- Failures point to a specific node, not "the task failed"
- Retries happen at the node level, not re-running the whole task
- Cost and duration are measurable per step
- Common patterns (analyze → verify → report) become reusable

## Scope

### In Scope

- New `workflow:` field in scheduler task schema
- Scheduler dispatch path for workflow tasks (bypasses tmux session creation)
- Workflow run results feed into existing notification/summary system
- Morning report includes per-node workflow details
- At least 3 existing scheduler tasks migrated to workflows
- Gate preconditions (`git_commit`, `git_diff`, `command`) still work for workflow tasks
- Failure handling: partial failures, node-level retry, workflow-level retry

### Out of Scope (Later)

- Running workflows across multiple machines (deferred)
- Workflow versioning / migrations (deferred)
- Automatic rollback on failure (Phase 4)
- Workflow composition — workflows calling workflows (Phase 4)

## Approach

### 1. Extend Scheduler Task Schema

```yaml
# ~/.agentwire/scheduler.yaml
tasks:
  # Existing prompt-based task (still supported)
  morning-briefing:
    schedule: { every: day, at: "08:00" }
    prompt: "Summarize my day..."
    type: claude-bypass
  
  # New: workflow-backed task
  nightly-doc-audit:
    schedule: { every: day, at: "23:00" }
    workflow: doc-drift-check
    inputs:
      paths: [docs/, agentwire/]
    gate:
      git_diff: [docs/, agentwire/]
    retries: 2
    notify: voice
```

### 2. Dispatch Path

Current scheduler:
1. Check gate preconditions
2. Acquire lock
3. Render prompt template
4. Create tmux session
5. Send prompt to session
6. Monitor for idle
7. Extract summary
8. Release lock, notify

New workflow path:
1. Check gate preconditions (unchanged)
2. Acquire lock (unchanged)
3. Load workflow definition
4. Render inputs (from task-level `inputs:` + gate outputs)
5. Call `workflows.runner.run_workflow(name, inputs)` directly — no tmux needed
6. Receive structured result (status, outputs, node events)
7. Persist result as scheduler run record
8. Release lock, notify with node-level detail

Key insight: **workflow tasks don't need tmux sessions.** They run the pi binary in subprocesses, collect JSONL, produce results. Simpler, faster, cheaper.

### 3. Task Result Mapping

Scheduler expects exit codes (see CLAUDE.md: 0=complete, 1=failed, 2=incomplete, etc.). Map workflow status:

| Workflow result | Scheduler exit code |
|-----------------|--------------------|
| All nodes succeed | 0 (complete) |
| Any node fails with `on_error: fail` | 1 (failed) |
| Gate precondition failed | 3 (skipped) |
| Timeout exceeded | 5 (timeout) |
| Invalid workflow definition | 4 (pre failure) |

### 4. Morning Report Integration

Scheduler report HTML needs new sections:
- Workflow runs: duration, cost per node, files modified, failures
- Per-node breakdown when a workflow fails
- Cost rollup across workflow tasks

### 5. Task Migration — Skipped

Existing scheduler tasks were **not migrated**. Decision (2026-04-16): recreate
workflow-backed tasks from scratch rather than carry over old monolithic prompts.
Rationale: the old tasks had well-documented quality issues (`console-cleanup`
too aggressive, `design-audit` broken by headless Chrome, etc.) that are best
addressed by re-authoring instead of mechanically converting.

New workflow tasks should be added directly to `~/.agentwire/scheduler.yaml`
using the shape in §1. Existing ensure tasks continue to work unchanged.

### 6. Scheduler Workflow Commands

```bash
# Dry-run a scheduled workflow
agentwire scheduler run <task> --dry-run   # Shows workflow execution plan

# See why a workflow task failed
agentwire scheduler history <task>  # Now includes workflow run IDs
agentwire workflow show <run-id>    # Drill into node details
```

## Files to Change

| File | Changes |
|------|---------|
| `agentwire/scheduler/tasks.py` | Extend TaskConfig dataclass with `workflow`, `inputs` fields |
| `agentwire/scheduler/dispatcher.py` | New `dispatch_workflow_task()` path alongside existing `dispatch_session_task()` |
| `agentwire/scheduler/schema.py` | Validate workflow reference exists when `workflow:` field set |
| `agentwire/scheduler/report.py` | Render workflow task runs in morning report |
| `agentwire/scheduler/cli.py` | Update `scheduler run` to handle workflow tasks |
| `~/.agentwire/scheduler.yaml` | Migrated tasks (one at a time) |
| `docs/scheduler.md` | Document workflow task schema |
| `tests/scheduler/test_workflow_dispatch.py` | New integration tests |

## Success Criteria

- [ ] Scheduler `workflow:` field works end-to-end with a real task
- [ ] Gates still gate workflow tasks (git_commit, git_diff, command)
- [ ] Cooldown, retries, priority all apply to workflow tasks
- [ ] Exit codes map correctly to scheduler status
- [ ] Morning report shows workflow details per task
- [ ] At least 3 existing tasks successfully migrated, producing results comparable in quality to the original prompt versions
- [ ] No regression in non-workflow task behavior

## Testing Plan

### Unit Tests
- TaskConfig accepts both `prompt:` and `workflow:` (but not both)
- Schema validation: workflow reference must resolve to existing YAML
- Exit code mapping for all workflow result types

### Integration Tests
- Schedule a workflow task, fire it, verify it runs and reports
- Fire a workflow task with failing gate, verify it skips correctly
- Fire a workflow task that hits a node failure with `retries: 2`, verify retry happens
- Fire a workflow task with partial outputs, verify post-commands get the outputs

### Manual QA
- Pick one real scheduler task, convert to workflow, run for 7 days
- Compare: success rate, cost, debuggability, duration
- Document findings in the mission file before moving to next task

## Open Questions

- **Lock granularity:** Should workflow tasks use node-level locks or just task-level? Start with task-level (simpler), revisit if parallelism becomes common.
- **Gate output → workflow input:** Gates today produce output (e.g., `git_diff` files). Should these feed workflow inputs automatically? Probably yes — expose `gate.git_diff.files` as workflow input.
- **Scheduler UI:** Does the portal need a new view for workflow tasks? Defer to Phase 5.
- **Long-running workflows:** What if a workflow takes 20 minutes? Does scheduler block the dispatch queue? Run workflows in background subprocess with their own lifecycle.

## Rollout

1. **Week 1:** Build dispatch path, test with single example workflow task
2. **Week 2:** Migrate first real task (`doc-drift` is a good candidate — non-critical, already multi-step in spirit)
3. **Week 3:** Migrate second task, compare metrics
4. **Week 4:** Migrate third task, decide on acceleration

## Notes

This phase is where the scheduler fully switches from monolithic `pi-zai` task prompts to composable workflow DAGs. Individual-prompt scheduler tasks stay supported (migration is opt-in per task), but workflows become the preferred pattern for anything multi-step.
