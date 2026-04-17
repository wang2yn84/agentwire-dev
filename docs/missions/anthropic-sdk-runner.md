> Living document. Update this, don't create new versions.

# Mission: Phase 6 — Agent-SDK Workflow Runner (parallel to pi)

Add a second, Anthropic-SDK-backed node executor alongside `pi_runner.py`. Workflows declare a `runner:` per node (or inherit workflow-level default); the DAG engine, storage, templating, and scheduler integration stay unchanged. Let real-world usage over weeks reveal which runner wins for which node type — *without* ripping out pi.

**Phase of:** `pi-harness-overview.md` (extends — not replaces — the pi path)
**Status:** planned — **prioritized ahead of Phase 4 and Phase 5**
**Estimated effort:** 4–6 weeks
**Depends on:** Phase 2 (workflow engine), Phase 3 (scheduler integration)
**Blocks:** Phase 4 (advanced patterns) and Phase 5 (desktop UI) — both should land on top of a runner-agnostic abstraction, so they wait until this phase reaches feature parity

**Strategic note (2026-04-16)**: After Anthropic confirmed subscription-mode Agent SDK usage is supported, we're reordering: Phase 6 (this mission) comes next, Phase 4 + 5 are explicitly gated on it. Rationale — every Phase 4 feature (parallelism, loops, cost gates, HITL) must work uniformly across runners, and building them pi-only would force a retrofit later.

---

## Context

- Phase 1–3 shipped pi → Z.AI as the primary LLM harness inside the workflow engine because we believed Anthropic was blocking Agent-SDK usage on subscription plans.
- **2026-04-16**: Anthropic publicly confirmed on X that subscription usage of the Agent SDK is supported and intended long-term.
- The workflow engine itself (DAG, Jinja2, output extraction, storage, scheduler dispatch) is already *ours*. The only place we hand control off is `pi_runner.run_node()` → the `pi` binary.
- Replacing just that layer unlocks native Python observability, in-process event streaming, mid-run control hooks, and optional MCP-from-inside-a-node — without losing pi (which still handles multi-provider + Z.AI subscription economics).

## Goal

Ship `agentwire/workflows/anthropic_runner.py` so a workflow YAML can say:

```yaml
nodes:
  cheap_scan:
    runner: pi                # default — glm-5.1 via Z.AI subscription
    prompt: "…"
  deep_reason:
    runner: anthropic         # Claude via Agent SDK, subscription-backed
    model: sonnet
    thinking: high
    prompt: "…"
```

…and have both paths produce byte-compatible `NodeResult` objects, so every downstream consumer (`runner.py`, `storage.py`, `context.py`, the morning report, the portal) works unchanged.

## Why Both, Not One

| Dimension | pi runner | Anthropic-SDK runner |
|---|---|---|
| **Cost / subscription** | Z.AI plan covers glm-5.1 in full | Anthropic plan |
| **Multi-provider** | Yes (zai, anthropic, openai, google, deepseek, …) | Claude-only |
| **Subprocess overhead** | 500ms–1s per node | Near-zero (in-process) |
| **Observability** | Parse JSONL after the fact | Live Python callbacks |
| **Tool customization** | 4 fixed tools from pi | Register any tools + custom behavior |
| **Damage-control bash** | Pi's bash bypasses our hooks | Native — tool wraps our damage-control |
| **MCP inside nodes** | No (pi intentionally skips MCP) | Yes — direct `mcp__agentwire__*` calls |
| **Mid-run control** | Can only inspect outputs | Can budget-cap, cancel, branch adaptively |
| **Maintenance** | Someone else owns the CLI | We own the SDK wrapping |

No single answer wins on every axis. That's why we build both and compare.

---

## Design

### 1. Runner abstraction

Define a tiny protocol:

```python
# agentwire/workflows/runners/__init__.py

class NodeRunner(Protocol):
    name: str               # "pi" | "anthropic"
    def run(self, node: ActionNode, context: Context,
            event_log_path: Path | None) -> NodeResult: ...
```

`pi_runner.run_node` moves to `agentwire/workflows/runners/pi.py` (thin rename + wrap). New `runners/anthropic.py` implements the same protocol.

A registry keeps it extensible:

```python
# agentwire/workflows/runners/__init__.py
RUNNERS: dict[str, NodeRunner] = {
    "pi": PiRunner(),
    "anthropic": AnthropicRunner(),
}
```

