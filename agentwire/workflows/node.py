"""Workflow node dataclasses.

MVP: ActionNode represents a single pi invocation. Future PRs extend with
ConditionalNode, ParallelNode. OutputSpec / depends_on / when / retries
fields are declared here so YAML parsing validates forward-compatibly,
but are not yet honored by the MVP runner.
"""

from __future__ import annotations

from dataclasses import dataclass, field


DEFAULT_TOOLS = ["read", "bash", "edit", "write"]
VALID_TOOLS = {"read", "bash", "edit", "write", "grep", "find", "ls"}
VALID_THINKING = {"off", "minimal", "low", "medium", "high", "xhigh"}


@dataclass
class ActionNode:
    """A single pi invocation in a workflow."""

    id: str
    prompt: str

    provider: str = "zai"
    model: str = "glm-5"
    tools: list[str] = field(default_factory=lambda: list(DEFAULT_TOOLS))
    thinking: str = "medium"

    # Declared for YAML forward-compat; runner MVP does not use these yet.
    depends_on: list[str] = field(default_factory=list)
    when: str | None = None
    timeout: int = 300
    retries: int = 0
    retry_delay: int = 10
    on_error: str = "fail"
    on_error_goto: str | None = None

    workdir: str | None = None
    extra_env: dict[str, str] = field(default_factory=dict)

    def validate(self) -> list[str]:
        """Return a list of validation error strings (empty = valid)."""
        errors: list[str] = []
        if not self.id or not self.id.strip():
            errors.append("node.id is required")
        if not self.prompt or not self.prompt.strip():
            errors.append(f"node[{self.id}].prompt is required")
        if self.thinking not in VALID_THINKING:
            errors.append(
                f"node[{self.id}].thinking={self.thinking!r} not in {sorted(VALID_THINKING)}"
            )
        bad_tools = [t for t in self.tools if t not in VALID_TOOLS]
        if bad_tools:
            errors.append(
                f"node[{self.id}].tools contains invalid: {bad_tools} "
                f"(valid: {sorted(VALID_TOOLS)})"
            )
        if self.on_error not in ("fail", "continue", "branch"):
            errors.append(
                f"node[{self.id}].on_error={self.on_error!r} "
                "must be one of fail|continue|branch"
            )
        return errors


@dataclass
class NodeResult:
    """Outcome of executing a single node."""

    node_id: str
    status: str  # "success" | "failure" | "timeout"
    final_text: str  # Final assistant message text
    events: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    tokens_used: dict = field(default_factory=dict)
    duration_ms: int = 0
    exit_code: int = 0
    error: str | None = None
