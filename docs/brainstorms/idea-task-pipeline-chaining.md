# Task Pipeline Chaining: Multi-Stage Workflows with Automatic Handoff

> Define sequential task pipelines where each stage's output becomes the next stage's input, with automatic handoff between workers.

## Problem

Complex work naturally decomposes into stages:

```
1. Implement the feature
2. Write tests for it
3. Update documentation
4. Create PR
```

Today, orchestrating this requires manual intervention at each step:

- Orchestrator spawns Worker A for implementation
- Waits for completion, reads summary
- Spawns Worker B for tests, manually includes context from A
- Repeat...

This creates friction:
- **Attention overhead**: Must monitor and intervene at each handoff
- **Context loss**: Each new worker starts fresh, needs briefing
- **Error prone**: Manual copying of context between stages
- **Blocking**: Can't walk away during multi-stage work

The orchestrator becomes a bottleneck, doing low-value coordination work.

## Proposed Solution

**Task Pipelines** - declarative multi-stage workflows where the system handles handoff automatically.

### Pipeline Definition

In `.agentwire.yml`:

```yaml
pipelines:
  full-feature:
    description: "Implement feature with tests and docs"
    stages:
      - name: implement
        roles: [glm-worker]
        prompt: |
          Implement: {{ task_description }}
          Files: {{ target_files }}

      - name: test
        roles: [glm-worker]
        prompt: |
          Previous stage: {{ stages.implement.summary }}
          Files changed: {{ stages.implement.files_changed }}
          Write comprehensive tests for these changes.
        depends_on: implement

      - name: document
        roles: [claude-worker]
        prompt: |
          Implementation: {{ stages.implement.summary }}
          Tests: {{ stages.test.summary }}
          Update relevant documentation for this feature.
        depends_on: test

      - name: review
        roles: [claude-worker]
        prompt: |
          Review all changes from this pipeline:
          - Implementation: {{ stages.implement.files_changed }}
          - Tests: {{ stages.test.files_changed }}
          - Docs: {{ stages.document.files_changed }}
          Create a PR with a comprehensive description.
        depends_on: document

    on_complete: |
      say "Feature pipeline complete. PR ready for review."
```

### Pipeline Invocation

```bash
# CLI
agentwire pipeline run full-feature \
  --var task_description="Add user authentication" \
  --var target_files="src/auth/"

# Voice
[User]: "Run full feature pipeline for user authentication in src/auth"

# MCP (for orchestrators)
agentwire_pipeline_run(
  pipeline="full-feature",
  vars={"task_description": "Add user authentication", "target_files": "src/auth/"}
)
```

### Automatic Handoff

When stage N completes:

1. System captures worker summary → `stages.N.summary`
2. Extracts files changed → `stages.N.files_changed`
3. Evaluates stage N+1's `depends_on`
4. Spawns stage N+1 worker with interpolated prompt
5. Injects previous stage context automatically

```
Stage 1 (implement)     Stage 2 (test)        Stage 3 (document)
┌─────────────────┐     ┌─────────────────┐    ┌─────────────────┐
│  GLM Worker     │     │  GLM Worker     │    │  Claude Worker  │
│                 │     │                 │    │                 │
│  Implements     │────▶│  Writes tests   │───▶│  Updates docs   │
│  feature        │     │  for changes    │    │  for feature    │
└─────────────────┘     └─────────────────┘    └─────────────────┘
        │                       │                      │
        ▼                       ▼                      ▼
   summary.md              summary.md             summary.md
   files_changed          files_changed          files_changed
```

### Context Propagation

Each stage receives structured context from dependencies:

```yaml
# Available in stage prompts:
{{ stages.implement.summary }}       # Full summary text
{{ stages.implement.files_changed }} # List of modified files
{{ stages.implement.status }}        # DONE, BLOCKED, ERROR
{{ stages.implement.duration }}      # How long it took
{{ stages.implement.attempt }}       # Retry count
```

