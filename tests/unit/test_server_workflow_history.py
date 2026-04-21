"""Tests for the /api/workflows/runs* endpoints in agentwire/server.py."""

import json
from unittest.mock import MagicMock, patch

import pytest

from agentwire.config import load_config


@pytest.fixture
def server(tmp_path):
    config = load_config(tmp_path / "nonexistent.yaml")
    from agentwire.server import AgentWireServer
    return AgentWireServer(config)


def _write_metadata(runs_dir, run_id: str, workflow: str, nodes=None, **overrides):
    """Create a minimal metadata.json under runs_dir/<run_id>/.

    Matches the actual on-disk schema: per-node `id` + nested `tokens: {input, output, cost}`.
    """
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True)
    if nodes is None:
        nodes = [{
            "id": "n1",
            "runner": "anthropic",
            "status": "success",
            "duration_ms": 5000,
            "tokens": {"input": 100, "output": 200, "cost": 0.42},
            "error": None,
        }]
    meta = {
        "schema_version": 2,
        "run_id": run_id,
        "workflow": workflow,
        "status": "success",
        "runner": "anthropic",
        "started_at": 1700000000.0,
        "duration_ms": 5000,
        "nodes": nodes,
    }
    meta.update(overrides)
    (run_dir / "metadata.json").write_text(json.dumps(meta))
    return meta


def _mock_request(match_info=None, query=None):
    req = MagicMock()
    req.match_info = match_info or {}
    req.query = query or {}
    return req


class TestWorkflowRunsList:
    async def test_empty_runs_dir(self, server, tmp_path):
        with patch("agentwire.workflows.cli.RUNS_DIR", tmp_path):
            resp = await server.api_workflows_runs_list(_mock_request())
        data = json.loads(resp.body)
        assert data == {"runs": []}

    async def test_lists_runs_newest_first(self, server, tmp_path):
        _write_metadata(tmp_path, "run-old", "wf-a", started_at=100.0)
        _write_metadata(tmp_path, "run-new", "wf-b", started_at=200.0)
        with patch("agentwire.workflows.cli.RUNS_DIR", tmp_path):
            resp = await server.api_workflows_runs_list(_mock_request())
        data = json.loads(resp.body)
        assert [r["run_id"] for r in data["runs"]] == ["run-new", "run-old"]

    async def test_slim_fields_only(self, server, tmp_path):
        _write_metadata(tmp_path, "run-1", "wf", extra_field="should_not_leak")
        with patch("agentwire.workflows.cli.RUNS_DIR", tmp_path):
            resp = await server.api_workflows_runs_list(_mock_request())
        data = json.loads(resp.body)
        row = data["runs"][0]
        assert "extra_field" not in row
        assert set(row.keys()) == {
            "run_id", "workflow", "status", "runner", "started_at",
            "duration_ms", "total_cost", "total_tokens_in",
            "total_tokens_out", "node_count",
        }
        assert row["node_count"] == 1

    async def test_totals_aggregated_from_nodes(self, server, tmp_path):
        _write_metadata(tmp_path, "run-multi", "wf", nodes=[
            {"id": "a", "status": "success", "duration_ms": 100,
             "tokens": {"input": 10, "output": 20, "cost": 0.1}},
            {"id": "b", "status": "success", "duration_ms": 200,
             "tokens": {"input": 30, "output": 40, "cost": 0.25}},
        ])
        with patch("agentwire.workflows.cli.RUNS_DIR", tmp_path):
            resp = await server.api_workflows_runs_list(_mock_request())
        data = json.loads(resp.body)
        row = data["runs"][0]
        assert row["total_tokens_in"] == 40
        assert row["total_tokens_out"] == 60
        assert row["total_cost"] == pytest.approx(0.35)

    async def test_workflow_filter(self, server, tmp_path):
        _write_metadata(tmp_path, "run-a", "wf-a")
        _write_metadata(tmp_path, "run-b", "wf-b")
        with patch("agentwire.workflows.cli.RUNS_DIR", tmp_path):
            resp = await server.api_workflows_runs_list(_mock_request(query={"workflow": "wf-a"}))
        data = json.loads(resp.body)
        assert len(data["runs"]) == 1
        assert data["runs"][0]["workflow"] == "wf-a"

    async def test_invalid_limit_defaults_to_50(self, server, tmp_path):
        _write_metadata(tmp_path, "run-1", "wf")
        with patch("agentwire.workflows.cli.RUNS_DIR", tmp_path):
            resp = await server.api_workflows_runs_list(_mock_request(query={"limit": "notanumber"}))
        # Should not raise; just return the run
        assert resp.status == 200


class TestWorkflowRunDetail:
    async def test_404_missing_run(self, server, tmp_path):
        with patch("agentwire.workflows.cli.RUNS_DIR", tmp_path):
            resp = await server.api_workflows_run_detail(
                _mock_request(match_info={"run_id": "does-not-exist"})
            )
        assert resp.status == 404

    async def test_returns_normalized_nodes_merged_with_events(self, server, tmp_path):
        _write_metadata(tmp_path, "run-1", "wf")
        # Write context.json
        (tmp_path / "run-1" / "context.json").write_text(json.dumps({"inputs": {"x": 1}}))
        # Write a node events file with one tool_use event and a text block
        nodes_dir = tmp_path / "run-1" / "nodes"
        nodes_dir.mkdir()
        event = {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "WebSearch", "input": {"query": "hi"}},
                    {"type": "text", "text": "all done"},
                ],
            },
        }
        (nodes_dir / "n1.events.jsonl").write_text(json.dumps(event) + "\n")

        with patch("agentwire.workflows.cli.RUNS_DIR", tmp_path):
            resp = await server.api_workflows_run_detail(
                _mock_request(match_info={"run_id": "run-1"})
            )
        data = json.loads(resp.body)
        assert data["metadata"]["run_id"] == "run-1"
        # metadata no longer carries the nodes array — nodes is top-level
        assert "nodes" not in data["metadata"]
        assert data["context"] == {"inputs": {"x": 1}}
        assert len(data["nodes"]) == 1
        node = data["nodes"][0]
        # Flat normalized schema
        assert node["node_id"] == "n1"
        assert node["status"] == "success"
        assert node["tokens_in"] == 100
        assert node["tokens_out"] == 200
        assert node["cost"] == pytest.approx(0.42)
        # Event-derived fields
        assert node["event_count"] == 1
        assert node["tool_calls"][0]["name"] == "WebSearch"
        assert node["final_text"] == "all done"

    async def test_missing_context_returns_empty_dict(self, server, tmp_path):
        _write_metadata(tmp_path, "run-1", "wf")
        with patch("agentwire.workflows.cli.RUNS_DIR", tmp_path):
            resp = await server.api_workflows_run_detail(
                _mock_request(match_info={"run_id": "run-1"})
            )
        data = json.loads(resp.body)
        assert data["context"] == {}
