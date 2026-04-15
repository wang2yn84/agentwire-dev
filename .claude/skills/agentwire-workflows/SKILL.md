---
name: agentwire-workflows
description: Pi workflow engine — YAML-defined DAGs of pi invocations chained by Jinja2 templating + output extraction. Covers YAML anatomy (inputs, nodes, outputs, when, retries, on_error), CLI (`agentwire workflow list/validate/run/history/show`), MCP tools (`workflow_list/validate/run/history/show`), storage layout (metadata.json, context.json, per-node event logs), and debugging. Use when authoring a new workflow YAML, debugging a failed workflow run, explaining workflow concepts, or choosing between a workflow and a scheduler task. Full reference lives in docs/workflows.md.
---

# Pi workflow engine — quick reference

Workflows live in `agentwire/workflows/examples/` (bundled) or `~/.agentwire/workflows/defs/` (user). Each node is a one-shot `pi -p --mode json` invocation.

## Minimum viable YAML

```yaml
name: hello
nodes:
  greet:
    prompt: "Say hi in one word."
    model: glm-4.7-flash
    tools: [read]
    thinking: "off"
```

## Everything the runner honors

- **Templating** — Jinja2 with `StrictUndefined` in `prompt:` and in workflow-level `when:` expressions (no `{{ }}` for `when`).
- **DAG** — `depends_on: [...]` (string or list). Topological sort + cycle detection at validate time.
- **Inputs** — declared under workflow-level `inputs:` with `type` (string|int|float|bool|json), `required`, `default`. Passed via `--input KEY=VAL` or `--input-file path.json`.
- **Output extraction** — per-node `outputs:` with `source: text | regex | jsonpath`. JSONPath subset: `$.a`, `$.a.b`, `$.a[*]`, `$.a[*].b`, `$.a[0]`. Nodes without declared outputs expose `{{ node_id.text }}`.
- **Retries** — `retries: N` (default 0), `retry_delay: S` (default 10). Triggered on status in `{failure, timeout}` only. Template/extraction errors never retry.
- **on_error** (after retries exhausted) — `fail` (halt) | `continue` (downstream gets `None` outputs, workflow → partial) | `branch` (requires `on_error_goto`; skip normal dependents, rescue target).
- **Skipping** — `when:` false OR upstream skipped/branched → node is `skipped`; dependents propagated unless rescued by a branch `on_error_goto`.
- **Tools** — subset of `{read, bash, edit, write, grep, find, ls}`. Default `[read, bash, edit, write]`. Empty list → `--no-tools`.
- **Thinking** — `off | minimal | low | medium | high | xhigh`. Default `medium`. Use `off` for flash-tier cheap nodes.

## CLI

```bash
agentwire workflow list [--json]
agentwire workflow validate <name-or-path>
agentwire workflow run <name> [--input KEY=VAL]... [--input-file FILE] [--dry-run] [--verbose] [--json]
agentwire workflow history [--workflow NAME] [--limit N] [--json]
agentwire workflow show <run-id> [--events] [--node ID] [--json]
```

`run` exits 0 on success|partial, 1 on failure.

## MCP

```
workflow_list()
workflow_validate(name_or_path)
workflow_run(name, inputs={}, dry_run=False)      # 600s timeout
workflow_history(workflow=None, limit=20)
workflow_show(run_id)
```

Agents should prefer MCP tools over shelling out.

## Storage

Runs live at `~/.agentwire/workflows/runs/<run-id>/`:
- `metadata.json` — workflow, status, inputs, node summaries (schema_version: 1)
- `context.json` — final inputs + per-node extracted outputs
- `nodes/<id>.events.jsonl` — raw pi JSONL stream

Runs without `metadata.json` (crashed mid-run) are silently hidden from `history`.

## Common authoring pitfalls

- **Typo in `{{ analyze.issues }}`** → StrictUndefined raises. Check the node's `outputs:` names.
- **`source: jsonpath` but model emits fences** — extractor handles ` ```json ... ``` ` fences automatically; if extraction still fails, tighten the prompt ("no fences, no prose") and lower `thinking`.
- **on_error: branch without on_error_goto** — validate catches this. Target must exist as a node in the same workflow.
- **`when: "{{ x == 'y' }}"`** — wrong. `when:` is a Jinja *expression*, not a template. Write `when: "x == 'y'"`.
- **Adding MCP tool doesn't appear** — requires `agentwire rebuild && agentwire portal restart --dev` *plus* a Claude session restart.

## When to pick which tool

| Need | Tool |
|---|---|
| Single interactive prompt | `claude` / `pi -p` |
| Recurring prompt on a schedule | `agentwire-scheduler` skill |
| Multi-step logic, variables between steps, conditionals, retries | workflows |

## Full reference

`docs/workflows.md` — concept intro, full YAML anatomy, per-field semantics, debugging guide, FAQ.

## Mission

Phase 2 status + roadmap lives in `docs/missions/pi-workflow-engine.md`. Sub-missions for later phases: `pi-scheduler-workflows.md` (Phase 3), `pi-workflow-advanced.md` (Phase 4), `pi-workflow-ui.md` (Phase 5).
