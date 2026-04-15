"""Workflow execution context + Jinja2 template rendering.

Carries `inputs` (workflow-level CLI args) and per-node `outputs`, and
renders Jinja2 templates like `{{ inputs.file }}` or `{{ analyze.issues }}`.
Strict undefined — referencing an unknown variable raises, surfacing typos
instead of silently producing `""`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import jinja2


@dataclass
class Context:
    """Per-run state: workflow inputs + outputs accumulated across nodes."""

    inputs: dict[str, Any] = field(default_factory=dict)
    # Each node's extracted outputs live under its node id:
    #   outputs["analyze"]["issues"] → extracted list
    outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    cwd: str | None = None

    def render(self, template: str) -> str:
        """Render a Jinja2 template against the current context."""
        env = jinja2.Environment(
            undefined=jinja2.StrictUndefined,
            keep_trailing_newline=True,
        )
        tmpl = env.from_string(template)
        return tmpl.render(inputs=self.inputs, **self.outputs)

    def set_node_outputs(self, node_id: str, values: dict[str, Any]) -> None:
        self.outputs[node_id] = values
