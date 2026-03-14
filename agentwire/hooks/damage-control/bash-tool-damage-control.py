#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# dependencies = ["pyyaml"]
# ///
"""
AgentWire Security Firewall - Bash Tool Hook
=============================================

Blocks dangerous commands before execution via PreToolUse hook.
Loads patterns from rules directory for easy customization.

Exit codes:
  0 = Allow command (or JSON output with permissionDecision)
  2 = Block command (stderr fed back to Claude)

JSON output for ask patterns:
  {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": "..."}}
"""

import fnmatch
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

# Import audit logger (from same directory)
try:
    from audit_logger import log_allowed, log_asked, log_blocked
except ImportError:
    # Fallback if audit_logger not available (no-op functions)
    def log_blocked(*args, **kwargs): pass
    def log_asked(*args, **kwargs): pass
    def log_allowed(*args, **kwargs): pass


def is_glob_pattern(pattern: str) -> bool:
    """Check if pattern contains glob wildcards."""
    return '*' in pattern or '?' in pattern or '[' in pattern


def glob_to_regex(glob_pattern: str) -> str:
    """Convert a glob pattern to a regex pattern for matching in commands.

    For file extension patterns like *.extension, ensures the extension is at a word boundary
    (not followed by more word characters like in json.things).
    """
    # Escape special regex chars except * and ?
    result = ""
    for char in glob_pattern:
        if char == '*':
            result += r'[^\s/]*'  # Match any chars except whitespace and path sep
        elif char == '?':
            result += r'[^\s/]'   # Match single char except whitespace and path sep
        elif char in r'\.^$+{}[]|()':
            result += '\\' + char
        else:
            result += char
    # Add word boundary at end to prevent matching substrings
    # e.g., *.ext should not match method.extension
    result += r'(?![a-zA-Z0-9_])'
    return result

# ============================================================================
# OPERATION PATTERNS - Edit these to customize what operations are blocked
# ============================================================================
# {path} will be replaced with the escaped path at runtime

# Operations blocked for READ-ONLY paths (all modifications)
WRITE_PATTERNS = [
    (r'>\s*{path}', "write"),
    (r'\btee\s+(?!.*-a).*{path}', "write"),
]

APPEND_PATTERNS = [
    (r'>>\s*{path}', "append"),
    (r'\btee\s+-a\s+.*{path}', "append"),
    (r'\btee\s+.*-a.*{path}', "append"),
]

EDIT_PATTERNS = [
    (r'\bsed\s+-i.*{path}', "edit"),
    (r'\bperl\s+-[^\s]*i.*{path}', "edit"),
    (r'\bawk\s+-i\s+inplace.*{path}', "edit"),
]

MOVE_COPY_PATTERNS = [
    (r'\bmv\s+.*\s+{path}', "move"),
    (r'\bcp\s+.*\s+{path}', "copy"),
]

DELETE_PATTERNS = [
    (r'\brm\s+.*{path}', "delete"),
    (r'\bunlink\s+.*{path}', "delete"),
    (r'\brmdir\s+.*{path}', "delete"),
    (r'\bshred\s+.*{path}', "delete"),
]

PERMISSION_PATTERNS = [
    (r'\bchmod\s+.*{path}', "chmod"),
    (r'\bchown\s+.*{path}', "chown"),
    (r'\bchgrp\s+.*{path}', "chgrp"),
]

TRUNCATE_PATTERNS = [
    (r'\btruncate\s+.*{path}', "truncate"),
    (r':\s*>\s*{path}', "truncate"),
]

# Combined patterns for read-only paths (block ALL modifications)
READ_ONLY_BLOCKED = (
    WRITE_PATTERNS +
    APPEND_PATTERNS +
    EDIT_PATTERNS +
    MOVE_COPY_PATTERNS +
    DELETE_PATTERNS +
    PERMISSION_PATTERNS +
    TRUNCATE_PATTERNS
)

# Patterns for no-delete paths (block ONLY delete operations)
NO_DELETE_BLOCKED = DELETE_PATTERNS

# ============================================================================
# CONFIGURATION LOADING
# ============================================================================

