"""Workflow YAML loader + schema validator.

MVP: supports `name`, `description`, `version`, and a `nodes` map. Each node
builds into an ActionNode. Multi-node DAGs are declared legally here (so
future PRs don't break existing files) but only single-node workflows
actually execute in the MVP runner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from agentwire.workflows.node import ActionNode


# Where workflow YAML files are discovered by `agentwire workflow list` / run.
# Order matters: first match wins.
# User overrides live in `~/.agentwire/workflows/defs/`. Bundled examples are
# resolved via `_repo_examples_dir()` at runtime (see discover_workflows).
DISCOVERY_DIRS = [
    Path.home() / ".agentwire" / "workflows" / "defs",
]


@dataclass
class WorkflowDef:
    """Parsed workflow definition."""

    name: str
    nodes: list[ActionNode]
    description: str = ""
    version: int = 1
    source_path: Path | None = None

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.name:
            errors.append("workflow.name is required")
        if not self.nodes:
            errors.append("workflow.nodes must contain at least one node")
        seen_ids: set[str] = set()
        for node in self.nodes:
            errors.extend(node.validate())
            if node.id in seen_ids:
                errors.append(f"duplicate node id: {node.id}")
            seen_ids.add(node.id)
        # depends_on references must resolve
        for node in self.nodes:
            for dep in node.depends_on:
                if dep not in seen_ids:
                    errors.append(
                        f"node[{node.id}].depends_on references unknown node: {dep}"
                    )
        return errors


def _node_from_dict(node_id: str, data: dict) -> ActionNode:
    """Build an ActionNode from a YAML node mapping."""
    if not isinstance(data, dict):
        raise ValueError(f"node[{node_id}] must be a mapping, got {type(data).__name__}")

    kwargs: dict = {"id": node_id, "prompt": data.get("prompt", "")}

    for key in (
        "provider", "model", "thinking", "when", "on_error",
        "on_error_goto", "workdir",
    ):
        if key in data:
            kwargs[key] = data[key]

    if "tools" in data:
        tools = data["tools"]
        if not isinstance(tools, list):
            raise ValueError(f"node[{node_id}].tools must be a list")
        kwargs["tools"] = [str(t) for t in tools]

    if "depends_on" in data:
        deps = data["depends_on"]
        if isinstance(deps, str):
            deps = [deps]
        kwargs["depends_on"] = [str(d) for d in deps]

    for int_key in ("timeout", "retries", "retry_delay"):
        if int_key in data:
            kwargs[int_key] = int(data[int_key])

    if "extra_env" in data:
        env = data["extra_env"]
        if not isinstance(env, dict):
            raise ValueError(f"node[{node_id}].extra_env must be a mapping")
        kwargs["extra_env"] = {str(k): str(v) for k, v in env.items()}

    return ActionNode(**kwargs)


def load_workflow(path: Path) -> WorkflowDef:
    """Load and parse a workflow YAML file. Does not validate."""
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: workflow root must be a mapping")

    name = data.get("name") or path.stem
    description = data.get("description", "")
    version = int(data.get("version", 1))

    raw_nodes = data.get("nodes", {})
    if not isinstance(raw_nodes, dict):
        raise ValueError(f"{path}: 'nodes' must be a mapping of id → node")

    nodes: list[ActionNode] = []
    for node_id, node_data in raw_nodes.items():
        nodes.append(_node_from_dict(str(node_id), node_data))

    return WorkflowDef(
        name=name,
        nodes=nodes,
        description=description,
        version=version,
        source_path=path,
    )


def _repo_examples_dir() -> Path:
    """Path to the bundled `agentwire/workflows/examples/` dir."""
    return Path(__file__).resolve().parent / "examples"


def discover_workflows() -> list[WorkflowDef]:
    """Find all workflow YAMLs in known discovery dirs."""
    search_dirs = [*DISCOVERY_DIRS, _repo_examples_dir()]
    found: dict[str, WorkflowDef] = {}
    for directory in search_dirs:
        if not directory.is_dir():
            continue
        for yaml_file in sorted(directory.glob("*.yaml")):
            try:
                wf = load_workflow(yaml_file)
            except Exception:
                continue
            # First match wins — user's ~/.agentwire dir overrides repo examples
            if wf.name not in found:
                found[wf.name] = wf
    return list(found.values())


def resolve_workflow(name_or_path: str) -> WorkflowDef:
    """Resolve a workflow by name or path. Raises FileNotFoundError if not found."""
    candidate = Path(name_or_path)
    if candidate.exists() and candidate.is_file():
        return load_workflow(candidate)

    for wf in discover_workflows():
        if wf.name == name_or_path:
            return wf

    raise FileNotFoundError(
        f"workflow {name_or_path!r} not found. "
        f"Searched: {[str(p) for p in [*DISCOVERY_DIRS, _repo_examples_dir()]]}"
    )
