"""Tests for agentwire/workflows/ — context, outputs, definitions, storage, runner."""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest

from agentwire.workflows import (
    ActionNode,
    Context,
    NodeResult,
    OutputSpec,
    WorkflowDef,
    list_runs,
    run_workflow,
)
from agentwire.workflows.definitions import (
    InputSpec,
    _find_cycle,
    topological_sort,
)
from agentwire.workflows.outputs import (
    OutputExtractionError,
    _apply_jsonpath,
    extract_outputs,
)
from agentwire.workflows.storage import write_run

# ---- Context ----------------------------------------------------------------

class TestContext:
    def test_render_with_input(self):
        ctx = Context(inputs={"name": "world"})
        assert ctx.render("hello {{ inputs.name }}").strip() == "hello world"

    def test_render_with_upstream_output(self):
        ctx = Context()
        ctx.set_node_outputs("a", {"value": "result"})
        assert ctx.render("got {{ a.value }}").strip() == "got result"

    def test_render_strict_undefined_raises(self):
        ctx = Context()
        with pytest.raises(Exception):
            ctx.render("{{ missing_var }}")

    def test_eval_condition_equality(self):
        ctx = Context()
        ctx.set_node_outputs("a", {"status": "pass"})
        assert ctx.eval_condition("a.status == 'pass'") is True
        assert ctx.eval_condition("a.status == 'fail'") is False

    def test_eval_condition_numeric(self):
        ctx = Context(inputs={"count": 5})
        assert ctx.eval_condition("inputs.count > 0") is True
        assert ctx.eval_condition("inputs.count > 10") is False

    def test_eval_condition_undefined_raises(self):
        ctx = Context()
        with pytest.raises(Exception):
            ctx.eval_condition("bogus.thing")


# ---- Output extraction ------------------------------------------------------

def _make_result(text: str) -> NodeResult:
    return NodeResult(node_id="x", status="success", final_text=text)


class TestOutputs:
    def test_text_source_trims(self):
        spec = OutputSpec(name="r", source="text")
        values, soft = extract_outputs([spec], _make_result("  hello  "))
        assert values["r"] == "hello"
        assert soft == []

    def test_regex_group(self):
        spec = OutputSpec(name="tid", source="regex", pattern=r"TICKET-(\d+)")
        values, _ = extract_outputs([spec], _make_result("see TICKET-42 please"))
        assert values["tid"] == "42"

    def test_regex_no_match_raises_when_required(self):
        spec = OutputSpec(name="tid", source="regex", pattern="ZZZ")
        with pytest.raises(OutputExtractionError):
            extract_outputs([spec], _make_result("no match here"))

    def test_regex_no_match_soft_when_optional(self):
        spec = OutputSpec(name="tid", source="regex", pattern="ZZZ", required=False)
        values, soft = extract_outputs([spec], _make_result("nope"))
        assert values["tid"] is None
        assert soft and "tid" in soft[0]

    def test_jsonpath_nested(self):
        spec = OutputSpec(name="v", source="jsonpath", pattern="$.a.b")
        values, _ = extract_outputs(
            [spec], _make_result(json.dumps({"a": {"b": "x"}}))
        )
        assert values["v"] == "x"

    def test_jsonpath_fenced_json(self):
        spec = OutputSpec(name="v", source="jsonpath", pattern="$.n")
        fenced = '```json\n{"n": 7}\n```'
        values, _ = extract_outputs([spec], _make_result(fenced))
        assert values["v"] == 7

    def test_jsonpath_wildcard_map(self):
        data = {"items": [{"k": 1}, {"k": 2}, {"k": 3}]}
        assert _apply_jsonpath(data, "$.items[*].k") == [1, 2, 3]

    def test_jsonpath_malformed_json_raises(self):
        spec = OutputSpec(name="v", source="jsonpath", pattern="$.x")
        with pytest.raises(OutputExtractionError):
            extract_outputs([spec], _make_result("not json at all"))


# ---- Definitions (topo sort, cycle, inputs) ---------------------------------

