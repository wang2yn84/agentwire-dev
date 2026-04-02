> Living document. Update this, don't create new versions.

# Mission: Channels — Pluggable Communication Integrations

**Status:** Planning

## Summary

Organize all communication integrations (Telegram, email, voice, future Slack/Discord) into a consistent, discoverable, pluggable pattern. Then add Slack + Discord as built-in channels, and document how developers can add their own.

## Why

Today each integration is wired independently — Telegram is a standalone bot process in `bridges/telegram.py`, email is a utility in `notifications.py`, voice is deeply integrated across `tts_server.py`, `stt/`, `listen.py`, `voiceclone.py`, and the portal. Config is scattered (`tts:`, `stt:`, `telegram:`, `notifications.email:`). Adding a new channel means reverse-engineering each existing integration. There's no pattern to follow, no auto-discovery, no consistent config.

All communication channels ultimately exist to get messages to/from Claude Code sessions. The channel is just the transport. A clean channel architecture makes the transport pluggable without touching session logic.

---

## Current State (What Exists Today)

### Telegram
- **Code:** `agentwire/bridges/telegram.py` (~650 lines)
- **Config:** `telegram:` top-level in config.yaml (NOT in a dataclass — loaded via raw dict)
  - `bot_token`, `allowed_users`, `default_session`, `voice_replies`, `forward_questions`, `forward_alerts`
  - Env fallbacks: `TELEGRAM_AGENTWIRE_BOT_TOKEN`, `TELEGRAM_USER_ID`
- **CLI:** `agentwire telegram start|serve|stop|status`
- **MCP tools:** None (no MCP tools for Telegram — managed as a service only)
- **Service:** Runs in tmux session `agentwire-telegram`, aiogram polling + WebSocket subscription to portal
- **State:** Per-user session tracking in `~/.agentwire/telegram-state.json`
- **Inbound flow:** Telegram message → `handle_text()` → `agentwire send -s {session} {text}`
- **Outbound flow:** Portal WebSocket → `_handle_ws_event()` → voice notes or text messages
- **Dependencies:** `aiogram`, `aiohttp`, `ffmpeg` (voice note conversion)
- **In-chat commands:** `/start`, `/list`, `/s {name}`, `/output`, `/new`, `/kill`, `/help`

### Email
- **Code:** `agentwire/notifications.py` (~325 lines)
- **Template:** `agentwire/templates/email_notification.html`
- **Config:** `notifications.email:` in config.yaml → `EmailConfig` dataclass in config.py
  - `api_key` (or `RESEND_API_KEY` env var), `from_address`, `default_to`
  - Branding: `banner_image_url`, `echo_image_url`, `echo_small_url`, `logo_image_url`
- **CLI:** `agentwire email --body "..." --to addr --subject "..." --attach file`
- **MCP tool:** `email_send(body, to, subject, attachments, plain_text)` in mcp_server.py
- **Service:** None — stateless HTTP calls to Resend.com API
- **Outbound flow:** Agent → `email_send()` MCP → CLI → `send_email()` → Resend API → email delivered
- **Dependencies:** `resend`, `jinja2`, `markdown`

### Voice (TTS + STT + Say + Listen + VoiceClone)

Voice is the primary real-time interface. It's NOT a "channel" in the same sense — it's deeply integrated into the portal and agent experience. For this refactor, voice gets documented in the channels list for discoverability but is NOT moved or restructured.

- **TTS server:** `agentwire/tts_server.py` + `agentwire/tts/` (engines: chatterbox, kokoro, qwen, zonos)
  - Config: `tts:` → `TTSConfig` (backend, url, default_voice, voices_dir, exaggeration, etc.)
  - CLI: `agentwire tts start|serve|stop|status|restart`
  - Service: FastAPI + Uvicorn in tmux `agentwire-tts`, port 8100
  - MCP: `tts_status()`

- **STT server:** `agentwire/stt/stt_server.py` + backends (moonshine, whisper)
  - Config: `stt:` → `STTConfig` (url, timeout)
  - CLI: `agentwire stt start|serve|stop|status`
  - Service: FastAPI + Uvicorn in tmux `agentwire-stt`, port 8101
  - MCP: `stt_status()`

- **Say (TTS output):** Portal route `POST /api/say/{session}` → TTS server → WebSocket audio broadcast
  - CLI: `agentwire say "text" [-s session] [-v voice]`
  - MCP: `say(text, session, voice)`
  - Routing: browser if connected, local speakers if not

