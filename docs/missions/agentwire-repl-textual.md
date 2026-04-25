> Living document. Update this, don't create new versions.

# Mission: Agentwire REPL — Textual TUI Rewrite

A from-scratch rendering layer for `agentwire repl` built on [Textual](https://github.com/textualize/textual). The current `prompt_toolkit` line-oriented loop hit its ceiling: there's no way to keep streaming partial output visible without flooding the chat history, no proportional layout, no borders/titles, no docked status line, no scrollable subregion for "what claude is doing right now". Textual gives all of that out of the box.

**Phase of:** own mission (sibling of `agentwire-repl.md`)
**Status:** **draft — scoping (2026-04-25). No code yet.**
**Depends on:** `agentwire-repl.md` Phases 1-5 (complete)
**Blocks:** future REPL feature work that needs richer layout (mode badges, modal permission prompts, inline waveform for /say, etc.)

## Why this rewrite

The user's framing (2026-04-25): *"we can stream the partials, but we need a way to keep them contained, they should get only some percentage of total height lines we have in the current display, so the input is always ideal near the bottom (with maybe a status line under it if we add one) and the chat history above and all the way to the top. But we could add the 'current action' as like 20% of the height we have to work with and have the text scroll there like in a subpane."*

That's a layout requirement, not a rendering tweak. `prompt_toolkit` *can* express it — `HSplit([history, Frame(action, title="..."), input], weights=[6, 2, 1])` — but our current code uses prompt_toolkit as a line-oriented prompt only (events go to raw stdout, prompt_toolkit owns just the input buffer). Adding a real layout means rewriting the rendering loop end-to-end. If we're rewriting it anyway, Textual is the better target:

| | prompt_toolkit (current) | Textual (proposed) |
|---|---|---|
| Layout primitives | low-level (HSplit/VSplit/Window) | declarative (Vertical/Horizontal/Grid + CSS) |
| Borders + titles | `Frame` widget | `Static`/`Container` + CSS border |
| Scrollable subregion | manual | `RichLog` built for it |
| Status line | manual | `Footer` + dock="bottom" |
| Modals (e.g. permission prompts) | hand-rolled | `Screen.push_screen` |
| Async event integration | yes | first-class |
| Dev tools | none | `textual console`, snapshot tests |
| Stars / momentum | 9k, mature | 25k, very active |
| Cost of "do nothing fancy" | high | medium |

Textual loses terminal scrollback by default (it's a full-screen app), but inside a tmux pane that doesn't matter — and the in-app history widget is its own infinite scroll. We gain way more than we lose.

## Architecture sketch

```
┌─ AgentwireREPL (Textual App) ─────────────────────────────┐
│ ┌─ Header ───────────────────────────────────────────────┐ │
│ │ agentwire repl · sdk-bypass · claude-opus-4-7 · roles… │ │
│ └────────────────────────────────────────────────────────┘ │
│ ┌─ ChatHistory (RichLog, fr=6) ──────────────────────────┐ │
│ │ > previous user turn                                   │ │
│ │ assistant final text                                   │ │
│ │ [tool: Bash → ls /tmp]                                 │ │
│ │ [tool result: ...]                                     │ │
│ │ assistant final text                                   │ │
│ │ ↑ scrolls; pinned to bottom on new content             │ │
│ └────────────────────────────────────────────────────────┘ │
│ ┌─ CurrentAction (RichLog, fr=2, border, title) ─────────┐ │
│ │ ╭─ Current action ─────────────────────────────────╮   │ │
│ │ │ thinking: planning the file structure...        │   │ │
│ │ │ thinking: deciding section ordering...          │   │ │
│ │ │ tool input (Write fanfic.html):                  │   │ │
│ │ │   <!doctype html><html><head><meta charset...   │   │ │
│ │ │   <style>body { font-family: ... }</style>      │   │ │
│ │ │   ↑ streams in real time, scrolls within frame  │   │ │
│ │ ╰──────────────────────────────────────────────────╯   │ │
│ └────────────────────────────────────────────────────────┘ │
│ ┌─ Input (TextArea, dock=bottom) ────────────────────────┐ │
│ │ > tell me about prompt caching                         │ │
│ └────────────────────────────────────────────────────────┘ │
│ ┌─ StatusLine (Footer, dock=bottom) ─────────────────────┐ │
│ │ 3 turns · 1.2k tok · $0.04 · effort=high · af_heart    │ │
│ └────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────┘
```

- **ChatHistory** — finalized turns. Tool calls collapse to one-line summaries.
- **CurrentAction** — live partial-message stream. Cleared at end of turn (the assistant block + tool result get appended to ChatHistory in collapsed form).
- **Input** — multi-line `TextArea`, Alt+Enter newline, Enter submit. `@`-mention auto-complete from `Glob` against cwd would be a nice phase-2 add.
- **StatusLine** — running totals, mode, role, voice. Replaces the bottom_toolbar.
- **Modals** — `sdk-prompted` permission prompts pop a `ModalScreen` instead of inline y/n/a.

## Phases

### Phase 1 — Parity (target: 1 wk)

Reach feature parity with the current prompt_toolkit REPL.

- Textual app skeleton (`agentwire/repl/textual_app.py`)
- ChatHistory + Input wired; SDK client streams events into ChatHistory
- All slash commands work (`/help`, `/cost`, `/tools`, `/model`, `/clear`, `/save`, `/resume`, `/effort`, `/thinking`, `/say`, `/run-workflow`, `/exit`)
- @-mention expansion (existing `agentwire.repl.mentions` reused)
- Transcript persistence (existing `agentwire.repl.persistence` reused)
- MCP, damage-control, role layering, voice (all already in `build_options`, no changes)
- Print mode (`-p`) keeps the existing single-shot path — Textual app only fires for interactive
- TTY detection: non-TTY (piped stdin, scheduler) falls back to a non-Textual path that mimics today's behavior. **Required** — otherwise we break workflow `human_gate` and the print-mode contract.
- **Success criteria:** every test in `tests/unit/test_repl_*.py` still passes (with adjusted fixtures); the seeded smoke test (echo prompt | agentwire repl) prints assistant content; an interactive Opus 4.7 session feels at least as good as today's.

### Phase 2 — Layout features (target: 1 wk)

What this rewrite was actually for.

- **CurrentAction RichLog** with border + title — partial messages stream here in real time, cleared on turn complete
- **Proportional weights** — chat=6 / action=2 / input=1 by default, configurable via `/layout` slash command
- **Header** — session metadata pinned at top
- **StatusLine (Footer)** — running totals, refreshed every event
- **Tool-call collapse** — finished tool calls in ChatHistory show `[Bash · ls -la · 12 files]` not the full streamed input
- **Permission prompts as modal** — `sdk-prompted` pops `ModalScreen` with allow/deny/always buttons
- **Color theming** — pull `tts.theme` (or new `repl.theme`) from `~/.agentwire/config.yaml`
- **Success criteria:** the 120-second silent gap that prompted this mission is gone; user always knows what claude is doing

### Phase 3 — Polish (target: ~1 wk)

- @-mention autocomplete (Glob against cwd, dropdown overlay)
- Slash-command palette (`Ctrl+P` opens fuzzy command picker)
- Inline cost graph (sparkline of per-turn cost)
- Transcript scrubber (jump to any prior turn)
- Snapshot tests using Textual's snapshot framework
- Doc: short `docs/repl-tui.md` walkthrough with screenshots
- **Success criteria:** the REPL is good enough to use over Claude Code for agentwire-network-flavored work — that's the original mission's bar

### Phase 4+ — Trigger-driven

Same posture as `agentwire-repl.md` Phase 6+. Don't pre-build:

- Multiplexer view (multiple SDK sessions in one Textual app)
- Workflow-run inline (run a workflow inside the REPL with live node-state cards)
- Inline waveform for /say
- Plugin widgets

## Open questions

- **Migration strategy:** ship Textual behind a flag (`AGENTWIRE_REPL_TUI=textual`) and run side-by-side until parity is proven, then flip default? Or branch-and-replace? Leaning side-by-side — the current REPL is good enough that we don't need to remove it on day 1.
- **Print mode:** stays on the prompt_toolkit / stdout path? It has no UX requirements that need Textual. Likely yes — print mode is fire-and-forget, no benefit from a TUI.
- **Workflow human_gate:** does it spawn the Textual app or stay on the simple line-mode? Probably needs Textual for parity once Textual is the default — humans reviewing a gate want the same UX they get in standalone REPL. But Textual-from-inside-a-workflow has tmux/TTY constraints to think through.
- **Resume / scroll-back of long transcripts:** `RichLog` handles this natively; verify with a 10K-line transcript.
- **Snapshot tests:** Textual has `pytest-textual-snapshot`. Worth adding to the test stack as part of Phase 3.

## Non-goals

- Replacing `pi` or `claude` REPLs — agentwire repl stays the agentwire-native harness; the others stay as they are.
- Cross-platform Windows-native polish — best-effort, but Linux + macOS terminals are the target.
- Rewriting the workflow `human_gate` runner — it just calls `run_repl(seed_message=...)`, which will route to whichever REPL implementation is active.

## Code references (for the rewrite)

- `agentwire/repl/app.py` — current entry point. The `_run_interactive` function is what gets replaced; everything from `build_options` downward is reused.
- `agentwire/repl/state.py` — `ReplState` dataclass; carries through unchanged.
- `agentwire/repl/commands.py` — slash commands; reused unchanged.
- `agentwire/repl/persistence.py` — transcript JSONL; reused unchanged.
- `agentwire/repl/mentions.py` — @-mention expansion; reused unchanged.
- `agentwire/repl/damage_control.py` — `make_pre_tool_hook`; reused unchanged.
- `agentwire/repl/context.py` — role/voice loading; reused unchanged.

The rewrite is the rendering layer only. Everything below is intact.

## Dependencies

- `textual >= 0.80` (latest stable as of 2026-04)
- `pytest-textual-snapshot` (test-only, Phase 3)
