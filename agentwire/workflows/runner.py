"""Workflow DAG executor.

Runs nodes in topological order. For each node:
  1. Render its Jinja2 prompt against the current Context
     (`{{ inputs.x }}`, `{{ upstream_node.var }}`).
  2. Invoke pi via `run_node`.
  3. Extract declared outputs into the Context for downstream nodes.

`when`, retries, and on_error branching ship in PR C.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from agentwire.workflows.context import Context
from agentwire.workflows.definitions import InputSpec, WorkflowDef, topological_sort
from agentwire.workflows.node import NodeResult
from agentwire.workflows.outputs import OutputExtractionError, extract_outputs
from agentwire.workflows.pi_runner import run_node


@dataclass
class WorkflowRun:
    """Result of running a workflow."""

    workflow: str
    run_id: str
    status: str  # "success" | "failure" | "partial"
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
    """Coerce + validate provided CLI inputs against declared specs.

    Unknown inputs are rejected; missing required inputs without defaults are errors.
    """
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
                           f"(depends_on={n.depends_on or '[]'})",
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

    node_results: list[NodeResult] = []
    overall_status = "success"
    overall_error: str | None = None

    for node in ordered_nodes:
        # Render prompt against current context. A template error here is a
        # definition-level bug (undefined var, bad syntax) — surface it as a
        # node failure and stop.
        try:
            rendered_prompt = context.render(node.prompt)
        except Exception as e:
            node_results.append(NodeResult(
                node_id=node.id,
                status="failure",
                final_text="",
                error=f"prompt template error: {e}",
            ))
            overall_status = "failure"
            overall_error = f"node[{node.id}] template error: {e}"
            break

        rendered_node = replace(node, prompt=rendered_prompt)

        event_log_path: Path | None = None
        if runs_dir is not None:
            event_log_path = runs_dir / run_id / "nodes" / f"{node.id}.events.jsonl"

        result = run_node(
            rendered_node,
            workflow_cwd=context.cwd,
            event_log_path=event_log_path,
        )
        node_results.append(result)

        if result.status != "success":
            overall_status = "failure"
            overall_error = result.error
            break

        # Extract declared outputs into context for downstream nodes.
        # If the node didn't declare outputs, expose `{{ node_id.text }}`
        # so downstream prompts can at least reference the final message.
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
                overall_status = "failure"
                overall_error = f"node[{node.id}] {result.error}"
                break
        else:
            context.set_node_outputs(node.id, {"text": result.final_text})

    duration_ms = int((time.monotonic() - started_mono) * 1000)

    return WorkflowRun(
        workflow=workflow.name,
        run_id=run_id,
        status=overall_status,
        started_at=started_at,
        duration_ms=duration_ms,
        node_results=node_results,
        context=context,
        error=overall_error,
    )
