> Living document. Update this, don't create new versions.

# Mission: Google Workspace CLI Integration

**Status: Complete**

Integrate `gws` (`@googleworkspace/cli`) into agentwire so agents can interact with Gmail, Drive, Calendar, Sheets, and other Google Workspace services.

## What Shipped

- **Auth setup** — `gws-auth` helper script at `~/.agentwire/scripts/gws-auth` (symlinked to `~/bin/gws-auth`). Authenticates a Google account with standard agentwire scopes in one command.
- **Multi-account support** — Multiple Google accounts via `GOOGLE_WORKSPACE_CLI_CONFIG_DIR` env var, each with its own `~/.config/gws-<name>/` directory.
- **Tooldef** — `agentwire/tooldefs/gws.yaml` documents all gws commands with correct syntax, access levels, and multi-account usage instructions.
- **Memory** — Agent memory at `project_gws_setup.md` captures authed accounts, granted scopes, and how-to-add-account guide.

## Authed Accounts (dotdev Mac)

| Account | Config Dir | Usage |
|---------|-----------|-------|
| `dotdevdotdev@gmail.com` | `~/.config/gws/` | Default, no env var needed |
| `jordangarygerard@gmail.com` | `~/.config/gws-jordan/` | `GOOGLE_WORKSPACE_CLI_CONFIG_DIR=~/.config/gws-jordan gws ...` |

## Granted Scopes

| Service | Scope |
|---------|-------|
| Gmail | Read-only |
| Drive | Read-only |
| Calendar | Full access (read + create/update/delete events) |
| Cloud Platform | Read-only |

## Auth Setup Pattern

```bash
# Auth a new account
gws-auth ~/.config/gws-<name>
# Sign in as target account when browser opens
```

Prerequisites for new accounts:
1. Add as test user on OAuth consent screen in GCP Console
2. Grant `Service Usage Consumer` IAM role on `dotdev-workspace` project

## Key Notes

- `GOOGLE_WORKSPACE_CLI_ACCOUNT` does NOT exist in gws v0.16.0 — only `GOOGLE_WORKSPACE_CLI_CONFIG_DIR` works
- Always use `--params '{"userId":"me"}'` — gws requires explicit userId
- Space-separated subcommands: `gws gmail users messages list`, not dot-notation

## Out of Scope

- MCP integration (gws has a built-in MCP server but CLI is sufficient for agent use)
- Building a native agentwire wrapper — gws CLI is the right abstraction level
