"""Extract named outputs from a NodeResult into the workflow context.

Supported sources:
  text     — whole final assistant message (with optional strip)
  regex    — first regex match group against final text
  jsonpath — simple JSONPath-lite against final text parsed as JSON

tool_result extraction is planned (needs content-block walking); out of
scope for PR B — fail validation if anyone declares it for now.
"""

from __future__ import annotations

import json
import re
from typing import Any

from agentwire.workflows.node import NodeResult, OutputSpec


class OutputExtractionError(Exception):
    """Raised when a required output cannot be extracted."""


def _apply_jsonpath(data: Any, path: str) -> Any:
    """Tiny JSONPath: supports $.a, $.a.b, $.a[*], $.a[*].b, $.a[0].

    Not a full JSONPath implementation — just what's needed for node outputs.
    Use jsonpath-ng later if we grow into it.
    """
    if not path.startswith("$"):
        raise ValueError(f"jsonpath must start with $, got: {path!r}")

    cursor: Any = data
    # Strip leading $ and optional .
    rest = path[1:]
    if rest.startswith("."):
        rest = rest[1:]

    # Tokenize: split by '.' but keep [*] and [N] attached
    tokens: list[str] = []
    buf = ""
    i = 0
    while i < len(rest):
        ch = rest[i]
        if ch == ".":
            if buf:
                tokens.append(buf)
                buf = ""
        elif ch == "[":
            close = rest.index("]", i)
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(rest[i:close + 1])
            i = close
        else:
            buf += ch
        i += 1
    if buf:
        tokens.append(buf)

    for tok in tokens:
        if tok == "[*]":
            if not isinstance(cursor, list):
                raise ValueError(f"[*] applied to non-list at {path!r}")
            # Downstream tokens apply to each element
            cursor = list(cursor)
        elif tok.startswith("[") and tok.endswith("]"):
            idx_str = tok[1:-1]
            try:
                idx = int(idx_str)
            except ValueError as e:
                raise ValueError(f"bad index {tok!r} in {path!r}") from e
            if isinstance(cursor, list) and isinstance(cursor, list):
                cursor = cursor[idx]
            else:
                raise ValueError(f"{tok} applied to non-list at {path!r}")
        else:
            if isinstance(cursor, list):
                # Apply key lookup to every element (e.g. $.a[*].b)
                cursor = [(c.get(tok) if isinstance(c, dict) else None) for c in cursor]
            elif isinstance(cursor, dict):
                cursor = cursor.get(tok)
            else:
                raise ValueError(f"cannot get {tok!r} from {type(cursor).__name__} at {path!r}")
    return cursor


def _extract_one(spec: OutputSpec, node_result: NodeResult) -> Any:
    text = node_result.final_text or ""

    if spec.source == "text":
        if spec.pattern:
            # pattern acts as a strip-to-fence hint, e.g. "```json" → strip fences
            stripped = text.strip()
            if spec.pattern and spec.pattern in stripped:
                parts = stripped.split(spec.pattern)
                if len(parts) >= 2:
                    return parts[1].split("```")[0].strip()
            return stripped
        return text.strip()

    if spec.source == "regex":
        match = re.search(spec.pattern, text, re.DOTALL | re.MULTILINE)
        if not match:
            raise OutputExtractionError(
                f"regex {spec.pattern!r} did not match output of node[{node_result.node_id}]"
            )
        return match.group(1) if match.groups() else match.group(0)

    if spec.source == "jsonpath":
        # Try to locate a JSON blob: prefer fenced ```json ... ```,
        # fall back to the whole text (trimmed).
        candidate = text.strip()
        fence = re.search(r"```(?:json)?\s*\n(.*?)```", candidate, re.DOTALL)
        if fence:
            candidate = fence.group(1).strip()
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as e:
            raise OutputExtractionError(
                f"node[{node_result.node_id}] output is not valid JSON: {e}"
            ) from e
        return _apply_jsonpath(parsed, spec.pattern)

    raise OutputExtractionError(f"unknown source: {spec.source!r}")


def extract_outputs(
    specs: list[OutputSpec],
    node_result: NodeResult,
) -> tuple[dict[str, Any], list[str]]:
    """Extract all declared outputs; return (values, soft_errors).

    Required-output failures raise OutputExtractionError immediately.
    Optional (required=False) failures are collected as soft_errors.
    """
    values: dict[str, Any] = {}
    soft_errors: list[str] = []
    for spec in specs:
        try:
            values[spec.name] = _extract_one(spec, node_result)
        except OutputExtractionError as e:
            if spec.required:
                raise
            soft_errors.append(f"{spec.name}: {e}")
            values[spec.name] = None
    return values, soft_errors
