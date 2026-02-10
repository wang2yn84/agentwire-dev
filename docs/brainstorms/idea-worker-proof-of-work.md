# Worker Proof-of-Work

> Workers must pass automated verification before their work is accepted — no more "done" that doesn't compile.

## Problem

Workers lie. Not maliciously — they just have a different definition of "done" than the codebase does.

```
[Worker 2 summary]
Status: ── DONE ──
What I Did: Added rate limiting middleware, wrote tests, updated routes.

[Orchestrator reads summary, trusts it, moves on]

[20 minutes later, during QA]
$ npm run build
ERROR: Property 'rateLimit' does not exist on type 'Request'
ERROR: Cannot find module './middleware/rateLimiter'
3 test failures
```

The orchestrator trusted the worker's self-reported status. Now it has to debug, figure out what went wrong, spawn another worker, and explain the context again. Every false "done" costs 5-10 minutes and thousands of tokens.

This happens constantly because:

1. **Workers don't run verification** — They edit files and assume correctness
2. **Self-assessment is unreliable** — LLMs are confidently wrong about their own output
3. **Orchestrators can't inspect every diff** — That defeats the purpose of delegation
4. **Errors compound** — Worker 2 builds on Worker 1's broken output, making things worse

## Proposed Solution

### Verification Gates

Define per-project verification commands that run automatically after a worker reports completion. Work isn't accepted until verification passes.

```yaml
# .agentwire.yml
verification:
  # Commands run in order, all must pass
  gates:
    - name: typecheck
      cmd: "npx tsc --noEmit"
      timeout: 60

    - name: lint
      cmd: "npm run lint -- --quiet"
      timeout: 30

    - name: build
      cmd: "npm run build"
      timeout: 120

    - name: test
      cmd: "npm test -- --bail"
      timeout: 180
      optional: true  # Warn but don't block

  # How many fix attempts before escalating
  max_fix_attempts: 2

  # What to do when all attempts exhausted
  on_exhausted: escalate  # escalate | accept-with-warning | fail
```

### Lifecycle Change

Current worker lifecycle:
```
Spawn → Work → Write Summary → Idle → Auto-Kill → Orchestrator reads summary
```

New lifecycle:
```
Spawn → Work → Write Summary → Verification Gates → Pass? → Auto-Kill
                                       ↓ Fail
                               Inject errors → Fix attempt → Re-verify
                                       ↓ Still fail (after max attempts)
                               Mark as NEEDS-REVIEW → Auto-Kill
```

### How It Works

**Step 1: Worker finishes and writes summary as normal.**

The worker still writes its summary to `.agentwire/worker-{pane}.md` with `── DONE ──` status.

**Step 2: System intercepts before accepting.**

Instead of immediately notifying the orchestrator, the idle handler runs verification gates:

```python
@on_worker_idle
async def verify_before_accepting(pane: int, session: str):
    config = load_verification_config(session)
    if not config or not config.gates:
        # No verification configured, accept as-is
        return notify_orchestrator(pane, session)

    for gate in config.gates:
        result = run_gate(gate, session)
        if result.failed:
            if attempt < config.max_fix_attempts:
                # Send errors back to worker, give it another chance
                inject_fix_prompt(pane, gate, result)
                return  # Worker continues, will idle again later

            # Out of attempts
            update_summary_status(pane, "NEEDS-REVIEW", result)
            return notify_orchestrator(pane, session)

    # All gates passed
    update_summary_status(pane, "VERIFIED")
    notify_orchestrator(pane, session)
```

**Step 3: On failure, worker gets a fix prompt.**

```
Your work didn't pass verification. Fix the errors and try again.

Gate: typecheck
Exit code: 1
Output:
  src/middleware/rateLimiter.ts(15,3): error TS2339: Property 'rateLimit' does not exist on type 'Request'.
  src/routes/api.ts(4,30): error TS2307: Cannot find module './middleware/rateLimiter'.

This is fix attempt 1 of 2. Focus only on fixing these errors.
```

The worker processes the errors, makes fixes, and goes idle again — triggering re-verification.

**Step 4: Summary reflects verification status.**

```markdown
# Worker Summary

## Status
── VERIFIED ── (passed typecheck, lint, build; tests skipped)

## Verification
- typecheck: PASS (attempt 1)
- lint: PASS (attempt 1)
- build: PASS (attempt 2, fixed missing export)
- test: SKIP (optional, timed out)
```

