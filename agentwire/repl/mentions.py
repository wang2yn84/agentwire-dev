"""@-mention expansion for the agentwire REPL.

Users can include file contents inline by typing `@path/to/file.py` in their
turn. Before the prompt is sent to the SDK, every mention is expanded to a
fenced block carrying the file's contents. Globs are supported (`@src/*.py`).

Design choices:
- Strings inside backticks or quotes are skipped — `@foo` in code stays
  literal so the user can talk about a token like "@decorator" without
  triggering expansion.
- Mentions cap at MAX_FILE_BYTES per file (default 64 KiB); larger files
  are clipped with a marker. Keeps the prompt-token bill bounded.
- Globs cap at MAX_GLOB_FILES (default 10); above that we list the matched
  paths instead of expanding (the user can refine).
- Missing files turn into an `@<path> (not found)` marker so the model
  isn't silently confused about why expected content didn't arrive.
"""

from __future__ import annotations

import glob as glob_mod
import re
from dataclasses import dataclass
from pathlib import Path

MAX_FILE_BYTES = 64 * 1024
MAX_GLOB_FILES = 10

# A mention is `@` followed by one or more path-y characters. We accept
# letters/digits/`._-/*?[]~` (no spaces). The leading `@` must not be
# inside backticks (handled by stripping code spans first) and must be
# preceded by start-of-line or whitespace so emails like `foo@bar.com`
# don't trigger.
_MENTION_RE = re.compile(r"(?:(?<=^)|(?<=\s))@([A-Za-z0-9._\-/~][A-Za-z0-9._\-/*?\[\]~]*)")


@dataclass
class ExpandedMention:
    raw: str        # e.g. "@src/main.py"
    target: str     # the path string after @
    rendered: str   # the substitution text (fenced code block, list, or marker)


def expand_mentions(text: str, cwd: Path | None = None) -> tuple[str, list[ExpandedMention]]:
    """Expand `@path` mentions inline. Returns (new_text, list_of_expansions).

    Mentions inside backtick code spans are left untouched.
    """
    if not text or "@" not in text:
        return text, []

    cwd = cwd or Path.cwd()
    placeholders, scrubbed = _strip_code_spans(text)

    expansions: list[ExpandedMention] = []

    def _replace(match: re.Match) -> str:
        raw = match.group(0)
        target = match.group(1)
        # Heuristic skip — bare alphanumerics with no separator are probably
        # not paths (e.g. "@somebody" mentions). Require a `.`, `/`, or glob
        # char somewhere; otherwise leave alone.
        if not any(c in target for c in "./*?[~"):
            return raw
        rendered = _render_mention(target, cwd)
        expansions.append(ExpandedMention(raw=raw, target=target, rendered=rendered))
        return rendered

    expanded = _MENTION_RE.sub(_replace, scrubbed)
    restored = _restore_code_spans(expanded, placeholders)
    return restored, expansions


def _render_mention(target: str, cwd: Path) -> str:
    """Resolve `target` to a rendered substitution string."""
    if any(c in target for c in "*?[") :
        return _render_glob(target, cwd)
    p = (cwd / target).expanduser().resolve() if not target.startswith("/") else Path(target).expanduser()
    if not p.exists():
        return f"`@{target}` (not found)"
    if p.is_dir():
        return _render_directory(p, target)
    return _render_file(p, target)


def _render_file(path: Path, target: str) -> str:
    try:
        raw = path.read_bytes()
    except Exception as exc:
        return f"`@{target}` (read error: {exc})"
    truncated = False
    if len(raw) > MAX_FILE_BYTES:
        raw = raw[:MAX_FILE_BYTES]
        truncated = True
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return f"`@{target}` (binary file, {len(raw)} bytes)"
    fence = _pick_fence(text)
    suffix = path.suffix.lstrip(".") or ""
    header = f"\n{fence}{suffix}\n# {target}\n{text}"
    if not text.endswith("\n"):
        header += "\n"
    header += fence
    if truncated:
        header += f"\n(truncated to {MAX_FILE_BYTES} bytes; full file is larger)"
    return header


def _render_directory(path: Path, target: str) -> str:
    entries = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir())
    if not entries:
        return f"`@{target}/` (empty directory)"
    return f"`@{target}/` (directory: {', '.join(entries[:30])}{'…' if len(entries) > 30 else ''})"


def _render_glob(pattern: str, cwd: Path) -> str:
    matches = sorted(
        glob_mod.glob(pattern, root_dir=str(cwd), recursive="**" in pattern)
    )
    if not matches:
        return f"`@{pattern}` (no matches)"
    if len(matches) > MAX_GLOB_FILES:
        listing = "\n".join(f"- {m}" for m in matches[:MAX_GLOB_FILES])
        return (
            f"`@{pattern}` matched {len(matches)} files; first {MAX_GLOB_FILES}:\n"
            f"{listing}\n(use a tighter pattern to expand inline)"
        )
    rendered_parts = [_render_file(cwd / m, m) for m in matches]
    return "\n".join(rendered_parts)


def _pick_fence(text: str) -> str:
    """Choose a code fence longer than any backtick run inside `text`."""
    longest = 0
    run = 0
    for ch in text:
        if ch == "`":
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return "`" * max(3, longest + 1)


# -- code-span scrubbing (so @ inside `code` is ignored) --

_CODE_SPAN_RE = re.compile(r"(`+)([^`]*)\1")
_PLACEHOLDER_FMT = "\x00CSPAN{}\x00"


def _strip_code_spans(text: str) -> tuple[list[str], str]:
    placeholders: list[str] = []

    def _take(m: re.Match) -> str:
        placeholders.append(m.group(0))
        return _PLACEHOLDER_FMT.format(len(placeholders) - 1)

    return placeholders, _CODE_SPAN_RE.sub(_take, text)


def _restore_code_spans(text: str, placeholders: list[str]) -> str:
    for i, original in enumerate(placeholders):
        text = text.replace(_PLACEHOLDER_FMT.format(i), original)
    return text
