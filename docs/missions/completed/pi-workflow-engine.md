> Living document. Update this, don't create new versions.

# Mission: Phase 2 — Pi Workflow Engine

Build a programmable workflow system where pi invocations are nodes in a directed graph. Each node is a one-shot `pi -p --mode json` call with structured inputs and outputs. Nodes chain into DAGs for complex automation that's impossible with today's single-prompt scheduler tasks.

**Phase of:** `pi-harness-overview.md`
**Status:** complete (code shipped 2026-04-14, v1.22.0)
**Estimated effort:** 2–3 weeks (actual: ~1 week)
**Depends on:** Phase 1 (validates pi invocation path, config, install)
**Blocks:** Phase 3 (scheduler workflows), Phase 4 (advanced patterns), Phase 5 (UI)

## Goal

Ship `agentwire workflow run <name>` that executes a YAML-defined DAG of pi invocations, with inputs/outputs flowing between nodes, conditional branching, retries, and full event log for debugging.

## Why This Unlocks Something New

**Today:** Scheduler tasks are one prompt. Complex logic lives inside that prompt as natural-language instructions. Debugging = guessing. Conditionals = hoping the model interprets correctly.

**With workflows:** Each step is discrete, testable, cheap (pi minimal tool surface), and observable (JSONL event stream). You compose small reliable nodes instead of praying a giant prompt works.

## Scope

### In Scope

- New module: `agentwire/workflows/`
- Node abstraction (`ActionNode`, `ConditionalNode`, `ParallelNode`)
- Pi runner: subprocess wrapper around `pi -p --mode json --no-session`
- JSONL event stream parser → structured outputs
- Jinja2-style template rendering for prompts (`{{ var }}`)
- YAML workflow definition format + validator
- DAG executor with topological sort + dependency resolution
- CLI commands: `agentwire workflow {list, run, show, validate}`
- Persistent run storage: `~/.agentwire/workflows/runs/<id>/events.jsonl`
- Per-node timeout, retry, on_error handling
- Variable extraction via JSON path (node outputs → workflow context)

### Out of Scope (Later Phases)

- Scheduler integration (Phase 3)
- Parallel fan-out / fan-in with join barriers (Phase 4)
- Human-in-the-loop pause/resume (Phase 4)
- Cost circuit breakers (Phase 4)
- Workflow canvas UI (Phase 5)
- Remote workflow execution across machines (deferred)

## Approach

### Module Layout

```
agentwire/workflows/
├── __init__.py
├── node.py           # Node dataclasses and validators
├── pi_runner.py      # Pi subprocess wrapper + JSONL parser
├── runner.py         # DAG executor
├── context.py        # Template rendering + variable store
├── definitions.py    # YAML parser and schema validator
├── storage.py        # Run persistence (events, outputs, metadata)
└── cli.py            # agentwire workflow command handlers
```

### Node Abstraction

```python
@dataclass
class ActionNode:
    id: str                          # Unique within workflow
    prompt: str                      # Jinja2 template
    provider: str = "zai"
    model: str = "glm-5"
    tools: list[str] = field(default_factory=lambda: ["read", "bash", "edit", "write"])
    thinking: str = "medium"         # off | minimal | low | medium | high | xhigh
    
    depends_on: list[str] = field(default_factory=list)
    when: str | None = None          # Jinja2 expression for conditional
    
    outputs: list[OutputSpec] = field(default_factory=list)
    timeout: int = 300               # Seconds
    retries: int = 0
    retry_delay: int = 10
    on_error: Literal["fail", "continue", "branch"] = "fail"
    on_error_goto: str | None = None # Node id to jump to on error
    
    workdir: str | None = None       # cwd for pi (default: workflow cwd)
    extra_env: dict[str, str] = field(default_factory=dict)

@dataclass
class OutputSpec:
    name: str                        # Variable name in context
    source: Literal["text", "regex", "jsonpath", "tool_result"]
    pattern: str                     # Regex or JSONPath expression
    required: bool = True            # Fail node if extraction fails?
```

### Pi Runner Contract

