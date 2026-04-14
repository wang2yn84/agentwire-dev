"""CLI handlers for `agentwire workflow *` subcommands.

Subcommands:
  list     - discover and print available workflows
  validate - parse + validate a workflow without running it
  run      - execute a workflow end-to-end
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agentwire.workflows.definitions import (
    discover_workflows,
    resolve_workflow,
)
from agentwire.workflows.runner import run_workflow


RUNS_DIR = Path.home() / ".agentwire" / "workflows" / "runs"


def cmd_workflow_list(args) -> int:
    """`agentwire workflow list` — discover available workflows."""
    workflows = discover_workflows()

    if getattr(args, "json", False):
        payload = [
            {
                "name": wf.name,
                "description": wf.description,
                "version": wf.version,
                "nodes": [n.id for n in wf.nodes],
                "source": str(wf.source_path) if wf.source_path else None,
            }
            for wf in workflows
        ]
        print(json.dumps(payload, indent=2))
        return 0

    if not workflows:
        print("No workflows found.")
        print("  Drop YAML files in ~/.agentwire/workflows/defs/ or see workflows/examples/.")
        return 0

    for wf in workflows:
        node_count = len(wf.nodes)
        desc = f" — {wf.description}" if wf.description else ""
        print(f"  {wf.name} ({node_count} node{'s' if node_count != 1 else ''}){desc}")

    return 0


def cmd_workflow_validate(args) -> int:
    """`agentwire workflow validate <name-or-path>` — check a workflow YAML."""
    try:
        workflow = resolve_workflow(args.workflow)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error parsing workflow: {e}", file=sys.stderr)
        return 1

    errors = workflow.validate()
    if errors:
        print(f"Workflow {workflow.name!r} has {len(errors)} validation error(s):")
        for err in errors:
            print(f"  - {err}")
        return 1

    print(f"Workflow {workflow.name!r} is valid ({len(workflow.nodes)} node(s)).")
    return 0


def cmd_workflow_run(args) -> int:
    """`agentwire workflow run <name-or-path>` — execute a workflow."""
    try:
        workflow = resolve_workflow(args.workflow)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    errors = workflow.validate()
    if errors:
        print(f"Workflow {workflow.name!r} has validation errors:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    dry_run = getattr(args, "dry_run", False)

    if getattr(args, "verbose", False):
        print(f"Running workflow {workflow.name!r} ({len(workflow.nodes)} node(s))...")
        if dry_run:
            print("  (dry-run)")

    try:
        result = run_workflow(workflow, runs_dir=RUNS_DIR, dry_run=dry_run)
    except NotImplementedError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    if getattr(args, "json", False):
        print(json.dumps({
            "workflow": result.workflow,
            "run_id": result.run_id,
            "status": result.status,
            "duration_ms": result.duration_ms,
            "error": result.error,
            "nodes": [
                {
                    "id": r.node_id,
                    "status": r.status,
                    "final_text": r.final_text,
                    "duration_ms": r.duration_ms,
                    "tokens": r.tokens_used,
                    "error": r.error,
                }
                for r in result.node_results
            ],
        }, indent=2))
        return 0 if result.status == "success" else 1

    print(f"\n=== Workflow {result.workflow!r} → {result.status} ===")
    print(f"  run_id: {result.run_id}")
    print(f"  duration: {result.duration_ms}ms")
    if result.error:
        print(f"  error: {result.error}")

    for node_result in result.node_results:
        print(f"\n  --- node[{node_result.node_id}] → {node_result.status} "
              f"({node_result.duration_ms}ms) ---")
        if node_result.tool_calls:
            tools = ", ".join(tc["name"] for tc in node_result.tool_calls)
            print(f"  tools used: {tools}")
        if node_result.tokens_used:
            t = node_result.tokens_used
            print(f"  tokens: in={t.get('input', 0)} out={t.get('output', 0)} "
                  f"cost=${t.get('cost', 0):.4f}")
        if node_result.final_text:
            print(f"\n{node_result.final_text}")
        if node_result.error:
            print(f"  error: {node_result.error}")

    return 0 if result.status == "success" else 1
