"""Persistent storage for workflow runs.

Each run dir under `~/.agentwire/workflows/runs/<run-id>/` holds:
  metadata.json    — run manifest (workflow, status, inputs, node summaries)
  context.json     — final Context (inputs + per-node extracted outputs)
  nodes/<id>.events.jsonl  — raw pi JSONL stream per node (written by pi_runner)

`metadata.json` is the authoritative index for `workflow history` / `show`.
Runs missing `metadata.json` are silently skipped by listings — they're
crashed/incomplete runs whose per-node logs may still be useful for manual
debugging but don't belong in the history view.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentwire.workflows.context import Context
    from agentwire.workflows.runner import WorkflowRun


logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2
METADATA_FILE = "metadata.json"
CONTEXT_FILE = "context.json"
NODES_DIR = "nodes"


def _summarize_runner(node_results) -> str:
    """Run-level runner tag: single runner name, 'mixed', or '' for no results."""
    runners = {r.runner for r in node_results if r.runner}
    if not runners:
        return ""
    if len(runners) == 1:
        return next(iter(runners))
    return "mixed"


def write_run(
    runs_dir: Path,
    run_id: str,
    run: WorkflowRun,
    context: Context,
) -> None:
    """Persist metadata + final context for a completed workflow run.

    Disk errors are logged but don't raise — the in-memory WorkflowRun
    is authoritative for the CLI caller that just executed.
    """
    run_dir = runs_dir / run_id
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("workflow storage: could not create %s: %s", run_dir, e)
        return

    metadata = {
        "schema_version": SCHEMA_VERSION,
        "workflow": run.workflow,
        "run_id": run.run_id,
        "status": run.status,
        "runner": _summarize_runner(run.node_results),
        "started_at": run.started_at,
        "duration_ms": run.duration_ms,
        "error": run.error,
        "inputs": dict(context.inputs),
        "nodes": [
            {
                "id": r.node_id,
                "runner": r.runner,
                "status": r.status,
                "attempts": r.attempts,
                "duration_ms": r.duration_ms,
                "tokens": r.tokens_used,
                "error": r.error,
            }
            for r in run.node_results
        ],
    }

    try:
        (run_dir / METADATA_FILE).write_text(json.dumps(metadata, indent=2))
    except OSError as e:
        logger.warning("workflow storage: could not write metadata: %s", e)

    try:
        (run_dir / CONTEXT_FILE).write_text(
            json.dumps(
                {"inputs": context.inputs, "outputs": context.outputs},
                indent=2,
                default=str,
            )
        )
    except OSError as e:
        logger.warning("workflow storage: could not write context: %s", e)


def list_runs(
    runs_dir: Path,
    workflow: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Scan runs_dir for runs with metadata, sorted newest first.

    Silently skips directories without a readable metadata.json.
    """
    if not runs_dir.is_dir():
        return []

    entries: list[dict] = []
    for child in runs_dir.iterdir():
        if not child.is_dir():
            continue
        meta_path = child / METADATA_FILE
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(meta, dict):
            continue
        if workflow is not None and meta.get("workflow") != workflow:
            continue
        entries.append(meta)

    entries.sort(key=lambda m: m.get("started_at", 0), reverse=True)
    if limit and limit > 0:
        entries = entries[:limit]
    return entries


def load_run(runs_dir: Path, run_id: str) -> dict | None:
    """Load metadata.json for a specific run. None if missing/unreadable."""
    meta_path = runs_dir / run_id / METADATA_FILE
    if not meta_path.is_file():
        return None
    try:
        data = json.loads(meta_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_context(runs_dir: Path, run_id: str) -> dict | None:
    """Load context.json for a specific run. None if missing/unreadable."""
    path = runs_dir / run_id / CONTEXT_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_events(
    runs_dir: Path,
    run_id: str,
    node_id: str | None = None,
) -> list[tuple[str, dict]]:
    """Load per-node events. Returns [(node_id, event_dict), ...].

    If node_id is given, returns only that node's events. If the run or node
    has no events file, returns an empty list.
    """
    nodes_dir = runs_dir / run_id / NODES_DIR
    if not nodes_dir.is_dir():
        return []

    if node_id is not None:
        files = [nodes_dir / f"{node_id}.events.jsonl"]
    else:
        files = sorted(nodes_dir.glob("*.events.jsonl"))

    out: list[tuple[str, dict]] = []
    for file in files:
        if not file.is_file():
            continue
        # Filename: "<node_id>.events.jsonl"
        nid = file.name.removesuffix(".events.jsonl")
        try:
            for line in file.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append((nid, json.loads(line)))
                except json.JSONDecodeError:
                    continue
        except OSError:
            continue
    return out