`workflows/runner.py` resolves the runner by name per node; no other engine code changes.

### 2. YAML: `runner:` field

- Per-node override: `runner: anthropic`
- Workflow-level default: top-level `runner: anthropic` applies to nodes that don't set their own
- Global default: `pi` (backwards-compatible — every existing workflow keeps working with no edits)

`definitions.py` validates: unknown runner name → parse error.

### 3. Anthropic-SDK runner

`agentwire/workflows/runners/anthropic.py`:

```python
from anthropic import Anthropic

class AnthropicRunner:
    name = "anthropic"

    def run(self, node, context, event_log_path):
        client = Anthropic()
        tools = self._build_tools(node.tools)          # read/write/edit/bash/grep/find/ls
        system = self._compose_system_prompt(node)     # CLAUDE.md + role prompts
        model = self._resolve_model(node.model)        # sonnet/opus/haiku -> full id
        
        events = []
        tool_calls = []
        final_text = ""

        # Stream + capture events for JSONL parity
        with client.messages.stream(
            model=model,
            system=system,
            tools=tools,
            max_tokens=4096,
            thinking=self._thinking(node.thinking),
            messages=[{"role": "user", "content": node.prompt}],
        ) as stream:
            for event in stream:
                events.append(self._serialize_event(event))
                # run tool calls + append tool_result messages as needed
                ...
            final_text = stream.get_final_text()
        
        # Write JSONL events to disk for parity with pi runner
        if event_log_path:
            self._persist_events(event_log_path, events)
        
        return NodeResult(
            node_id=node.id, status="success",
            final_text=final_text, tool_calls=tool_calls,
            tokens_used=self._tokens(events),
            duration_ms=..., events=events,
        )
```

### 4. Tools — isolated per runner to protect pi

**Decision (2026-04-16):** tool implementations are NOT shared between runners. Pi's bash / read / write / edit keep going through the `pi` binary exactly as they do today — zero behavioural change to pi. The Anthropic runner has its own standalone Python tool implementations in `agentwire/workflows/runners/anthropic_tools.py`:

```python
# agentwire/workflows/runners/anthropic_tools.py
def tool_read(path: str) -> str: ...
def tool_write(path: str, content: str) -> str: ...
def tool_edit(path: str, old_string: str, new_string: str) -> str: ...
def tool_bash(command: str, timeout: int = 60) -> dict:
    # Route through the same damage-control filter the CLI uses
    if is_blocked(command):
        return {"error": "blocked by damage-control", "stderr": "..."}
    ...
```

The damage-control win applies only to the Anthropic runner — it's not retroactively forced onto pi. If we want to add damage-control to pi's bash later, that's a separate, opt-in workstream and out of this mission's scope.

**Why isolation matters**: Phase 2 shipped the pi path working with pi's native tool set. Changing those tools risks regressions in existing workflows. Keeping them completely untouched preserves the pi path as a known-good baseline while we experiment with the SDK path.

### 5. Event serialization

To keep `storage.py` / `workflow show <run_id> --events` working identically, Anthropic-SDK streams get translated into the same JSONL shape pi emits (`session`, `agent_start`, `turn_start`, `message_start`, `message_end`, `turn_end`, `agent_end`). A small translator lives in `runners/anthropic_events.py`.

Tools, thinking blocks, token usage all map one-to-one with pi's schema. Portal and `workflow show` don't need to know which runner produced the events.

### 6. Output extraction

Unchanged. `outputs.extract_outputs(events, output_specs)` operates on the unified event shape; extractors don't care which runner emitted it.

### 7. Observability — MVP scope

**Decision (2026-04-16):** the basic observability wins are trivial because the SDK already emits structured events. Pull the first four features into MVP; defer the harder ones.

Shipped in MVP (week 1-2):

| Feature | How |
|---|---|
| Structured Python logging per node | Standard `logging` with run_id / node_id in extra |
| Live event callback — every tool call, thinking block, text chunk | SDK streams them; expose as `AnthropicRunner(on_event=callback)` |
| `--verbose` CLI flag: print events live as they happen | Hook the callback; `cli.py` already has the flag for pi, extend to Anthropic |
| Cost accumulator per run | Sum `usage.input_tokens * rate + usage.output_tokens * rate` across node results |

Stretch for Phase 6 (week 4+, after canary):

