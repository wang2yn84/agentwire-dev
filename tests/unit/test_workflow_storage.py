"""Storage schema v2 tests — runner field + back-compat reads."""

from __future__ import annotations

import json
import time

from agentwire.workflows.context import Context
from agentwire.workflows.node import NodeResult
from agentwire.workflows.runner import WorkflowRun
from agentwire.workflows.storage import (
    METADATA_FILE,
    SCHEMA_VERSION,
    _summarize_runner,
    list_runs,
    load_run,
    write_run,
)


def _run_with_nodes(*pairs: tuple[str, str]) -> WorkflowRun:
    """pairs: (node_id, runner). Returns a success WorkflowRun."""
    return WorkflowRun(
        workflow="multi",
        run_id="multi-20260417T000000-zzzz",
        status="success",
        started_at=time.time(),
        duration_ms=123,
        node_results=[
            NodeResult(
                node_id=nid,
                status="success",
                final_text="",
                duration_ms=10,
                runner=rnr,
            )
            for nid, rnr in pairs
        ],
    )


class TestSummarizeRunner:
    def test_empty(self):
        assert _summarize_runner([]) == ""

    def test_all_pi(self):
        results = [NodeResult(node_id="a", status="success", final_text="", runner="pi")]
        assert _summarize_runner(results) == "pi"

    def test_all_anthropic(self):
        results = [
            NodeResult(node_id="a", status="success", final_text="", runner="anthropic"),
            NodeResult(node_id="b", status="success", final_text="", runner="anthropic"),
        ]
        assert _summarize_runner(results) == "anthropic"

    def test_mixed(self):
        results = [
            NodeResult(node_id="a", status="success", final_text="", runner="pi"),
            NodeResult(node_id="b", status="success", final_text="", runner="anthropic"),
        ]
        assert _summarize_runner(results) == "mixed"

    def test_ignores_blanks(self):
        # A skipped node has runner="" because the runner loop never touched it.
        results = [
            NodeResult(node_id="a", status="skipped", final_text="", runner=""),
            NodeResult(node_id="b", status="success", final_text="", runner="pi"),
        ]
        assert _summarize_runner(results) == "pi"


class TestSchemaV2:
    def test_write_round_trip_single_runner(self, tmp_path):
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        run = _run_with_nodes(("a", "anthropic"))
        write_run(runs_dir, run.run_id, run, Context())

        meta = load_run(runs_dir, run.run_id)
        assert meta is not None
        assert meta["schema_version"] == SCHEMA_VERSION == 2
        assert meta["runner"] == "anthropic"
        assert meta["nodes"][0]["runner"] == "anthropic"

    def test_write_round_trip_mixed(self, tmp_path):
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        run = _run_with_nodes(("a", "pi"), ("b", "anthropic"))
        write_run(runs_dir, run.run_id, run, Context())

        meta = load_run(runs_dir, run.run_id)
        assert meta["runner"] == "mixed"
        runners = [n["runner"] for n in meta["nodes"]]
        assert runners == ["pi", "anthropic"]

    def test_v1_backcompat_load(self, tmp_path):
        """A v1-era metadata.json (no runner fields) still loads cleanly."""
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        run_dir = runs_dir / "old-run-id"
        run_dir.mkdir()
        (run_dir / METADATA_FILE).write_text(json.dumps({
            "schema_version": 1,
            "workflow": "legacy",
            "run_id": "old-run-id",
            "status": "success",
            "started_at": 1000.0,
            "duration_ms": 50,
            "error": None,
            "inputs": {},
            "nodes": [
                {"id": "a", "status": "success", "attempts": 1,
                 "duration_ms": 10, "tokens": {}, "error": None},
            ],
        }))

        meta = load_run(runs_dir, "old-run-id")
        assert meta is not None
        assert meta["schema_version"] == 1
        # Missing runner field — consumers .get() this with "" default.
        assert meta.get("runner", "") == ""
        assert meta["nodes"][0].get("runner", "") == ""

        # list_runs tolerates it too.
        rows = list_runs(runs_dir)
        assert len(rows) == 1
        assert rows[0]["workflow"] == "legacy"
