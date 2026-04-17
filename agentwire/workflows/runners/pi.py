"""Pi runner — thin shim over the existing pi_runner.run_node.

Keeps pi's behaviour byte-for-byte identical while exposing the new
NodeRunner Protocol. The workflow engine calls `PiRunner().run(...)`
instead of `pi_runner.run_node(...)` directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from agentwire.workflows.node import ActionNode, NodeResult
from agentwire.workflows.pi_runner import run_node as _pi_run_node


class PiRunner:
    name = "pi"

    def run(
        self,
        node: ActionNode,
        workflow_cwd: str | None = None,
        event_log_path: Path | None = None,
        on_event: Callable[[dict], None] | None = None,
    ) -> NodeResult:
        # `on_event` is accepted for Protocol conformance but pi emits events
        # only after the subprocess exits (JSONL parsed post-hoc). Live streaming
        # is an Anthropic-runner feature; for pi we ignore the callback.
        return _pi_run_node(
            node,
            workflow_cwd=workflow_cwd,
            event_log_path=event_log_path,
        )