### Parallel Stages

Stages without dependencies can run in parallel:

```yaml
pipelines:
  full-stack-feature:
    stages:
      - name: api
        prompt: "Implement API endpoint for {{ feature }}"

      - name: frontend
        prompt: "Implement frontend component for {{ feature }}"
        # No depends_on - runs parallel with api

      - name: integration
        prompt: |
          API changes: {{ stages.api.summary }}
          Frontend changes: {{ stages.frontend.summary }}
          Connect frontend to API and test integration.
        depends_on: [api, frontend]  # Waits for both
```

### Conditional Stages

Skip stages based on conditions:

```yaml
stages:
  - name: test
    prompt: "Run tests"

  - name: fix-tests
    prompt: "Fix failing tests: {{ stages.test.summary }}"
    depends_on: test
    when: "{{ stages.test.status == 'ERROR' }}"

  - name: deploy
    prompt: "Deploy to staging"
    depends_on: test
    when: "{{ stages.test.status == 'DONE' }}"
```

### Pipeline Status

```bash
# Check running pipeline
agentwire pipeline status full-feature

Pipeline: full-feature
Started: 2 minutes ago
Current: stage 2/4 (test)

┌──────────┬────────┬──────────┬─────────┐
│ Stage    │ Status │ Duration │ Worker  │
├──────────┼────────┼──────────┼─────────┤
│ implement│ ✓ DONE │ 1m 23s   │ pane 1  │
│ test     │ ◉ RUN  │ 0m 45s   │ pane 2  │
│ document │ ○ WAIT │ -        │ -       │
│ review   │ ○ WAIT │ -        │ -       │
└──────────┴────────┴──────────┴─────────┘
```

### Pipeline Events

Hooks for monitoring and integration:

```yaml
pipelines:
  full-feature:
    on_stage_complete: |
      alert "{{ stage_name }} complete: {{ stage_status }}"

    on_stage_error: |
      say "{{ stage_name }} failed. Pipeline paused."

    on_complete: |
      email --subject "Feature ready" --body "{{ pipeline_summary }}"

    on_error: |
      # Human-in-the-loop recovery
      say "Pipeline blocked at {{ failed_stage }}. What should I do?"
```

## Implementation Considerations

### Pipeline State Machine

```python
class PipelineExecutor:
    def __init__(self, pipeline_def: dict, variables: dict):
        self.stages = self._build_dag(pipeline_def["stages"])
        self.context = {"vars": variables, "stages": {}}
        self.active_workers = {}

    async def run(self):
        while not self.all_complete():
            # Find stages with satisfied dependencies
            ready = self._get_ready_stages()

            # Spawn workers for ready stages (up to concurrency limit)
            for stage in ready:
                worker_pane = await self._spawn_worker(stage)
                self.active_workers[stage.name] = worker_pane

            # Wait for any worker to complete
            completed = await self._wait_for_completion()

            # Capture context from completed workers
            for stage_name in completed:
                self.context["stages"][stage_name] = await self._capture_context(
                    self.active_workers[stage_name]
                )

    def _render_prompt(self, stage) -> str:
        return render_template(stage.prompt, self.context)
```

### Context Capture

Extend worker summaries with structured data:

```markdown
# Worker Summary

## Task
{{ original prompt }}

## Status
─── DONE ───

## Files Changed
- `src/auth/login.ts` (created)
- `src/auth/types.ts` (modified)

## Changes Summary
Added login endpoint with JWT token generation...

## Notes for Next Stage
Authentication middleware ready. Frontend can now call /api/auth/login.
```

Parse this into structured context:
```python
def capture_stage_context(summary_file: Path) -> dict:
    content = summary_file.read_text()
    return {
        "summary": extract_section(content, "Changes Summary"),
        "files_changed": extract_file_list(content, "Files Changed"),
        "status": extract_status(content),
        "notes": extract_section(content, "Notes for Next Stage"),
    }
```

