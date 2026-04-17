"""Tests for `apply_runner_override` — the helper behind `workflow run --runner`."""

from __future__ import annotations

from agentwire.workflows.definitions import WorkflowDef, apply_runner_override
from agentwire.workflows.node import ActionNode


class TestApplyRunnerOverride:
    def test_none_returns_same_object(self):
        """None means 'no override' — return the workflow as-is, no copy."""
        wf = WorkflowDef(name="t", nodes=[ActionNode(id="a", prompt="p", runner="pi")])
        assert apply_runner_override(wf, None) is wf

    def test_flips_every_node(self):
        wf = WorkflowDef(
            name="t",
            nodes=[
                ActionNode(id="a", prompt="p", runner="pi"),
                ActionNode(id="b", prompt="p", runner="pi"),
            ],
        )
        out = apply_runner_override(wf, "anthropic")

        assert [n.runner for n in out.nodes] == ["anthropic", "anthropic"]
        # Original untouched — essential because discover_workflows() caches.
        assert [n.runner for n in wf.nodes] == ["pi", "pi"]
        assert out is not wf

    def test_override_to_pi_surfaces_anthropic_field_errors(self):
        """Overriding an anthropic node to pi leaves anthropic-only fields set,
        which ActionNode.validate() rejects with a clear message."""
        wf = WorkflowDef(
            name="t",
            nodes=[
                ActionNode(
                    id="a", prompt="p", runner="anthropic",
                    model="claude-opus-4-7", effort="high",
                ),
            ],
        )
        overridden = apply_runner_override(wf, "pi")
        errors = overridden.validate()
        assert any(
            "effort" in e and "only valid when runner=anthropic" in e
            for e in errors
        ), f"expected pi+effort validation error, got: {errors}"

    def test_override_to_anthropic_rejects_lowercase_pi_tools(self):
        """Pi tools are lowercase; anthropic expects CamelCase. After override,
        validation surfaces the namespace mismatch."""
        wf = WorkflowDef(
            name="t",
            nodes=[
                ActionNode(
                    id="a", prompt="p", runner="pi",
                    tools=["read", "grep"], model="claude-opus-4-7",
                ),
            ],
        )
        overridden = apply_runner_override(wf, "anthropic")
        errors = overridden.validate()
        assert any("CamelCase" in e for e in errors), (
            f"expected CamelCase tool-namespace error, got: {errors}"
        )