def get_rules_dir() -> Path:
    """Get path to rules directory."""
    agentwire_dir = os.environ.get("AGENTWIRE_DIR", os.path.expanduser("~/.agentwire"))
    rules_dir = Path(agentwire_dir) / "damage-control"
    if rules_dir.exists() and list(rules_dir.glob("*.yaml")):
        return rules_dir
    # Fallback: rules/ subdirectory next to this script
    return Path(__file__).parent / "rules"


def load_config() -> Dict[str, Any]:
    """Load and merge patterns from all .yaml files in rules directory."""
    rules_dir = get_rules_dir()

    merged = {
        "bashToolPatterns": [],
        "zeroAccessPaths": [],
        "readOnlyPaths": [],
        "noDeletePaths": [],
        "allowedPaths": [],
    }

    if not rules_dir.exists():
        print(f"Warning: Rules directory not found at {rules_dir}", file=sys.stderr)
        return merged

    yaml_files = sorted(rules_dir.glob("*.yaml"))
    if not yaml_files:
        print(f"Warning: No .yaml files found in {rules_dir}", file=sys.stderr)
        return merged

    for rules_file in yaml_files:
        try:
            with open(rules_file, "r") as f:
                data = yaml.safe_load(f) or {}
            for key in merged:
                merged[key].extend(data.get(key, []))
        except Exception as e:
            print(f"Warning: Could not load {rules_file.name}: {e}", file=sys.stderr)

    return merged


# ============================================================================
# ALLOWED PATHS (Granular Permissions)
# ============================================================================
# Operations: "all", "read", "write", "edit", "delete", "move", "chmod"

ALL_OPERATIONS = {"read", "write", "edit", "delete", "move", "chmod"}


def _parse_allowed_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
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


def _find_project_config() -> Tuple[str, List[Any]]:
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