class TestDefinitions:
    def test_topological_sort_diamond(self):
        nodes = [
            ActionNode(id="a", prompt="p"),
            ActionNode(id="b", prompt="p", depends_on=["a"]),
            ActionNode(id="c", prompt="p", depends_on=["a"]),
            ActionNode(id="d", prompt="p", depends_on=["b", "c"]),
        ]
        ordered = [n.id for n in topological_sort(nodes)]
        assert ordered.index("a") < ordered.index("b")
        assert ordered.index("a") < ordered.index("c")
        assert ordered.index("b") < ordered.index("d")
        assert ordered.index("c") < ordered.index("d")

    def test_cycle_detection(self):
        nodes = [
            ActionNode(id="a", prompt="p", depends_on=["b"]),
            ActionNode(id="b", prompt="p", depends_on=["a"]),
        ]
        cycle = _find_cycle(nodes)
        assert cycle is not None and set(cycle) == {"a", "b"}

    def test_no_cycle(self):
        nodes = [
            ActionNode(id="a", prompt="p"),
            ActionNode(id="b", prompt="p", depends_on=["a"]),
        ]
        assert _find_cycle(nodes) is None

    def test_input_coerce_types(self):
        assert InputSpec(name="x", type="int").coerce("42") == 42
        assert InputSpec(name="x", type="float").coerce("1.5") == 1.5
        assert InputSpec(name="x", type="bool").coerce("true") is True
        assert InputSpec(name="x", type="bool").coerce("0") is False
        assert InputSpec(name="x", type="json").coerce('{"k":1}') == {"k": 1}

    def test_workflow_validate_branch_requires_goto(self):
        wf = WorkflowDef(
            name="wf",
            nodes=[
                ActionNode(id="a", prompt="p", on_error="branch"),
                ActionNode(id="b", prompt="p"),
            ],
        )
        errors = wf.validate()
        assert any("on_error_goto" in e for e in errors)

    def test_workflow_validate_branch_goto_unknown(self):
        wf = WorkflowDef(
            name="wf",
            nodes=[
                ActionNode(id="a", prompt="p", on_error="branch",
                           on_error_goto="nowhere"),
                ActionNode(id="b", prompt="p"),
            ],
        )
        errors = wf.validate()
        assert any("unknown" in e for e in errors)

    def test_output_validation_catches_missing_pattern(self):
        node = ActionNode(
            id="a",
            prompt="p",
            outputs=[OutputSpec(name="v", source="jsonpath", pattern="")],
        )
        errors = node.validate()
        assert any("requires pattern" in e for e in errors)


# ---- Storage round-trip -----------------------------------------------------

def _make_workflow_run(runs_dir, **overrides):
    from agentwire.workflows.runner import WorkflowRun
    defaults = dict(
        workflow="test-wf",
        run_id="test-wf-20260415T000000-aaaa1111",
        status="success",
        started_at=time.time(),
        duration_ms=100,
        node_results=[
            NodeResult(
                node_id="a",
                status="success",
                final_text="hi",
                duration_ms=50,
                attempts=1,
                runner="pi",
            ),
        ],
    )
    defaults.update(overrides)
    return WorkflowRun(**defaults)


