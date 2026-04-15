"""Workflow DAG executor.

Runs nodes in topological order. For each node:
  1. Skip-check: was an upstream node skipped/branched past us?
  2. `when:` eval — Jinja expression. Falsy → skip (propagate to dependents).
  3. Render Jinja2 prompt (`{{ inputs.x }}`, `{{ upstream.var }}`).
  4. Run pi; retry on failure|timeout up to `retries` times with `retry_delay`.
  5. On success: extract outputs into context.
  6. On failure after retries: consult `on_error`:
       - fail     → halt workflow
       - continue → mark outputs None, keep going (dependents see Nones)
       - branch   → skip normal dependents, preserve `on_error_goto` target

Workflow status:
  success — every node succeeded
  partial — some nodes skipped or failed-continue, but nothing halted
  failure — a node with on_error=fail failed (execution halted)
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from agentwire.workflows import storage
from agentwire.workflows.context import Context
from agentwire.workflows.definitions import InputSpec, WorkflowDef, topological_sort
from agentwire.workflows.node import ActionNode, NodeResult
from agentwire.workflows.outputs import OutputExtractionError, extract_outputs
from agentwire.workflows.pi_runner import run_node


@dataclass
class WorkflowRun:
    """Result of running a workflow."""

    workflow: str
    run_id: str
    status: str  # "success" | "partial" | "failure"
    started_at: float
    duration_ms: int
    node_results: list[NodeResult] = field(default_factory=list)
    context: Context | None = None
    error: str | None = None


def _generate_run_id(workflow_name: str) -> str:
    ts = time.strftime("%Y%m%dT%H%M%S")
    return f"{workflow_name}-{ts}-{uuid.uuid4().hex[:8]}"


def _resolve_inputs(
    specs: list[InputSpec],
    provided: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Coerce + validate provided CLI inputs against declared specs."""
    errors: list[str] = []
    by_name = {s.name: s for s in specs}
    for key in provided:
        if key not in by_name:
            errors.append(f"unknown input: {key!r}")

    resolved: dict[str, Any] = {}
    for spec in specs:
        if spec.name in provided:
            try:
                resolved[spec.name] = spec.coerce(provided[spec.name])
            except Exception as e:
                errors.append(f"input[{spec.name}]: {e}")
        elif spec.default is not None:
            resolved[spec.name] = spec.default
        elif spec.required:
            errors.append(f"missing required input: {spec.name}")
        else:
            resolved[spec.name] = None
    return resolved, errors


def _build_dependents_map(nodes: list[ActionNode]) -> dict[str, list[str]]:
    """dep_id → [nodes that depend on dep_id]."""
    dependents: dict[str, list[str]] = {n.id: [] for n in nodes}
    for n in nodes:
        for dep in n.depends_on:
            dependents.setdefault(dep, []).append(n.id)
    return dependents


def _mark_transitive_skipped(
    start_id: str,
    dependents: dict[str, list[str]],
    skipped: set[str],
    rescue: set[str] | None = None,
) -> None:
    """BFS-mark everything downstream of `start_id` as skipped.

    Nodes in `rescue` are NOT marked (used by on_error=branch to preserve the
    fallback target). Their own dependents are also not propagated through the
    rescued node.
    """
    rescue = rescue or set()
    queue = list(dependents.get(start_id, []))
    while queue:
        nid = queue.pop(0)
        if nid in rescue or nid in skipped:
            continue
        skipped.add(nid)
        queue.extend(dependents.get(nid, []))


def _run_one_node_with_retries(
    node: ActionNode,
    rendered_prompt: str,
    context: Context,
    runs_dir: Path | None,
    run_id: str,
) -> NodeResult:
    """Invoke pi; retry on failure|timeout per node.retries / retry_delay."""
    attempts = 0
    result: NodeResult | None = None
    event_log_path: Path | None = None
    if runs_dir is not None:
        event_log_path = runs_dir / run_id / "nodes" / f"{node.id}.events.jsonl"

    rendered_node = replace(node, prompt=rendered_prompt)

    while True:
        attempts += 1
        result = run_node(
            rendered_node,
            workflow_cwd=context.cwd,
            event_log_path=event_log_path,
        )
        if result.status == "success":
            break
        if result.status not in ("failure", "timeout"):
            break
        if attempts > node.retries:
            break
        time.sleep(node.retry_delay)

    assert result is not None
    result.attempts = attempts
    return result


