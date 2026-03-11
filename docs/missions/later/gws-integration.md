> Living document. Update this, don't create new versions.

# Mission: Google Workspace CLI Integration

Integrate `gws` (`@googleworkspace/cli`) into agentwire so agents can interact with Gmail, Drive, Calendar, Sheets, and other Google Workspace services.

## Context

Google quietly released a CLI for all Workspace APIs in March 2026. It's pre-v1.0 and "not officially supported" but already very capable. Reference doc: `docs/gws-google-workspace-cli.md`.

## What This Enables

- **Scheduled tasks** that draft email digests, read calendars, update tracking sheets
- **Orchestrators** that can send emails, create Drive folders, log task results to Sheets
- **Voice-triggered workflows** like "add that to my calendar" or "email the team"
- **Morning briefings** enriched with real calendar/email data (vs. generic prompts)

## Proposed Approach

`gws` is a plain CLI tool — agents use it via shell commands, same as `gh`, `git`, etc. No special agentwire integration needed beyond:

1. **Auth setup** — one-time OAuth setup per machine, credentials stored in keyring
2. **Role additions** — add `gws` usage guidance to relevant roles (e.g. `worker`, `task-runner`)
3. **Scheduler tasks** — example tasks that use `gws` for real data in pre/prompt phases
4. **Documentation** — CLAUDE.md or role files pointing agents to the gws reference doc

## Auth Considerations

- For interactive sessions: `gws auth login -s gmail,drive,calendar` (browser OAuth)
- For scheduled/headless tasks: export credentials once, store in `~/.agentwire/` or use a service account
- Env var: `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE` — can be set per-task in scheduler config

## Out of Scope (for now)

- MCP integration (gws has a built-in MCP server but CLI is sufficient for agent use)
- Building a native agentwire wrapper — gws CLI is the right abstraction level