| Feature | Scope |
|---|---|
| **Basic live UI for workflow events** | New portal endpoint — Server-Sent Events streaming the event callback to a simple HTML page that scrolls as the run progresses. **Text-only, no graph, no canvas.** Explicitly NOT the Phase 5 canvas. |

**Explicitly deferred — needs further design discussion before we commit:**

- **Mid-run control** (abort if cost > cap, switch model adaptively on retry, cancel cleanly) → Phase 6 stretch or Phase 4. Hard — decision points include: what triggers an abort? can a node be resumed? how does cost propagate to the workflow-level decision? User flagged these as needing more discussion before work starts.
- **Full portal canvas** with node-status colors, live DAG rendering, replay scrubber → **Phase 5**. The basic tailing UI above is intentionally not a stepping stone toward this — the canvas is a separate, richer product surface.

```python
class AnthropicRunner:
    def __init__(self, on_event: Callable[[dict], None] | None = None): ...

# Usage (from workflows/runner.py):
def _on_event(event):
    _log_structured(event, run_id=..., node_id=...)
    if cli_args.verbose:
        _print_event_line(event)
    _update_cost_tally(event)
```

### 8. Scheduler integration

Zero changes to `scheduler.py`. Workflow tasks still just call `run_workflow(wf, runs_dir, inputs)` — the runner selection happens entirely inside the workflow engine, invisible to the scheduler.

---

## Files to Change

| File | Change |
|---|---|
| `agentwire/workflows/runners/__init__.py` | New — runner registry |
| `agentwire/workflows/runners/pi.py` | New — thin wrapper around existing `pi_runner.py` (pi behaviour unchanged) |
| `agentwire/workflows/runners/anthropic.py` | New — SDK-backed node executor |
| `agentwire/workflows/runners/anthropic_tools.py` | New — Anthropic-runner-only tool implementations (read/write/edit/bash with damage-control) |
| `agentwire/workflows/runners/anthropic_events.py` | New — event-shape translator (SDK events → pi-shaped JSONL) |
| `agentwire/workflows/runners/anthropic_capabilities.py` | New — model → supported settings table; used by validator and runtime |
| `agentwire/workflows/pi_runner.py` | Leave in place as a shim for one release cycle, then delete |
| `agentwire/workflows/node.py` | Add `runner: str \| None` on `ActionNode` (default None → workflow default → "pi") |
| `agentwire/workflows/definitions.py` | Parse top-level `runner:` + per-node `runner:`; validate against registry |
| `agentwire/workflows/runner.py` | Resolve runner from registry per node; thread event callback |
| `agentwire/workflows/storage.py` | Record which runner produced each run in `metadata.json` |
| `pyproject.toml` | Add `anthropic>=0.40.0` (or whatever ships current Agent SDK) to deps |
| `docs/workflows.md` | New "Runners" section — when to pick which |
| `docs/missions/anthropic-sdk-runner.md` | This file (mission) |
| `.claude/skills/agentwire-workflows/SKILL.md` | Mention per-node `runner:` field |
| `tests/unit/test_workflows.py` | Runner registry, validation, default-propagation |
| `tests/integration/test_anthropic_runner.py` | New — smoke-test against real SDK with a trivial prompt |

---

## Success Criteria

- [ ] Existing 7 example workflows run identically under the pi runner — zero YAML edits required, zero pi behavioural changes
- [ ] A new `workflows/examples/claude-reasoning.yaml` showcases the Anthropic runner with `runner: anthropic`
- [ ] `workflow show <run_id>` output is visually identical whether the run was pi or anthropic (modulo provider/model names in the metadata)
- [ ] Portal morning report surfaces which runner each workflow used (small badge next to `workflow:` label)
- [ ] Pi's tool path stays completely untouched (no shared Python tool code; pi binary still runs its own tools)
- [ ] **`daily-book-report.compose_and_send` converted to `runner: anthropic`** (canary — the heaviest reasoning node on a live scheduled task) and running for at least 2 weeks without regression
- [ ] Basic observability landed in MVP: structured logging, live event callback, `--verbose` live event stream, per-run cost accumulator
- [ ] Comparison happens naturally across weeks of real usage — user feedback captured in this doc, no formal A/B benchmark workflow needed

---

## Testing Plan

### Unit
- Runner registry: unknown name raises, registered names resolve
- `ActionNode.runner` default cascade: per-node > workflow > global default "pi"
- Event translator: golden-file comparison between pi JSONL and translated Anthropic JSONL for the same prompt (ignore timestamps / IDs)
- Tool implementations: bash damage-control coverage (assert `rm -rf /` is blocked, assert allowed commands succeed)
- Shared output extractors unchanged — already covered by existing tests