- **Listen (STT input):** `agentwire/listen.py` — ffmpeg recording + transcription
  - CLI: `agentwire listen start|stop|cancel`
  - MCP: `listen_start()`, `listen_stop()`, `listen_cancel()`
  - Transcription chain: STT server → whisperkit-cli fallback

- **Voice cloning:** `agentwire/voiceclone.py` — record samples + upload to TTS backend
  - CLI: `agentwire voiceclone start|stop|list|delete|cancel`
  - MCP: `voiceclone_start()`, `voiceclone_stop()`, `voiceclone_list()`, `voiceclone_delete()`, `voiceclone_cancel()`
  - MCP: `voices_list()`, `transcribe()`

- **Alert (text notification):** No audio, just text to parent session
  - CLI: `agentwire alert "text" [--to session]`
  - MCP: `alert(text, to)`

**Dependencies:** torch, torchaudio, fastapi, uvicorn, transformers, ffmpeg, whisperkit-cli (varies by backend)

---

## Phases

### Phase 1: Refactor existing integrations into `agentwire/channels/`

**Goal:** Move Telegram and email into a channels directory with consistent structure. Voice gets a thin wrapper for discoverability. All existing functionality unchanged.

**Target structure:**
```
agentwire/channels/
├── __init__.py          # Channel registry + discovery
├── base.py              # Base channel class
├── telegram/
│   ├── __init__.py      # TelegramChannel class
│   ├── bot.py           # Bot logic (from bridges/telegram.py)
│   └── config.py        # TelegramConfig dataclass
├── email/
│   ├── __init__.py      # EmailChannel class
│   ├── sender.py        # Send logic (from notifications.py)
│   ├── config.py        # EmailConfig dataclass (moved from config.py)
│   └── templates/
│       └── notification.html  # Branded template (moved from templates/)
├── voice/
│   └── __init__.py      # VoiceChannel — thin wrapper, docs only, not restructured
```

**Base channel interface (`base.py`):**
```python
class Channel:
    """Base class for communication channels."""

    name: str                    # "telegram", "email", "discord"
    enabled: bool                # From config
    has_service: bool = False    # Needs start/stop lifecycle?

    def get_config(self) -> dict:
        """Return channel config for display/discovery."""

    # Service lifecycle (for channels with long-running processes)
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def status(self) -> dict: ...

    # Outbound (agent → user)
    async def send_text(self, text: str, **kwargs) -> None: ...
    async def send_voice(self, audio: bytes, **kwargs) -> None: ...

    # Not all channels support all methods — NotImplementedError is fine
```

**Channel registry (`__init__.py`):**
```python
def discover_channels() -> list[Channel]:
    """Auto-discover channel modules in this directory."""
    # Scan agentwire/channels/ for modules with a Channel subclass
    # Filter by enabled in config
    # Return instantiated channels

def get_channel(name: str) -> Channel | None:
    """Get a specific channel by name."""

def list_channels() -> list[dict]:
    """List all channels with status for CLI/MCP."""
```

**Config migration:**
```yaml
# OLD (scattered):
telegram:
  bot_token: "..."
notifications:
  email:
    api_key: "..."
tts:
  backend: "kokoro"

# NEW (consolidated under channels:):
channels:
  telegram:
    enabled: true
    bot_token: "..."
    allowed_users: [8669226777]
    default_session: "main"
    voice_replies: true
    forward_questions: true
    forward_alerts: true

  email:
    enabled: true
    api_key: ""           # or RESEND_API_KEY env var
    from_address: "Echo <echo@agentwire.dev>"
    default_to: "user@example.com"
    # Branding
    banner_image_url: ""
    echo_image_url: ""
    echo_small_url: ""
    logo_image_url: ""

  voice:
    enabled: true         # Always true when portal runs
    # Voice config stays at top-level tts:/stt: sections
    # This entry is for channel discovery only
```

**Backwards compatibility:** During migration, config loading checks both old paths (`telegram:`, `notifications.email:`) and new path (`channels.telegram:`, `channels.email:`). New path takes precedence. Old paths work indefinitely — no forced migration.

**New CLI:**
- `agentwire channels list` — show all channels with enabled/disabled status
- `agentwire channels list --json` — JSON output for MCP

**New MCP tool:**
- `channels_list()` — agents discover what communication channels are available

**Existing CLI preserved:** `agentwire telegram start`, `agentwire email`, etc. all continue to work — they just import from the new location.

