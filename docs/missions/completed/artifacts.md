> Living document. Update this, don't create new versions.

# Artifacts — Agent Visual Canvas

**Status:** Phase 2 complete

## Problem

AgentWire agents can only output text to terminals. When agents generate diagrams, dashboards, HTML prototypes, or data visualizations, they have no way to display them visually in the portal. Users must manually open files or copy URLs.

## Solution

Add a visual layer to the portal desktop: agents write HTML/web content and display it as sandboxed iframe windows. This turns AgentWire from a terminal orchestrator into a full visual workspace.

## Phase 1: Core Infrastructure (Complete)

### What Shipped

- **Artifact window type** — New WinBox window containing a sandboxed iframe
- **File serving** — `/artifacts/` static route serves from `~/.agentwire/artifacts/`
- **Two MCP tools** — `desktop_open_artifact(url, title)` and `desktop_write_artifact(filename, html_content, title)`
- **CLI command** — `agentwire open <url> [--title] [--artifact-id] [--json]`
- **Upload endpoint** — `POST /api/artifacts/upload` for writing HTML files via portal API
- **Smart sandboxing** — Local files get `allow-scripts allow-same-origin`, external URLs get `allow-scripts allow-forms allow-popups` (no same-origin)

### Architecture

```
Agent (MCP tool)
  → desktop_write_artifact(filename="dashboard.html", html_content="<h1>...</h1>")
  → POST /api/artifacts/upload  (writes file to ~/.agentwire/artifacts/)
  → broadcast desktop_open_window {type: "artifact", url: "/artifacts/dashboard.html"}
  → Portal JS creates WinBox with sandboxed iframe
```

## Phase 2: Management & Discovery (Complete)

### What Shipped

- **Artifacts menu** — "Artifacts" item in portal top bar opens list window
- **List endpoint** — `GET /api/artifacts` returns file metadata (name, size, mtime)
- **Delete endpoint** — `DELETE /api/artifacts/{filename}` removes files
- **Artifacts list window** — Browse, open, and delete artifacts from the portal UI

## Phase 3: Widget Persistence

- `artifacts.yaml` manifest — pin favorite artifacts with position/size
- Startup auto-open — pinned artifacts open when portal starts
