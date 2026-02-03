# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.2.0] - 2026-02-03

### Added

- Persistent STT server (`agentwire stt start`) to eliminate cold start delays - transcriptions now complete in ~0.3-0.5s instead of 3-5s
- STT server uses faster-whisper with openai-whisper fallback, supports model selection via `--model` flag
- `listen.py` now tries STT server first, falls back to whisperkit-cli if unavailable

### Changed

- Default STT port changed from 8100 to 8101 to avoid conflict with TTS server

### Fixed

- Email body text contrast improved (`#d0d0d0` → `#e8e8e8`) for better readability on dark backgrounds

## [1.1.0] - 2026-02-01

### Added

- MCP server for external agent integration with tools for sessions, machines, and transcription
- Scheduled workloads with `ensure` and `exit_on_complete` options and lock management CLI
- Email notifications via Resend with branded templates and banner header
- TTS improvements: queued audio playback, orphaned task handling, model-specific roles
- Standalone voice role for non-orchestration use; support `voice: random` in project config
- Progressive loading for faster UI feedback and real-time session updates
- UI enhancements: combo button in projects list, delete project action, reusable ListCard component

### Changed

- Roles refactored for consistency; leader role made agent-agnostic; delegation roles authoritative
- Roles and docs updated to favor MCP tools over direct CLI commands
- Updated assets and splash screens with layered foreground images

### Fixed

- Numerous CLI and portal fixes: correct `--type` handling for remote sessions; proper session removal broadcasts; correct sessions data access in monitor
- Health/exit behavior for scheduled tasks; TTS error messages surfaced; directory auto-creation on custom paths
- Email template layout fixes (full-width banner, proper aspect ratios) and HTML detection to prevent escaping
- Worktree guidance and examples corrected; GLM-only delegation role enforced; pane spawn examples require explicit `pane_type`

### Documentation

- Expanded docs: scheduled workloads spec, lock management commands, email notifications, MCP tools and roles
- Added brainstorm docs (context compression, transcripts, worker streaming, ambient context, audio cues)
- Updated project URLs and YouTube channel descriptions; clarified MCP tools worktree limitations

### Chore

- Asset cleanup and refresh (logos, splash images, transparent and black-bg variants)
- Git hygiene and ignore updates; example/demo script additions

## [1.0.0] - 2026-01-19

Initial public release of AgentWire.

### Added

- **Desktop Control Center** - WinBox-powered window management with draggable/resizable session windows
- **Session Windows** - Monitor mode (read-only output) or Terminal mode (full xterm.js) per session
- **Push-to-Talk Voice** - Hold to speak, release to send transcription from any device
- **TTS Playback** - Agent responses spoken back via browser audio with smart routing
- **Multi-Device Access** - Control sessions from phone, tablet, or laptop on your network
- **Git Worktrees** - Multiple agents work the same project in parallel on separate branches
- **Remote Machines** - Orchestrate Claude Code sessions on remote servers via SSH
- **Safety Hooks** - 300+ dangerous command patterns blocked (rm -rf, git push --force, secret exposure)
- **Session Roles** - Orchestrator sessions coordinate voice, workers execute focused tasks
- **Permission Hooks** - Claude Code integration for permission dialogs in the portal

### CLI Commands

- `agentwire init` - Interactive setup wizard
- `agentwire portal start/stop/status` - Portal management
- `agentwire tts start/stop/status` - TTS server management
- `agentwire stt start/stop/status` - STT server management
- `agentwire new/list/kill/send/output` - Session management
- `agentwire spawn/split/detach/jump` - Pane management
- `agentwire say` - TTS with smart audio routing
- `agentwire safety check/status/logs` - Security diagnostics
- `agentwire machine add/remove/list` - Remote machine management
- `agentwire tunnels up/down/status` - SSH tunnel management
- `agentwire history list/show/resume` - Session history
- `agentwire doctor` - Auto-diagnose and fix issues
- `agentwire generate-certs` - SSL certificate generation

### Security

- Damage control hooks protecting against 300+ dangerous command patterns
- Zero-access paths for credentials, SSH keys, and API tokens
- Read-only paths for system configs
- No-delete paths for session and mission files
- Audit logging for all security decisions

### Documentation

- Comprehensive README with platform-specific installation instructions
- Architecture documentation
- Troubleshooting guide
- TTS setup guide
- Remote machines guide
- Security documentation

[1.0.0]: https://github.com/dotdevdotdev/agentwire-dev/releases/tag/v1.0.0

[1.1.0]: https://github.com/dotdevdotdev/agentwire-dev/compare/v1.0.0...v1.1.0
[Unreleased]: https://github.com/dotdevdotdev/agentwire-dev/compare/v1.1.0...HEAD