**Files to move/refactor:**

| From | To | Notes |
|------|----|-------|
| `agentwire/bridges/telegram.py` | `agentwire/channels/telegram/bot.py` | Split into Channel class + bot logic |
| `agentwire/notifications.py` | `agentwire/channels/email/sender.py` | Email send logic |
| `agentwire/templates/email_notification.html` | `agentwire/channels/email/templates/notification.html` | Template |
| Config in `config.py` (EmailConfig) | `agentwire/channels/email/config.py` | Dataclass stays similar |
| New | `agentwire/channels/base.py` | Base channel class |
| New | `agentwire/channels/__init__.py` | Registry + discovery |
| New | `agentwire/channels/voice/__init__.py` | Thin wrapper for discoverability |

**Done when:**
- [ ] All existing functionality unchanged (Telegram bot, email, voice all work as before)
- [ ] Code organized in `agentwire/channels/`
- [ ] `agentwire channels list` shows: telegram, email, voice with enabled/disabled
- [ ] `channels_list()` MCP tool works
- [ ] Config supports both old and new paths
- [ ] Tests: telegram start/stop, email send, say/listen all pass

### Phase 2: Add Slack and Discord as built-in channels

**Discord:**
- `agentwire/channels/discord/` — follows same pattern as telegram
- Bot using `discord.py` library
- DM-based: messages route to/from sessions (like Telegram)
- In-chat commands: similar to Telegram (`/list`, `/send`, `/output`)
- Config: `channels.discord.bot_token`, `guild_ids`, `allowed_users`, `default_session`
- Service: Runs in tmux session `agentwire-discord`
- CLI: `agentwire discord start|serve|stop|status` (or `agentwire channels discord start`)

**Slack:**
- `agentwire/channels/slack/` — follows same pattern
- Bot using `slack-bolt` SDK
- DM and channel-based messaging
- Slash commands: `/aw list`, `/aw send session "msg"`, `/aw output session`
- Config: `channels.slack.bot_token`, `signing_secret`, `app_token` (for Socket Mode), `default_session`
- Service: Socket Mode (no public URL needed) in tmux session `agentwire-slack`
- CLI: `agentwire slack start|serve|stop|status`

**Done when:**
- [ ] Discord bot handles DMs to/from sessions, voice note support
- [ ] Slack bot handles DMs, channel mentions, slash commands
- [ ] Both show in `agentwire channels list`
- [ ] Both follow channel pattern from Phase 1
- [ ] Config sections documented

### Phase 3: Channel development guide + example template

**Files:**
- `agentwire/channels/_example.py` — annotated example channel (underscore = not auto-registered)
- `docs/channels.md` — developer guide

**Guide covers:**
1. Channel directory structure
2. Base class methods to implement
3. Config section registration
4. Inbound message handling (routing to sessions via `agentwire send`)
5. Outbound message handling (subscribing to portal WebSocket events)
6. Service lifecycle (start/stop for long-running bots)
7. MCP tool registration (optional channel-specific tools)
8. Testing checklist
9. Publishing/sharing custom channels

**The example channel** should be a minimal but complete implementation (~100 lines) that a developer can copy and modify. Something like a webhook channel that receives/sends via HTTP.

**Done when:**
- [ ] A developer can read the guide, copy the example, and have a working custom channel
- [ ] Custom channels in `agentwire/channels/` are auto-detected
- [ ] Example demonstrates: config, inbound, outbound, service lifecycle

---

## Architecture Notes

- Channels are **not** an abstraction over voice. Voice is the primary real-time interface and stays deeply integrated with the portal. The voice "channel" is just a discoverability wrapper.
- Each channel **owns its own MCP tools**. Telegram gets `telegram_send()` if needed, Slack gets `slack_send()`. No forced generic interface that removes agency from agents.
- Agents can call `channels_list()` to discover what's available, then use channel-specific tools.
- A `notify(message)` convenience tool could broadcast to all enabled channels, but channel-specific tools remain the primary interface.
- **Service management** follows existing patterns: tmux sessions, `start|serve|stop|status` CLI.

## Overnight Suitability

- **Phase 1** is a great overnight candidate — refactoring existing code into new structure with clear prep. The session needs: "here's every file involved, here's the target structure, here's the base class, consolidate without breaking anything."
- **Phase 2** (Slack/Discord) — coding could run overnight after human provisions bot tokens and does initial API setup.
- **Phase 3** — documentation, better done interactively.