```python
def run_node(node: ActionNode, context: Context) -> NodeResult:
    """Execute one node. Synchronous. Streams JSONL to file."""
    
    prompt = context.render(node.prompt)
    
    cmd = [
        "pi", "-p", prompt,
        "--provider", node.provider,
        "--model", node.model,
        "--tools", ",".join(node.tools),
        "--thinking", node.thinking,
        "--mode", "json",
        "--no-session",
    ]
    
    env = {**os.environ, "ZAI_API_KEY": get_zai_key(), **node.extra_env}
    
    events = []
    with open(event_log_path, "w") as log:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, env=env,
                                cwd=node.workdir or context.cwd)
        for line in proc.stdout:
            log.write(line.decode())
            event = json.loads(line)
            events.append(event)
            # Emit progress event to CLI/UI...
    
    # Extract outputs per node.outputs spec
    outputs = extract_outputs(events, node.outputs)
    
    return NodeResult(
        node_id=node.id,
        status="success" if proc.returncode == 0 else "failure",
        outputs=outputs,
        events=events,
        final_text=extract_final_assistant_message(events),
        tool_calls=extract_tool_calls(events),
        files_modified=extract_file_changes(events),
        tokens_used=extract_token_usage(events),
        duration_ms=...,
    )
```

### Workflow Definition (YAML)

```yaml
name: refactor-and-verify
description: Analyze file, apply refactorings, verify via tests
version: 1

inputs:
  file:
    type: string
    required: true
    description: Path to file to refactor

nodes:
  analyze:
    prompt: |
      Read {{ inputs.file }} and identify refactoring opportunities.
      Output JSON: {"issues": [{"description": "...", "severity": "high|medium|low"}]}
    tools: [read, grep]
    thinking: high
    outputs:
      - name: issues
        source: jsonpath
        pattern: $.issues[*]

  refactor:
    depends_on: [analyze]
    prompt: |
      Apply these refactorings to {{ inputs.file }}:
      {% for issue in analyze.issues %}
        - {{ issue.description }} ({{ issue.severity }})
      {% endfor %}
    tools: [read, edit]
    retries: 2
    timeout: 600

  verify:
    depends_on: [refactor]
    prompt: "Run tests and report pass/fail"
    tools: [bash]
    outputs:
      - name: test_status
        source: regex
        pattern: "(PASS|FAIL)"

  rollback:
    depends_on: [verify]
    when: "{{ verify.test_status == 'FAIL' }}"
    prompt: "Git reset --hard HEAD to undo refactorings"
    tools: [bash]

outputs:
  - verify.test_status
  - refactor.files_modified
```

### DAG Executor

- Topological sort of nodes by `depends_on`
- Sequential execution initially (parallel in Phase 4)
- Context object carries: `inputs`, per-node outputs, `env` (global variables)
- When a node has `when`, evaluate expression against current context; skip if false
- On `on_error: fail`, halt workflow; on `continue`, proceed; on `branch`, jump to `on_error_goto`
- Emit lifecycle events: workflow_start, node_start, node_tool_call, node_end, workflow_end

### CLI Commands

```bash
# List available workflows
agentwire workflow list
agentwire workflow list --json

# Validate YAML without running
agentwire workflow validate <path-or-name>

# Run workflow with inputs
agentwire workflow run refactor-and-verify --input file=src/api.ts
agentwire workflow run <name> --input-file inputs.json
agentwire workflow run <name> --dry-run   # Print execution plan only

# Inspect past runs
agentwire workflow history
agentwire workflow show <run-id>
agentwire workflow show <run-id> --events  # Full JSONL replay
agentwire workflow show <run-id> --node analyze  # Just one node
```

### Storage

```
~/.agentwire/workflows/
├── defs/                        # Workflow YAML files (discoverable)
│   ├── refactor-and-verify.yaml
│   └── ...
└── runs/
    └── <workflow>-<timestamp>-<id>/
        ├── metadata.json        # Inputs, start/end, status
        ├── context.json         # Final context (all variables)
        ├── events.jsonl         # Combined event log across all nodes
        └── nodes/
            ├── analyze.events.jsonl
            ├── refactor.events.jsonl
            └── ...
```

## Files to Change