def load_allowed_paths(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Load allowed paths from global config and per-project .agentwire.yml.

    Returns list of {"path": str, "allow": set} entries.
    """
    raw = list(config.get("allowedPaths", []))

    # Merge per-project allowed paths (relative to project root)
    project_root, project_paths = _find_project_config()
    for p in project_paths:
        if not isinstance(p, dict):
            continue
        entry = _parse_allowed_entry(p)
        if not os.path.isabs(os.path.expanduser(entry["path"])):
            entry["path"] = os.path.join(project_root, entry["path"])
        raw.append(entry)

    # Parse all entries (skip non-dict items)
    result = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if "allow" in item and isinstance(item["allow"], set):
            result.append(item)
        else:
            result.append(_parse_allowed_entry(item))
    return result


def _match_allowed(file_path: str, pattern: str) -> bool:
    """Check if file_path matches an allowed-path pattern (glob or prefix)."""
    expanded_pattern = os.path.expanduser(pattern)
    normalized = os.path.normpath(file_path)
    expanded_normalized = os.path.expanduser(normalized)

    if is_glob_pattern(pattern):
        lower_path = expanded_normalized.lower()
        lower_pattern = expanded_pattern.lower()
        if fnmatch.fnmatch(lower_path, lower_pattern):
            return True
        basename = os.path.basename(expanded_normalized)
        if fnmatch.fnmatch(basename.lower(), lower_pattern):
            return True
        return False
    else:
        if expanded_normalized.startswith(expanded_pattern) or expanded_normalized == expanded_pattern.rstrip('/'):
            return True
        return False


def is_path_allowed(file_path: str, allowed_paths: List[Dict[str, Any]], operation: str) -> bool:
    """Check if file_path has the given operation permitted by any allowed-path entry."""
    for entry in allowed_paths:
        if _match_allowed(file_path, entry["path"]):
            if operation in entry["allow"]:
                return True
    return False


def _extract_paths_from_command(command: str) -> List[str]:
    """Extract file/directory paths from a command string.

    Looks for tokens that look like file paths (starting with /, ~/, ./, or containing /).
    Also handles quoted paths.
    """
    paths = []
    # Match quoted strings and unquoted path-like tokens
    # Quoted paths
    for m in re.finditer(r'''["']([^"']+)["']''', command):
        candidate = m.group(1)
        if '/' in candidate or candidate.startswith('.'):
            paths.append(os.path.expanduser(candidate))
    # Unquoted path-like tokens
    for token in re.split(r'\s+', command):
        # Strip quotes
        token = token.strip("\"'")
        if token.startswith(('/','~/','./')):
            paths.append(os.path.expanduser(token))
        elif '/' in token and not token.startswith('-'):
            paths.append(os.path.expanduser(token))
    return paths


def _infer_operation_from_reason(reason: str) -> str:
    """Infer the required operation from a bash pattern's reason field."""
    reason_lower = reason.lower()
    if "rm" in reason_lower or "delet" in reason_lower or "trash" in reason_lower or "rmdir" in reason_lower:
        return "delete"
    if "chmod" in reason_lower or "permission" in reason_lower:
        return "chmod"
    if "chown" in reason_lower or "chgrp" in reason_lower:
        return "chmod"
    if "mv" in reason_lower or "move" in reason_lower:
        return "move"
    return "write"


def _command_targets_allowed_path(command: str, allowed_paths: List[Dict[str, Any]], operation: str) -> bool:
    """Check if ALL file paths in the command have the required operation permitted.

    Security: ALL paths must match (not any). This prevents commands like
    `rm /tmp/safe.txt /etc/passwd` from being allowed just because /tmp is allowlisted.
    """
    if not allowed_paths:
        return False
    cmd_paths = _extract_paths_from_command(command)
    if not cmd_paths:
        return False
    for cmd_path in cmd_paths:
        if not is_path_allowed(cmd_path, allowed_paths, operation):
            return False
    return True


# ============================================================================
# PATH CHECKING
# ============================================================================

def check_path_patterns(command: str, path: str, patterns: List[Tuple[str, str]], path_type: str) -> Tuple[bool, str]:
    """Check command against a list of patterns for a specific path.

    Supports both:
    - Literal paths: ~/.bashrc, /etc/hosts (prefix matching)
    - Glob patterns: *.lock, *.md, src/* (glob matching)
    """
    if is_glob_pattern(path):
        # Glob pattern - convert to regex for command matching
        glob_regex = glob_to_regex(path)
        for pattern_template, operation in patterns:
            # For glob patterns, we check if the operation + glob appears in command
            # e.g., "rm *.lock" should match DELETE_PATTERNS with *.lock
            try:
                # Build a regex that matches: operation ... glob_pattern
                # Extract the command prefix from pattern_template (e.g., '\brm\s+.*' from '\brm\s+.*{path}')
                cmd_prefix = pattern_template.replace("{path}", "")
                if cmd_prefix and re.search(cmd_prefix + glob_regex, command, re.IGNORECASE):
                    return True, f"Blocked: {operation} operation on {path_type} {path}"
            except re.error:
                continue
    else:
        # Original literal path matching (prefix-based)
        expanded = os.path.expanduser(path)
        escaped_expanded = re.escape(expanded)
        escaped_original = re.escape(path)

        for pattern_template, operation in patterns:
            # Check both expanded path (/Users/x/.ssh/) and original tilde form (~/.ssh/)
            pattern_expanded = pattern_template.replace("{path}", escaped_expanded)
            pattern_original = pattern_template.replace("{path}", escaped_original)
            try:
                if re.search(pattern_expanded, command) or re.search(pattern_original, command):
                    return True, f"Blocked: {operation} operation on {path_type} {path}"
            except re.error:
                continue

    return False, ""


def check_command(command: str, config: Dict[str, Any]) -> Tuple[bool, bool, str]:
    """Check if command should be blocked or requires confirmation.

    Returns: (blocked, ask, reason)
      - blocked=True, ask=False: Block the command
      - blocked=False, ask=True: Show confirmation dialog
      - blocked=False, ask=False: Allow the command

    Evaluation order:
      1. Hard-blocked bash patterns (bypassable=false or unset) → BLOCK always
      2. Ask patterns → ASK always
      3. Bypassable bash patterns → check if ALL target paths have the required permission
      4. zeroAccessPaths → check allowlist with "read" permission
      5. readOnlyPaths → check allowlist with operation-specific permission
      6. noDeletePaths → check allowlist with "delete" permission
    """
    patterns = config.get("bashToolPatterns", [])
    zero_access_paths = config.get("zeroAccessPaths", [])
    read_only_paths = config.get("readOnlyPaths", [])
    no_delete_paths = config.get("noDeletePaths", [])
    allowed_paths = load_allowed_paths(config)

    # Phase 1: Hard-blocked and ask bash patterns (NEVER bypassed by allowlist)
    # Phase 2: Bypassable bash patterns (can be overridden by allowlist)
    bypassable_matches = []

    for item in patterns:
        pattern = item.get("pattern", "")
        reason = item.get("reason", "Blocked by pattern")
        should_ask = item.get("ask", False)
        bypassable = item.get("bypassable", False)

        try:
            if re.search(pattern, command, re.IGNORECASE):
                if should_ask:
                    return False, True, reason  # Ask always
                elif bypassable:
                    bypassable_matches.append((pattern, reason))
                else:
                    return True, False, f"Blocked: {reason}"  # Hard block
        except re.error:
            continue

    # Phase 2: Check bypassable matches against allowlist
    for pattern, reason in bypassable_matches:
        operation = _infer_operation_from_reason(reason)
        if not _command_targets_allowed_path(command, allowed_paths, operation):
            return True, False, f"Blocked: {reason}"

    # 3. Check for ANY access to zero-access paths (including reads)
    for zero_path in zero_access_paths:
        if is_glob_pattern(zero_path):
            glob_regex = glob_to_regex(zero_path)
            try:
                file_path_regex = r'(?:^|[\s/="\'<>])' + glob_regex + r'(?:[\s"\')<>]|$)'
                if re.search(file_path_regex, command, re.IGNORECASE):
                    extension = zero_path.split('*')[-1] if '*' in zero_path else zero_path
                    if extension.startswith('.'):
                        extension = extension[1:]
                    if extension:
                        method_call_regex = r'\w\.' + re.escape(extension) + r'\s*\('
                        if re.search(method_call_regex, command):
                            continue
                    if _command_targets_allowed_path(command, allowed_paths, "read"):
                        continue
                    return True, False, f"Blocked: zero-access pattern {zero_path} (no operations allowed)"
            except re.error:
                continue
        else:
            expanded = os.path.expanduser(zero_path)
            escaped_expanded = re.escape(expanded)
            escaped_original = re.escape(zero_path)

            if re.search(escaped_expanded, command) or re.search(escaped_original, command):
                if _command_targets_allowed_path(command, allowed_paths, "read"):
                    continue
                return True, False, f"Blocked: zero-access path {zero_path} (no operations allowed)"

    # 4. Check for modifications to read-only paths (reads allowed)
    for readonly in read_only_paths:
        blocked, reason = check_path_patterns(command, readonly, READ_ONLY_BLOCKED, "read-only path")
        if blocked:
            # Infer which operation from the reason
            op = "write"
            reason_lower = reason.lower()
            if "delete" in reason_lower:
                op = "delete"
            elif "chmod" in reason_lower or "chown" in reason_lower or "chgrp" in reason_lower:
                op = "chmod"
            elif "move" in reason_lower:
                op = "move"
            elif "edit" in reason_lower:
                op = "edit"
            if _command_targets_allowed_path(command, allowed_paths, op):
                continue
            return True, False, reason

    # 5. Check for deletions on no-delete paths (read/write/edit allowed)
    for no_delete in no_delete_paths:
        blocked, reason = check_path_patterns(command, no_delete, NO_DELETE_BLOCKED, "no-delete path")
        if blocked:
            if _command_targets_allowed_path(command, allowed_paths, "delete"):
                continue
            return True, False, reason

    return False, False, ""


# ============================================================================
# MAIN
# ============================================================================

def main() -> None:
    config = load_config()

    # Read hook input from stdin
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON input: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading input: {e}", file=sys.stderr)
        sys.exit(1)

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    # Only check Bash commands
    if tool_name != "Bash":
        sys.exit(0)

    command = tool_input.get("command", "")
    if not command:
        sys.exit(0)

    # Check the command
    is_blocked, should_ask, reason = check_command(command, config)

    if is_blocked:
        # Log blocked command
        log_blocked("Bash", command, reason)
        print(f"SECURITY: {reason}", file=sys.stderr)
        print(f"Command: {command[:100]}{'...' if len(command) > 100 else ''}", file=sys.stderr)
        sys.exit(2)
    elif should_ask:
        # Log ask pattern
        log_asked("Bash", command, reason)
        # Output JSON to trigger confirmation dialog
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": reason
            }
        }
        print(json.dumps(output))
        sys.exit(0)
    else:
        # Log allowed command
        log_allowed("Bash", command, user_approved=False)
        sys.exit(0)


if __name__ == "__main__":
    main()
