"""Workflow DAG executor.

MVP: supports a single ActionNode. Multi-node DAGs raise NotImplementedError
until PR B lands topological sort + dependency resolution.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from agentwire.workflows.definitions import WorkflowDef
from agentwire.workflows.node import NodeResult
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
    error: str | None = None


def _generate_run_id(workflow_name: str) -> str:
    ts = time.strftime("%Y%m%dT%H%M%S")
    return f"{workflow_name}-{ts}-{uuid.uuid4().hex[:8]}"


def run_workflow(
    workflow: WorkflowDef,
    runs_dir: Path | None = None,
    dry_run: bool = False,
) -> WorkflowRun:
    """Execute a workflow end-to-end.

    MVP: runs the first (and only) node. Raises NotImplementedError for
    multi-node workflows — Phase 2 PR B ships DAG support.
    """
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

    if len(workflow.nodes) > 1:
        raise NotImplementedError(
            "multi-node workflows land in Phase 2 PR B. "
            f"workflow {workflow.name!r} has {len(workflow.nodes)} nodes."
        )

    run_id = _generate_run_id(workflow.name)
    started_at = time.time()
    started_mono = time.monotonic()

    node = workflow.nodes[0]

    if dry_run:
        return WorkflowRun(
            workflow=workflow.name,
            run_id=run_id,
            status="success",
            started_at=started_at,
            duration_ms=0,
            node_results=[
                NodeResult(
                    node_id=node.id,
                    status="success",
                    final_text=f"[dry-run] would execute node {node.id!r}",
                )
            ],
        )

    event_log_path: Path | None = None
    if runs_dir is not None:
        event_log_path = runs_dir / run_id / "nodes" / f"{node.id}.events.jsonl"

    result = run_node(node, event_log_path=event_log_path)

    duration_ms = int((time.monotonic() - started_mono) * 1000)
    status = "success" if result.status == "success" else "failure"

    return WorkflowRun(
        workflow=workflow.name,
        run_id=run_id,
        status=status,
        started_at=started_at,
        duration_ms=duration_ms,
        node_results=[result],
        error=result.error,
    )