### Pipeline Persistence

Store pipeline state for resume-after-crash:

```yaml
# ~/.agentwire/pipelines/{pipeline_id}.yaml
pipeline: full-feature
started: 2024-01-15T10:30:00Z
variables:
  task_description: "Add user auth"
stages:
  implement:
    status: DONE
    summary_file: /path/to/summary.md
    files_changed: [src/auth/login.ts, src/auth/types.ts]
  test:
    status: RUNNING
    worker_pane: 2
    started: 2024-01-15T10:31:23Z
```

### Concurrency Control

Limit parallel stages to avoid resource exhaustion:

```yaml
pipelines:
  massive-refactor:
    max_parallel: 3  # At most 3 workers at once
    stages:
      - name: module-a
      - name: module-b
      - name: module-c
      - name: module-d
      # First 3 run in parallel, module-d waits
```

## Built-in Pipelines

Ship useful defaults:

| Pipeline | Stages | Use Case |
|----------|--------|----------|
| `feature` | implement → test → PR | Standard feature work |
| `bugfix` | investigate → fix → test → PR | Bug fixes |
| `refactor` | analyze → implement → test | Safe refactoring |
| `security-patch` | audit → fix → test → review | Security updates |

```bash
# Use built-in pipeline
agentwire pipeline run feature --var description="Add logout button"
```

## Potential Challenges

1. **Stage boundary detection**: When is a stage "done"? Solution: Use existing idle detection + explicit success criteria in stage definition.

2. **Context size explosion**: Late stages have too much context from earlier stages. Solution: Summarization at each stage, configurable context window per stage.

3. **Partial failure recovery**: Stage 3 of 5 fails, what now? Solution: `on_error` handlers, ability to resume from failed stage, manual override to skip.

4. **Variable scoping confusion**: Which variables are available where? Solution: Clear namespacing (`vars.*`, `stages.*.summary`), good error messages for undefined refs.

5. **Worker type mismatch**: GLM worker can't handle nuanced stage. Solution: Per-stage role configuration, ability to specify worker type per stage.

6. **Pipeline sprawl**: Too many pipelines, hard to maintain. Solution: Pipeline inheritance/composition, shared stage definitions.

## Example Workflows

### Feature with Code Review Loop

```yaml
pipelines:
  reviewed-feature:
    stages:
      - name: implement
        roles: [glm-worker]
        prompt: "Implement {{ feature }}"

      - name: self-review
        roles: [claude-worker]
        prompt: |
          Review implementation: {{ stages.implement.summary }}
          Files: {{ stages.implement.files_changed }}
          List any issues or improvements needed.
        depends_on: implement

      - name: fix-issues
        roles: [glm-worker]
        prompt: |
          Fix these review issues: {{ stages.self-review.summary }}
        depends_on: self-review
        when: "{{ 'issues found' in stages.self-review.summary.lower() }}"

      - name: final-pr
        roles: [claude-worker]
        prompt: "Create PR for {{ feature }}"
        depends_on: [implement, fix-issues]
```

### Parallel Testing Strategy

```yaml
pipelines:
  comprehensive-test:
    stages:
      - name: unit-tests
        prompt: "Run unit tests"

      - name: integration-tests
        prompt: "Run integration tests"
        # Parallel with unit-tests

      - name: e2e-tests
        prompt: "Run e2e tests with browser"
        # Parallel with others

      - name: report
        prompt: |
          Compile test report:
          - Unit: {{ stages.unit-tests.summary }}
          - Integration: {{ stages.integration-tests.summary }}
          - E2E: {{ stages.e2e-tests.summary }}
        depends_on: [unit-tests, integration-tests, e2e-tests]
```

## Success Metrics

- Reduced orchestrator intervention during multi-stage work
- Pipeline completion rate (stages finishing successfully)
- Time from pipeline start to completion
- Reduction in "context loss" issues between stages
- User satisfaction with "walk away" workflows