class TestStorage:
    def test_write_and_list_roundtrip(self, tmp_path):
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        run = _make_workflow_run(runs_dir)
        ctx = Context(inputs={"x": 1})
        ctx.set_node_outputs("a", {"text": "hi"})

        write_run(runs_dir, run.run_id, run, ctx)

        rows = list_runs(runs_dir)
        assert len(rows) == 1
        assert rows[0]["run_id"] == run.run_id
        assert rows[0]["inputs"] == {"x": 1}
        assert rows[0]["nodes"][0]["id"] == "a"
        assert rows[0]["nodes"][0]["runner"] == "pi"
        assert rows[0]["runner"] == "pi"
        assert rows[0]["schema_version"] == 2

    def test_list_runs_sorted_newest_first(self, tmp_path):
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        older = _make_workflow_run(
            runs_dir,
            run_id="test-wf-20260101T000000-old",
            started_at=1000.0,
        )
        newer = _make_workflow_run(
            runs_dir,
            run_id="test-wf-20260601T000000-new",
            started_at=2000.0,
        )
        ctx = Context()
        write_run(runs_dir, older.run_id, older, ctx)
        write_run(runs_dir, newer.run_id, newer, ctx)

        rows = list_runs(runs_dir)
        assert [r["run_id"] for r in rows] == [newer.run_id, older.run_id]

    def test_list_skips_orphan_dir(self, tmp_path):
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        (runs_dir / "orphan-run").mkdir()  # no metadata.json
        assert list_runs(runs_dir) == []

    def test_list_skips_malformed_metadata(self, tmp_path):
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        bad = runs_dir / "bad-run"
        bad.mkdir()
        (bad / "metadata.json").write_text("not json {{{")

        # good one still listed
        run = _make_workflow_run(runs_dir)
        write_run(runs_dir, run.run_id, run, Context())

        rows = list_runs(runs_dir)
        assert len(rows) == 1
        assert rows[0]["run_id"] == run.run_id

    def test_filter_by_workflow(self, tmp_path):
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        a = _make_workflow_run(runs_dir, workflow="alpha",
                               run_id="alpha-1", started_at=1.0)
        b = _make_workflow_run(runs_dir, workflow="beta",
                               run_id="beta-1", started_at=2.0)
        write_run(runs_dir, a.run_id, a, Context())
        write_run(runs_dir, b.run_id, b, Context())

        assert [r["run_id"] for r in list_runs(runs_dir, workflow="alpha")] == ["alpha-1"]
        assert [r["run_id"] for r in list_runs(runs_dir, workflow="beta")] == ["beta-1"]


# ---- Runner (mocked pi) -----------------------------------------------------

def _canned(node_id: str, status: str = "success", final_text: str = "") -> NodeResult:
    """Factory for NodeResult objects the fake run_node returns."""
    return NodeResult(
        node_id=node_id,
        status=status,
        final_text=final_text,
        duration_ms=10,
        attempts=1,
    )


class _FakeRunner:
    """Test double that replaces PiRunner in the registry."""
    name = "pi"

    def __init__(self, side_effect):
        self.side_effect = side_effect

    def run(self, node, workflow_cwd=None, event_log_path=None, on_event=None):
        return self.side_effect(
            node, workflow_cwd=workflow_cwd, event_log_path=event_log_path
        )


def _patch_pi(side_effect):
    """Swap the registered 'pi' runner with a fake that calls side_effect."""
    from agentwire.workflows.runners import RUNNERS
    return patch.dict(RUNNERS, {"pi": _FakeRunner(side_effect)})


class TestRunnerDryRun:
    def test_dry_run_lists_all_nodes_in_topo_order(self, tmp_path):
        wf = WorkflowDef(
            name="wf",
            nodes=[
                ActionNode(id="a", prompt="p"),
                ActionNode(id="b", prompt="p", depends_on=["a"]),
            ],
        )
        run = run_workflow(wf, runs_dir=tmp_path, dry_run=True)
        assert run.status == "success"
        assert [r.node_id for r in run.node_results] == ["a", "b"]

    def test_missing_required_input_errors(self, tmp_path):
        wf = WorkflowDef(
            name="wf",
            nodes=[ActionNode(id="a", prompt="hi {{ inputs.x }}")],
            inputs=[InputSpec(name="x", type="string", required=True)],
        )
        run = run_workflow(wf, runs_dir=tmp_path, dry_run=True)
        assert run.status == "failure"
        assert "missing required input" in (run.error or "")