| File | Changes |
|------|---------|
| `agentwire/workflows/__init__.py` | Module init, public API |
| `agentwire/workflows/node.py` | Node dataclasses |
| `agentwire/workflows/pi_runner.py` | Pi subprocess + JSONL parser |
| `agentwire/workflows/runner.py` | DAG executor |
| `agentwire/workflows/context.py` | Jinja2 rendering, variable store |
| `agentwire/workflows/definitions.py` | YAML loader + JSON schema validator |
| `agentwire/workflows/storage.py` | Run persistence |
| `agentwire/workflows/cli.py` | CLI handlers for `agentwire workflow *` |
| `agentwire/__main__.py` | Wire `workflow` subcommand into argparse |
| `agentwire/mcp_server.py` | Add `workflow_run` / `workflow_list` MCP tools |
| `docs/workflows.md` | Full developer docs |
| `workflows/examples/` | Ship 3–5 example workflows in repo |
| `tests/workflows/test_*.py` | Unit + integration tests |

## Success Criteria

- [ ] `agentwire workflow run <name>` executes a simple 3-node workflow end-to-end
- [ ] Jinja2 templating works: node B receives node A's outputs
- [ ] `when` conditionals correctly skip nodes
- [ ] `retries` actually retries on pi failure
- [ ] Event log is replayable: can reconstruct workflow state from `events.jsonl`
- [ ] Dry-run mode prints execution plan without running pi
- [ ] At least one example workflow ships in repo (`refactor-and-verify`)
- [ ] MCP tools work: orchestrator sessions can trigger workflows
- [ ] Documentation explains: how to write a workflow, how to test one, how to debug a failed run
- [ ] Workflow YAML schema is versioned (`version: 1`) and validated

## Testing Plan

### Unit Tests
- Node dataclass validation (required fields, enum checks)
- OutputSpec extraction: regex, jsonpath, text patterns
- Template rendering: variables, loops, conditionals
- DAG topological sort
- Dependency cycle detection (should raise)

### Integration Tests (with real pi + Z.AI)
- 2-node workflow: analyze → report
- 3-node workflow with conditional: analyze → refactor → (verify | rollback)
- Retry behavior: force a failure, verify retry happens
- Timeout behavior: set low timeout, verify kill + cleanup
- Output extraction: JSON response → parsed into context

### Example Workflows (ship with repo)
1. **refactor-and-verify** — as above
2. **dependency-audit** — scan package.json, check versions, open issues
3. **doc-drift-check** — compare CLAUDE.md claims against actual code
4. **pr-triage** — read PR diff, generate review comment
5. **test-recovery** — when test fails, diagnose → propose fix → verify

## Open Questions

- **Templating engine:** Jinja2 is heavy — consider minijinja or a lightweight alternative. But devs know Jinja, so familiarity wins.
- **Parallel execution:** Ship sequential first. Parallel needs asyncio refactor — Phase 4.
- **Secrets:** How do nodes access secrets (API keys, tokens)? Pass via `extra_env`, source from config/env.
- **Streaming output:** Should CLI stream JSONL as it runs, or batch-display at end? Stream with live progress.
- **Fork within workflow:** Can a node spawn a pi session with context from earlier? Yes — use `--session <file>` with copied JSONL.
- **Cross-workflow coordination:** Can workflow A trigger workflow B? Phase 4 concern.
- **State between runs:** Do workflows have persistent state (e.g., "last seen issue"), or are they stateless? Stateless by default; persistence via writing to files is allowed.

## Risk Mitigation

- **Pi stability:** Pi is pre-1.0. If JSONL schema changes, parser breaks. Mitigation: pin pi version in docs, add schema version check, warn on unknown event types.
- **Token cost runaway:** A workflow with many retries + xhigh thinking could get expensive. Mitigation: per-run cost accumulator, abort if exceeds config cap (Phase 4).
- **Runaway bash:** Pi's `bash` tool runs anything. Workflows triggered by scheduler need same damage-control hooks Claude Code uses. Mitigation: run pi nodes under same safety scripts, or add `--bash-deny` via extension.

## Prior Art

- **n8n** — node-based workflow automation, too GUI-centric
- **Temporal** — robust but too heavy for dev workflows
- **GitHub Actions** — YAML DAGs, good reference for syntax decisions
- **Prefect** — Python workflow orchestration, good for DAG patterns
- **Langgraph** — LLM-specific DAGs, closest conceptual match

We're building a minimal version optimized for pi specifically. Don't reinvent what we don't need.
