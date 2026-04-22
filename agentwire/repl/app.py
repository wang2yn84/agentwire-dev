"""Agentwire REPL application entry point.

Phase 1 scaffold — prints a greeting and echoes stdin so we can verify the
session-type plumbing (enum dispatch, build_agent_command, tmux pane spawn)
works end-to-end before the SDK integration lands in PR 2.
"""

from __future__ import annotations

import sys


BANNER = """\
╭─────────────────────────────────────────────────────────╮
│  agentwire repl — SDK-based interactive harness         │
│  Phase 1 scaffold · mode={mode} · model={model}
╰─────────────────────────────────────────────────────────╯
"""


def run_repl(
    mode: str = "bypass",
    model: str | None = None,
    print_prompt: str | None = None,
    system_prompt: str | None = None,
) -> int:
    """Run the REPL. Returns exit code.

    Phase 1: no claude-agent-sdk integration yet. Just proves plumbing.
    - Interactive: print banner, echo stdin lines with "scaffold" marker,
      exit on Ctrl+D.
    - Print mode (-p PROMPT): print the prompt back, exit.

    SDK client, tool runner, event streaming all land in subsequent PRs.
    """
    model_display = model or "claude-opus-4-7 (default)"

    if print_prompt is not None:
        print(f"[agentwire repl · scaffold · mode={mode} · model={model_display}]")
        print(f"prompt received: {print_prompt}")
        if system_prompt:
            preview = system_prompt.splitlines()[0][:80] if system_prompt else ""
            print(f"system prompt: {len(system_prompt)} chars (first line: {preview!r})")
        print("[Phase 1 scaffold — SDK integration lands in PR 2]")
        return 0

    sys.stdout.write(BANNER.format(mode=mode, model=model_display))
    if system_prompt:
        sys.stdout.write(f"system prompt loaded: {len(system_prompt)} chars\n")
    sys.stdout.write("Phase 1 scaffold — type anything and it echoes back. Ctrl+D to exit.\n\n")
    sys.stdout.flush()

    try:
        while True:
            try:
                line = input("> ")
            except EOFError:
                sys.stdout.write("\n[exit]\n")
                return 0
            except KeyboardInterrupt:
                sys.stdout.write("\n[interrupt — use Ctrl+D to exit]\n")
                continue
            sys.stdout.write(f"scaffold received: {line!r}\n")
            sys.stdout.flush()
    except Exception as exc:
        sys.stderr.write(f"[agentwire repl: unexpected error: {exc}]\n")
        return 1