class TestRunnerWhen:
    def test_when_false_skips_node_and_dependents(self, tmp_path):
        wf = WorkflowDef(
            name="wf",
            nodes=[
                ActionNode(id="a", prompt="p"),
                ActionNode(id="b", prompt="p", depends_on=["a"],
                           when="a.text == 'xxx'"),  # will be false
                ActionNode(id="c", prompt="p", depends_on=["b"]),
            ],
        )

        def fake_run_node(node, **kw):
            return _canned(node.id, final_text="yyy")

        with _patch_pi(fake_run_node):
            run = run_workflow(wf, runs_dir=tmp_path)

        statuses = {r.node_id: r.status for r in run.node_results}
        assert statuses == {"a": "success", "b": "skipped", "c": "skipped"}
        assert run.status == "partial"

    def test_when_true_runs_node(self, tmp_path):
        wf = WorkflowDef(
            name="wf",
            nodes=[
                ActionNode(id="a", prompt="p"),
                ActionNode(id="b", prompt="p", depends_on=["a"],
                           when="a.text == 'yes'"),
            ],
        )
        with _patch_pi(lambda node, **kw: _canned(node.id, final_text="yes")):
            run = run_workflow(wf, runs_dir=tmp_path)
        statuses = {r.node_id: r.status for r in run.node_results}
        assert statuses == {"a": "success", "b": "success"}
        assert run.status == "success"


class TestRunnerOnError:
    def test_on_error_fail_halts(self, tmp_path):
        wf = WorkflowDef(
            name="wf",
            nodes=[
                ActionNode(id="a", prompt="p"),  # default on_error=fail
                ActionNode(id="b", prompt="p", depends_on=["a"]),
            ],
        )

        def fake(node, **kw):
            if node.id == "a":
                return NodeResult(node_id="a", status="failure",
                                  final_text="", error="boom", attempts=1)
            return _canned(node.id)

        with _patch_pi(fake):
            run = run_workflow(wf, runs_dir=tmp_path)

        assert run.status == "failure"
        statuses = {r.node_id: r.status for r in run.node_results}
        assert statuses["a"] == "failure"
        # b is unreached but still shows up in topo-ordered results as skipped.
        assert statuses["b"] == "skipped"

    def test_on_error_continue_provides_none_outputs(self, tmp_path):
        wf = WorkflowDef(
            name="wf",
            nodes=[
                ActionNode(
                    id="a",
                    prompt="p",
                    on_error="continue",
                    outputs=[OutputSpec(name="val", source="text")],
                ),
                ActionNode(
                    id="b",
                    prompt="got={{ a.val }}",
                    depends_on=["a"],
                ),
            ],
        )

        def fake(node, **kw):
            if node.id == "a":
                return NodeResult(node_id="a", status="failure",
                                  final_text="", error="boom", attempts=1)
            # The runner should render b's prompt with a.val == None
            return _canned(node.id, final_text=node.prompt)

        with _patch_pi(fake):
            run = run_workflow(wf, runs_dir=tmp_path)

        assert run.status == "partial"
        statuses = {r.node_id: r.status for r in run.node_results}
        assert statuses == {"a": "failure", "b": "success"}
        # b's final_text mirrors its rendered prompt — a.val was set to None
        # by the on_error=continue path, and Jinja renders None as "None".
        b_result = next(r for r in run.node_results if r.node_id == "b")
        assert b_result.final_text == "got=None"


class TestRunnerRetries:
    def test_retries_happen_on_failure(self, tmp_path):
        wf = WorkflowDef(
            name="wf",
            nodes=[
                ActionNode(id="a", prompt="p", retries=2, retry_delay=0,
                           on_error="continue"),
            ],
        )
        call_count = {"n": 0}

        def fake(node, **kw):
            call_count["n"] += 1
            return NodeResult(node_id=node.id, status="failure",
                              final_text="", error="flaky", attempts=1)

        with _patch_pi(fake):
            run = run_workflow(wf, runs_dir=tmp_path)

        assert call_count["n"] == 3  # 1 initial + 2 retries
        result = run.node_results[0]
        assert result.attempts == 3
        assert result.status == "failure"

    def test_retries_stop_after_success(self, tmp_path):
        wf = WorkflowDef(
            name="wf",
            nodes=[ActionNode(id="a", prompt="p", retries=5, retry_delay=0)],
        )
        state = {"n": 0}

        def fake(node, **kw):
            state["n"] += 1
            if state["n"] < 2:
                return NodeResult(node_id=node.id, status="failure",
                                  final_text="", error="not yet", attempts=1)
            return _canned(node.id, final_text="ok")

        with _patch_pi(fake):
            run = run_workflow(wf, runs_dir=tmp_path)

        result = run.node_results[0]
        assert result.status == "success"
        assert result.attempts == 2