### Integration (requires ANTHROPIC_API_KEY in env)
- `run_workflow(hello-world, runner: anthropic)` → success, final_text populated
- Tool-use roundtrip: node that must use `read` — verify tool call appears in events, tool_result is fed back
- Retry on transient 529/overloaded — retries trigger, final attempt recorded
- Thinking budgets: `thinking: high` produces thinking blocks; `thinking: off` does not
- Cost accumulator across a 3-node workflow matches sum of per-node token costs

### Manual QA
- **First canary**: flip `daily-book-report.compose_and_send` to `runner: anthropic` once MVP feature parity is reached. It's the heaviest reasoning node on a live scheduled task, and Vanessa sees the output daily — any regression surfaces fast.
- Run for ≥2 weeks in production. Capture notes here on: output quality vs glm-5.1, latency, per-run cost, any reliability issues.
- No formal benchmark workflow — comparison happens naturally across the set of workflow tasks the user runs.

---

## Locked decisions (not open — user-directed 2026-04-16)

- **Model strings**: always use the full proper Anthropic model ID — `claude-sonnet-4-6`, `claude-opus-4-7`, `claude-haiku-4-5-20251001`. No alias table, no `sonnet`/`opus`/`haiku` shorthand. Full strings in YAML, config, code, docs. Explicit, easy to update, no ambiguity about which exact model ran.
- **Node settings on Anthropic-runner nodes** (fail-fast, no magic fallbacks). The SDK surface shipped April 16, 2026 with Opus 4.7 is:
  - `thinking: {type: "adaptive"}` — Claude decides when and how much to think. No token budget. Opus 4.7 additionally supports `{type: "adaptive", display: "summarized"}` to restore visible thinking output.
  - `output_config.effort: "low" | "medium" | "high" | "xhigh" | "max"` — controls thinking depth and overall token spend. `xhigh` is Opus 4.7-only, `max` is Opus-tier only, and `effort` **errors on Haiku 4.5** entirely.
  - `output_config.task_budget: {type: "tokens", total: N}` — beta (header `task-budgets-2026-03-13`), Opus 4.7 only, minimum 20000. Hard ceiling on token spend across an agentic loop.
  - `thinking: {type: "enabled", budget_tokens: N}` is **fully removed on Opus 4.7** (returns 400) and deprecated on Opus 4.6 / Sonnet 4.6. Gone as a concept for new code targeting 4.6/4.7 family.

  **YAML schema for Anthropic-runner nodes**:

  ```yaml
  nodes:
    deep_reason:
      runner: anthropic
      model: claude-opus-4-7             # full proper string — no aliases

      # All three settings below are optional. Omitted = API default.
      thinking: { type: adaptive }       # or { type: disabled } | { type: adaptive, display: summarized }
      effort: xhigh                      # low | medium | high | xhigh | max
      task_budget: { tokens: 40000 }     # Opus 4.7 only, min 20000 (beta)
  ```

  **Validation policy — strict, at parse time.** Bad combinations are caught at `agentwire workflow validate` and at `agentwire scheduler board` load, before a single node runs. No silent coercion, no "warn and continue" — errors surface with the exact setting that's wrong and why. A small capability table at `agentwire/workflows/runners/anthropic_capabilities.py` is consulted by both the validator and the runtime:

  | Setting | Requires | Error if violated |
  |---|---|---|
  | `effort: max` | Opus-tier (`claude-opus-*`) | `"effort: max requires claude-opus-*, got {model}"` |
  | `effort: xhigh` | Opus 4.7 specifically | `"effort: xhigh requires claude-opus-4-7, got {model}"` |
  | `effort: any` | Not Haiku 4.5, not Sonnet 4.5 | `"effort param not supported on {model}, omit it"` |
  | `task_budget` | Opus 4.7 + beta header | `"task_budget requires claude-opus-4-7 (beta: task-budgets-2026-03-13)"` |
  | `task_budget.tokens < 20000` | Always | `"task_budget.tokens minimum is 20000, got {N}"` |
  | `thinking: {type: enabled, budget_tokens: N}` | Pre-4.6 models only | `"budget_tokens removed on {model}, use thinking: {type: adaptive} + effort instead"` |

  **Runtime behaviour**: if a validation rule missed something and the Anthropic API returns 400, the `NodeResult.error` carries the verbatim API error message. No retry (deterministic failure). User sees a clean stack: "YAML → validator → Anthropic API 400 → fix YAML."

  **Pi-side `thinking: medium` strings are ignored by the Anthropic runner** — no translation attempted. If a workflow wants effort control on a Claude node, it declares `effort:` explicitly. Pi's short strings stay pi-only.

  **Why strict over lenient**: silent "warn and drop unsupported settings" is worse at scale — users who *thought* they enabled `effort: xhigh` and got free high-quality runs get surprised when they learn it was ignored. Errors at validation time (cheap, instant) are always better than errors at runtime (expensive, post-partial-work, maybe mid-DAG). The validator is the right place to catch this.