Or if verification failed:

```markdown
## Status
── NEEDS-REVIEW ── (typecheck failing after 2 fix attempts)

## Verification
- typecheck: FAIL
  Error: Cannot resolve circular dependency between auth.ts and session.ts
  Fix attempts: 2 (both failed)
- lint: PASS
- build: NOT RUN (blocked by typecheck)
```

### Verification Presets

Common project types get sensible defaults:

```yaml
# Auto-detected from package.json / tsconfig.json / etc.
verification:
  preset: typescript-nextjs  # Automatically sets gates
```

| Preset | Gates |
|--------|-------|
| `typescript` | tsc, lint |
| `typescript-nextjs` | tsc, lint, next build |
| `python` | mypy, ruff, pytest |
| `rust` | cargo check, cargo clippy, cargo test |
| `go` | go vet, golangci-lint, go test |

### Scoped Verification

Not every task needs full verification. Workers can be scoped:

```yaml
# In task instructions or .agentwire.yml overrides
verification:
  scope: changed  # Only verify files the worker touched

  gates:
    - name: typecheck-changed
      cmd: "npx tsc --noEmit {{ changed_files }}"
```

Built-in variable `{{ changed_files }}` is populated from `git diff --name-only` against the state when the worker started.

## Implementation Considerations

### Integration with Idle Detection

The verification step hooks into the existing idle detection pipeline. When a worker goes idle:

1. Current behavior: Write summary → notify orchestrator → auto-kill
2. New behavior: Write summary → run gates → (fix loop if needed) → notify → auto-kill

The idle handler checks if verification is configured before deciding the path.

### Worker Context Preservation

The fix prompt must be injected before the worker's context is lost. Since workers auto-kill on idle, verification runs in the brief window between idle detection and kill. If a fix is needed, the auto-kill is deferred and the worker receives the fix prompt instead.

### Gate Parallelism

Independent gates (typecheck + lint) can run in parallel. Dependent gates (build depends on typecheck) run sequentially. Configuration supports this:

```yaml
gates:
  - name: typecheck
    cmd: "npx tsc --noEmit"
  - name: lint
    cmd: "npm run lint"
    parallel_with: typecheck  # Runs at same time as typecheck
  - name: build
    cmd: "npm run build"
    depends_on: [typecheck]   # Waits for typecheck to pass
```

### Cost Tracking

Verification adds token cost (fix attempts) but should reduce net cost (fewer orchestrator debugging cycles). Track:

```
worker_task_tokens: 15000
verification_fix_tokens: 3000  (1 fix attempt)
total: 18000

vs. without verification:
worker_task_tokens: 15000
orchestrator_debug_tokens: 8000
new_worker_tokens: 12000
total: 35000
```

## CLI Integration

```bash
# Run verification manually on current project state
agentwire verify

# Run specific gate
agentwire verify --gate typecheck

# Show verification config for a project
agentwire verify --show-config

# Skip verification for a specific worker spawn
agentwire spawn --roles worker --no-verify
```

## Potential Challenges

### Flaky Tests

Tests that pass sometimes and fail others would cause false rejections. Mitigations:
- `optional: true` flag for flaky gates
- Retry count per gate (not just per fix attempt): `retries: 2` means the gate command itself is retried
- Allow regex patterns to ignore known flaky failures

### Long Build Times

Projects with slow builds would bottleneck verification. Mitigations:
- Scoped verification (only check changed files)
- Parallel gate execution
- Timeout per gate with graceful fallback
- `quick` preset that skips heavy gates: `preset: typescript-quick` (tsc only, no build)

### Worker Confusion on Fix Prompts

Workers might make things worse when trying to fix verification errors, especially with complex type errors. Mitigations:
- Cap fix attempts (default 2)
- Escalate to orchestrator with full context rather than letting the worker flail
- Include the original task context in the fix prompt so the worker doesn't lose the plot

### Shared Worktree Conflicts

Multiple workers sharing a directory means one worker's verification could see another worker's broken state. Mitigations:
- Run verification per-worker using `git stash` isolation
- Better: use git worktrees for parallel workers (already supported)
- Gate commands receive `{{ changed_files }}` to scope checks

### Not All Work is Verifiable

Some tasks (documentation, config changes, design decisions) don't have automated checks. The system gracefully degrades:
- No `verification` config → current behavior (no gates)
- All gates `optional: true` → warnings only
- Workers can be spawned with `--no-verify` for non-code tasks