def run_workflow(
    workflow: WorkflowDef,
    runs_dir: Path | None = None,
    dry_run: bool = False,
    inputs: dict[str, Any] | None = None,
) -> WorkflowRun:
    """Execute a workflow end-to-end."""
    errors = workflow.validate()
    if errors:
        return WorkflowRun(
            workflow=workflow.name,
            run_id="",
            status="failure",
            started_at=time.time(),
            duration_ms=0,
            error="; ".join(errors),
        )

    resolved_inputs, input_errors = _resolve_inputs(workflow.inputs, inputs or {})
    if input_errors:
        return WorkflowRun(
            workflow=workflow.name,
            run_id="",
            status="failure",
            started_at=time.time(),
            duration_ms=0,
            error="; ".join(input_errors),
        )

    ordered_nodes = topological_sort(workflow.nodes)
    dependents = _build_dependents_map(workflow.nodes)
    context = Context(inputs=resolved_inputs)

    run_id = _generate_run_id(workflow.name)
    started_at = time.time()
    started_mono = time.monotonic()

    if dry_run:
        plan_results = [
            NodeResult(
                node_id=n.id,
                status="success",
                final_text=f"[dry-run] would execute node {n.id!r} "
                           f"(depends_on={n.depends_on or '[]'}"
                           + (f", when={n.when!r}" if n.when else "")
                           + ")",
            )
            for n in ordered_nodes
        ]
        return WorkflowRun(
            workflow=workflow.name,
            run_id=run_id,
            status="success",
            started_at=started_at,
            duration_ms=0,
            node_results=plan_results,
            context=context,
        )

    skipped: set[str] = set()
    results_by_id: dict[str, NodeResult] = {}
    overall_status = "success"
    overall_error: str | None = None
    halted = False

    for node in ordered_nodes:
        # 1. Was this node already marked skipped by an upstream event?
        if node.id in skipped:
            results_by_id[node.id] = NodeResult(
                node_id=node.id,
                status="skipped",
                final_text="",
                error="upstream skipped or branched",
            )
            overall_status = "partial"
            continue

        # 2. when: conditional. Eval errors are treated as node failures
        # (not retryable — a bad expression won't fix itself).
        if node.when:
            try:
                active = context.eval_condition(node.when)
            except Exception as e:
                results_by_id[node.id] = NodeResult(
                    node_id=node.id,
                    status="failure",
                    final_text="",
                    error=f"when expression error: {e}",
                )
                overall_status = "failure"
                overall_error = f"node[{node.id}] when error: {e}"
                halted = True
                break
            if not active:
                _mark_transitive_skipped(node.id, dependents, skipped)
                results_by_id[node.id] = NodeResult(
                    node_id=node.id,
                    status="skipped",
                    final_text="",
                    error=f"when={node.when!r} → false",
                )
                overall_status = "partial"
                continue

        # 3. Render prompt (not retryable on failure).
        try:
            rendered_prompt = context.render(node.prompt)
        except Exception as e:
            results_by_id[node.id] = NodeResult(
                node_id=node.id,
                status="failure",
                final_text="",
                error=f"prompt template error: {e}",
            )
            overall_status = "failure"
            overall_error = f"node[{node.id}] template error: {e}"
            halted = True
            break

        # 4. Run pi with retry loop.
        result = _run_one_node_with_retries(
            node, rendered_prompt, context, runs_dir, run_id
        )
        results_by_id[node.id] = result

        if result.status == "success":
            # 5. Extract outputs for downstream nodes.
            if node.outputs:
                try:
                    values, soft_errors = extract_outputs(node.outputs, result)
                    context.set_node_outputs(node.id, values)
                    if soft_errors:
                        result.error = (
                            (result.error + "; " if result.error else "")
                            + "optional output failures: "
                            + "; ".join(soft_errors)
                        )
                except OutputExtractionError as e:
                    result.status = "failure"
                    result.error = f"output extraction failed: {e}"
                    # Extraction failure is a hard failure — consult on_error
                    # using the same policy as pi failures.
                    _apply_failure_policy = True
                else:
                    _apply_failure_policy = False
            else:
                context.set_node_outputs(node.id, {"text": result.final_text})
                _apply_failure_policy = False

            if not _apply_failure_policy:
                continue

        # 6. Node failed even after retries (or extraction failed). on_error policy:
        if node.on_error == "fail":
            overall_status = "failure"
            overall_error = (
                f"node[{node.id}] failed after {result.attempts} attempt(s): "
                f"{result.error}"
            )
            halted = True
            break

        if node.on_error == "continue":
            overall_status = "partial"
            null_outputs: dict[str, Any] = (
                {spec.name: None for spec in node.outputs}
                if node.outputs
                else {"text": ""}
            )
            context.set_node_outputs(node.id, null_outputs)
            continue

        if node.on_error == "branch":
            overall_status = "partial"
            rescue = {node.on_error_goto} if node.on_error_goto else set()
            _mark_transitive_skipped(node.id, dependents, skipped, rescue=rescue)
            continue

        # Unreachable given node.validate() enforces the enum, but be safe.
        overall_status = "failure"
        overall_error = f"node[{node.id}] unknown on_error: {node.on_error!r}"
        halted = True
        break

    # Nodes not reached after a halt should appear as skipped in the output,
    # in topological order, so CLI output stays coherent.
    if halted:
        for node in ordered_nodes:
            if node.id not in results_by_id:
                results_by_id[node.id] = NodeResult(
                    node_id=node.id,
                    status="skipped",
                    final_text="",
                    error="not reached (upstream halt)",
                )

    # Preserve topological order in the returned list.
    node_results = [results_by_id[n.id] for n in ordered_nodes]

    duration_ms = int((time.monotonic() - started_mono) * 1000)

    run_result = WorkflowRun(
        workflow=workflow.name,
        run_id=run_id,
        status=overall_status,
        started_at=started_at,
        duration_ms=duration_ms,
        node_results=node_results,
        context=context,
        error=overall_error,
    )

    if runs_dir is not None:
        storage.write_run(runs_dir, run_id, run_result, context)

    return run_result