- **No kill-switch config**. Rollback path is: flip the offending node's `runner:` field back to `pi` (or delete it — `pi` is the default). One-line YAML edit. That's the whole rollback story.
- **Authentication**: uses the existing Anthropic Claude monthly subscription auth on this machine — the same plumbing the `claude` CLI uses. No `ANTHROPIC_API_KEY` env var, no config entry. The SDK picks up subscription auth from wherever Claude Code stores it. Exact plumbing confirmed as part of Week 1 scaffolding (non-blocking — just verify it works, no design decision).

## Open Questions

- **Agent SDK package name / version**: pin to the right `anthropic>=x.y.z` once we pick a first target version.
- **Retry on rate limit**: retry vs bail-out policy might differ from pi's. Start with pi's behaviour and tune from data.
- **Tool whitelist scoping per-session-type**: pi-zai-restricted / pi-zai-readonly map to pi's `--tools`. For the Anthropic runner, we apply the same whitelist at tool-registration time.
- **Pricing transparency**: Anthropic SDK usage reports tokens natively. Subscription-covered runs should still show tokens used (even if cost is $0) so we can reason about throughput. Surface in the morning report cost column alongside pi's.

---

## Risk Mitigation

- **SDK churn**: Anthropic's SDK surface changes. Pin version, follow their migration guides, keep an integration test that verifies streaming semantics.
- **Tool-use bugs are expensive**: A bad tool impl could let Claude escape the safe surface. Damage-control for bash is mandatory. Rollback for any production issue = flip the offending workflow's `runner:` back to `pi` (one-line YAML edit).
- **Observability overload**: Live event streams can swamp the portal. Start with server-side buffering + client-side throttling (same play we'd use in Phase 5 anyway).
- **Subscription policy reversal**: If Anthropic ever walks back subscription SDK usage, the `pi` path is still there. Rollback = flip workflows' `runner:` field back to `pi`.

---

## Rollout

1. **Week 1** — Scaffolding: runner registry, move `pi_runner.py` behind `runners/pi.py` shim, ship tests proving zero pi-behaviour change
2. **Week 1-2** — MVP Anthropic runner: single node, 4 tools, events translated to pi-shaped JSONL, hello-world passes. **Observability basics** land here: structured logging, live event callback, `--verbose` stream, cost accumulator
3. **Week 2-3** — Feature parity: retries on rate limits, thinking budgets mapped, output extraction hooked in, storage records `runner` field, tool whitelist honored for pi-zai-restricted/readonly equivalents
4. **Week 3-4** — Integration polish: morning report shows runner badge per row, `workflow show` surfaces runner, `agentwire workflow run <name> --runner anthropic` CLI override for testing
5. **Week 4+** — **Canary**: flip `daily-book-report.compose_and_send` to `runner: anthropic`. Run ≥2 weeks. Capture findings in this doc.
6. **Week 5-6 (stretch)** — Basic live UI: SSE endpoint + scrolling event page in the portal. Text-only, intentionally minimal — NOT the Phase 5 canvas.
7. **Week 6+** — Decide on Phase 4 and Phase 5 restart based on what we learned. Mid-run control + full canvas get their own design passes before work starts.

**Guardrails while this phase is in flight:**

- Phase 4 and Phase 5 work is paused until Phase 6 hits feature parity with pi
- Pi path is byte-for-byte unchanged the entire time
- Rollback for any production issue = flip the offending workflow's `runner:` field back to `pi` (one-line YAML edit — `pi` is the default runner so even deletion works)
- If the Anthropic path underdelivers, we keep pi as the default indefinitely — no forced migration
