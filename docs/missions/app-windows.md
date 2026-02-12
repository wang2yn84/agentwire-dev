> Living document. Update this, don't create new versions.

# App Windows — Agent Visual Canvas

**Status:** Phase 1 in progress

## Problem

AgentWire agents can only output text to terminals. When agents generate diagrams, dashboards, HTML prototypes, or data visualizations, they have no way to display them visually in the portal. Users must manually open files or copy URLs.

## Solution

Add a visual layer to the portal desktop: agents write HTML/web content and display it as sandboxed iframe windows. This turns AgentWire from a terminal orchestrator into a full visual workspace.

## Phase 1: Core Infrastructure (Current)

### What Ships

- **App window type** — New WinBox window containing a sandboxed iframe
- **File serving** — `/apps/` static route serves from `~/.agentwire/apps/`
- **Two MCP tools** — `desktop_open_app(url, title)` and `desktop_write_app(filename, html_content, title)`
- **CLI command** — `agentwire open <url> [--title] [--app-id] [--json]`
- **Upload endpoint** — `POST /api/apps/upload` for writing HTML files via portal API
- **Smart sandboxing** — Local files get `allow-scripts allow-same-origin`, external URLs get `allow-scripts allow-forms allow-popups` (no same-origin)

### Architecture

```
Agent (MCP tool)
  → desktop_write_app(filename="dashboard.html", html_content="<h1>...</h1>")
  → POST /api/apps/upload  (writes file to ~/.agentwire/apps/)
  → broadcast desktop_open_window {type: "app", url: "/apps/dashboard.html"}
  → Portal JS creates WinBox with sandboxed iframe
```

### Files Changed

| File | Change |
|------|--------|
| `agentwire/config.py` | `AppsConfig` dataclass (dir, max_size_mb) |
| `agentwire/server.py` | `/apps/` static route, `"app"` type in desktop_open, upload endpoint |
| `agentwire/static/js/app-window.js` | New `AppWindow` class |
| `agentwire/static/js/desktop.js` | Wire up `window_type === 'app'` routing |
| `agentwire/static/css/desktop.css` | Iframe container styles |
| `agentwire/mcp_server.py` | `desktop_open_app` + `desktop_write_app` tools |
| `agentwire/__main__.py` | `open` subcommand |
| `CLAUDE.md` | Document new tools |

## Phase 2: Management & Discovery

- `desktop_list_apps` MCP tool — list files in `~/.agentwire/apps/`
- App deletion — `desktop_delete_app(filename)` tool
- Agent workflow docs — best practices for generating app content

## Phase 3: Widget Persistence

- `apps.yaml` manifest — pin favorite apps with position/size
- Startup auto-open — pinned apps open when portal starts
- Apps menu in portal menubar — browse and launch saved apps
