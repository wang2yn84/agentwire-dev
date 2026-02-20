#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# dependencies = ["pyyaml"]
# ///
"""
AgentWire Edit Tool Damage Control
===================================

Blocks edits to protected files via PreToolUse hook on Edit tool.
Loads zeroAccessPaths and readOnlyPaths from patterns.yaml.

Exit codes:
  0 = Allow edit
  2 = Block edit (stderr fed back to Claude)
"""

import fnmatch
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

import yaml

# Import audit logger (from same directory)
try:
    from audit_logger import log_allowed, log_blocked
except ImportError:
    # Fallback if audit_logger not available (no-op functions)
    def log_blocked(*args, **kwargs): pass
    def log_allowed(*args, **kwargs): pass


def is_glob_pattern(pattern: str) -> bool:
    """Check if pattern contains glob wildcards."""
    return '*' in pattern or '?' in pattern or '[' in pattern


def match_path(file_path: str, pattern: str) -> bool:
    """Match file path against pattern, supporting both prefix and glob matching."""
    expanded_pattern = os.path.expanduser(pattern)
    normalized = os.path.normpath(file_path)
    expanded_normalized = os.path.expanduser(normalized)

    if is_glob_pattern(pattern):
        # Glob pattern matching (case-insensitive for security)
        basename = os.path.basename(expanded_normalized)
        basename_lower = basename.lower()
        pattern_lower = pattern.lower()
        expanded_pattern_lower = expanded_pattern.lower()

        # Match against basename for patterns like *.pem, .env*
        if fnmatch.fnmatch(basename_lower, expanded_pattern_lower):
            return True
        if fnmatch.fnmatch(basename_lower, pattern_lower):
            return True
        # Also try full path match for patterns like /path/*.pem
        if fnmatch.fnmatch(expanded_normalized.lower(), expanded_pattern_lower):
            return True
        return False
    else:
        # Prefix matching (original behavior for directories)
        if expanded_normalized.startswith(expanded_pattern) or expanded_normalized == expanded_pattern.rstrip('/'):
            return True
        return False


def get_config_path() -> Path:
    """Get path to patterns.yaml, checking multiple locations."""
    # 1. Check AgentWire hooks directory
    agentwire_dir = os.environ.get("AGENTWIRE_DIR", os.path.expanduser("~/.agentwire"))
    config = Path(agentwire_dir) / "hooks" / "damage-control" / "patterns.yaml"
    if config.exists():
        return config

    # 2. Fallback to script directory
    return Path(__file__).parent / "patterns.yaml"


def load_config() -> Dict[str, Any]:
    """Load config from YAML."""
    config_path = get_config_path()

    if not config_path.exists():
        return {"zeroAccessPaths": [], "readOnlyPaths": []}

    with open(config_path, "r") as f:
        config = yaml.safe_load(f) or {}

    return config


ALL_OPERATIONS = {"read", "write", "edit", "delete", "move", "chmod"}


def _parse_allowed_entry(entry: dict) -> dict:
    """Parse an allowed-path entry to {path: str, allow: set}.

    Entry must be a dict with "path" key and optional "allow" (defaults to "all").
    """
    allow = entry.get("allow", "all")
    if isinstance(allow, str) and allow.strip().lower() == "all":
        return {"path": entry["path"], "allow": ALL_OPERATIONS.copy()}
    if isinstance(allow, list):
        return {"path": entry["path"], "allow": {a.strip().lower() for a in allow}}
    if isinstance(allow, str):
        return {"path": entry["path"], "allow": {allow.strip().lower()}}
    return {"path": entry["path"], "allow": ALL_OPERATIONS.copy()}


def _find_project_config() -> Tuple[str, list]:
    """Walk up from $PWD to find .agentwire.yml and return (project_root, allowed_paths)."""
    cwd = os.environ.get("PWD", os.getcwd())
    current = os.path.abspath(cwd)
    while True:
        config_file = os.path.join(current, ".agentwire.yml")
        if os.path.isfile(config_file):
            try:
                with open(config_file, "r") as f:
                    data = yaml.safe_load(f) or {}
                safety = data.get("safety", {})
                if isinstance(safety, dict):
                    paths = safety.get("allowed_paths", [])
                    if isinstance(paths, list):
                        return current, paths
            except Exception:
                pass
            return current, []
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return cwd, []


def load_allowed_paths(config: Dict[str, Any]) -> list:
    """Load allowed paths from global config and per-project .agentwire.yml.

    Returns list of {"path": str, "allow": set} entries.
    """
    raw = list(config.get("allowedPaths", []))

    project_root, project_paths = _find_project_config()
    for p in project_paths:
        if not isinstance(p, dict):
            continue
        entry = _parse_allowed_entry(p)
        if not os.path.isabs(os.path.expanduser(entry["path"])):
            entry["path"] = os.path.join(project_root, entry["path"])
        raw.append(entry)

    result = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if "allow" in item and isinstance(item["allow"], set):
            result.append(item)
        else:
            result.append(_parse_allowed_entry(item))
    return result


def is_path_allowed(file_path: str, allowed_paths: list, operation: str) -> bool:
    """Check if file_path has the given operation permitted by any allowed-path entry."""
    for entry in allowed_paths:
        if match_path(file_path, entry["path"]):
            if operation in entry["allow"]:
                return True
    return False


def check_path(file_path: str, config: Dict[str, Any]) -> Tuple[bool, str]:
    """Check if file_path is blocked. Returns (blocked, reason)."""
    allowed = load_allowed_paths(config)

    # Check allowlist with "edit" permission
    if is_path_allowed(file_path, allowed, "edit"):
        return False, ""

    # Check zero-access paths first (no access at all)
    for zero_path in config.get("zeroAccessPaths", []):
        if match_path(file_path, zero_path):
            return True, f"zero-access path {zero_path} (no operations allowed)"

    # Check read-only paths (edits not allowed)
    for readonly in config.get("readOnlyPaths", []):
        if match_path(file_path, readonly):
            return True, f"read-only path {readonly}"

    return False, ""


def main() -> None:
    config = load_config()

    # Read hook input from stdin
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON input: {e}", file=sys.stderr)
        sys.exit(1)

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    # Only check Edit tool
    if tool_name != "Edit":
        sys.exit(0)

    file_path = tool_input.get("file_path", "")
    if not file_path:
        sys.exit(0)

    # Check if file is blocked
    blocked, reason = check_path(file_path, config)
    if blocked:
        # Log blocked edit
        log_blocked("Edit", file_path, reason)
        print(f"SECURITY: Blocked edit to {reason}: {file_path}", file=sys.stderr)
        sys.exit(2)

    # Log allowed edit
    log_allowed("Edit", file_path, user_approved=False)
    sys.exit(0)


if __name__ == "__main__":
    main()
