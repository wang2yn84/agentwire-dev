> Living document. Update this, don't create new versions.

# Mission: Channels — Pluggable Communication Integrations

**Status:** Planning

## Summary

Organize all communication integrations into a layered architecture: **primitives** (voice/TTS/STT) as infrastructure that channels consume, **service channels** (Telegram, Discord, Slack) as bidirectional long-lived transports, and **send-only channels** (email, SMS, webhook) as stateless outbound transports. Then document how developers can add their own.

---

## Architecture: Three Layers

### Layer 1: Primitives (Infrastructure)

Voice/TTS/STT are **not channels** — they're capabilities that any channel can consume. TTS generates audio. STT transcribes audio. These are tools in the agent's toolkit and infrastructure that channels build on.

| Primitive | What it does | Already used by |
|-----------|-------------|-----------------|
| **TTS** | Text → audio | `say()` agent tool, Telegram voice notes, (future) email audio attachments |
| **STT** | Audio → text | `listen()` agent tool, Telegram voice messages |
| **Voice cloning** | Record + upload voice samples | TTS engine selection |

**Primitives stay where they are.** `tts_server.py`, `stt/`, `listen.py`, `voiceclone.py` — no move, no refactor. They're infrastructure, not channels. Channels import and use them.

`say()`, `listen()`, `alert()` remain top-level agent MCP tools — they're direct agent-to-user primitives, not channel-mediated.

### Layer 2: Service Channels (Bidirectional)

Long-lived processes that bridge external platforms to agentwire sessions. They handle both inbound (user → session) and outbound (session → user). They maintain state and connections.

| Channel | Library | Inbound | Outbound | Status |
|---------|---------|---------|----------|--------|
| **Telegram** | aiogram | Messages, voice notes → session | WebSocket events → voice notes, text | Exists |
| **Discord** | discord.py | DMs, commands → session | Events → text, voice notes | Planned |
| **Slack** | slack-bolt | DMs, slash commands, mentions → session | Events → text, threads | Planned |
| **Email inbox** | (IMAP or webhook) | Inbound emails → session | — | Future |

**Service channel pattern:**
- Runs in its own tmux session (`agentwire-{name}`)
- CLI: `agentwire {name} start|serve|stop|status`
- Subscribes to portal WebSocket for outbound events
- Routes inbound messages to sessions via `agentwire send`
- Maintains per-user state (which user → which session)
- Can use primitives (TTS for voice notes, STT for voice message transcription)

### Layer 3: Send-Only Channels (Outbound)

Stateless, fire-and-forget outbound. No process, no inbound, no service lifecycle. Agent calls send, message goes out.

| Channel | Backend | Status |
|---------|---------|--------|
| **Email** | Resend.com API | Exists |
| **SMS** | Twilio API | Planned |
| **Webhook** | HTTP POST to URL | Planned |

**Send-only channel pattern:**
- No service process
- Just a `send()` function
- Config: API keys, defaults (recipient, from address, URL)
- MCP tool: `{name}_send()` exposed when enabled

**Note on email:** Email sending is send-only. Email inbox monitoring (watching for replies, routing to sessions) would be a separate service channel. They share config but are different archetypes. Email inbox monitoring is a future item — not part of this mission.

**Note on email refactor:** The current `notifications.py` likely has significant refactor opportunities (template rendering, markdown conversion, attachment handling all in one file). When implementing Phase 1, do a thorough review of this code and clean it up.

---

## Current State (What Exists Today)

### Telegram (Service Channel)
- **Code:** `agentwire/bridges/telegram.py` (~650 lines)
- **Config:** `telegram:` top-level in config.yaml (NOT in a dataclass — loaded via raw dict)
  - `bot_token`, `allowed_users`, `default_session`, `voice_replies`, `forward_questions`, `forward_alerts`
  - Env fallbacks: `TELEGRAM_AGENTWIRE_BOT_TOKEN`, `TELEGRAM_USER_ID`
- **CLI:** `agentwire telegram start|serve|stop|status`
- **MCP tools:** None (managed as a service only)
- **Service:** tmux session `agentwire-telegram`, aiogram polling + portal WebSocket subscription
- **State:** `~/.agentwire/telegram-state.json`
- **Inbound:** Telegram message → `handle_text()` → `agentwire send -s {session} {text}`
- **Outbound:** Portal WebSocket → `_handle_ws_event()` → voice notes or text
- **In-chat commands:** `/start`, `/list`, `/s {name}`, `/output`, `/new`, `/kill`, `/help`
- **Dependencies:** `aiogram`, `aiohttp`, `ffmpeg`

### Email (Send-Only Channel)
- **Code:** `agentwire/notifications.py` (~325 lines)
- **Template:** `agentwire/templates/email_notification.html`
- **Config:** `notifications.email:` → `EmailConfig` dataclass
  - `api_key` (or `RESEND_API_KEY` env), `from_address`, `default_to`
  - Branding: `banner_image_url`, `echo_image_url`, `echo_small_url`, `logo_image_url`
- **CLI:** `agentwire email --body "..." --to addr --subject "..." --attach file`
- **MCP tool:** `email_send(body, to, subject, attachments, plain_text)`
- **Service:** None — stateless Resend.com API calls
- **Dependencies:** `resend`, `jinja2`, `markdown`

### Voice (Primitives — NOT a channel)
- **TTS:** `tts_server.py` + `tts/` engines — FastAPI on port 8100, tmux `agentwire-tts`
- **STT:** `stt/stt_server.py` — FastAPI on port 8101, tmux `agentwire-stt`
- **Say:** Portal route → TTS → WebSocket audio broadcast. MCP: `say(text, session, voice)`
- **Listen:** `listen.py` — ffmpeg recording + transcription. MCP: `listen_start/stop/cancel()`
- **Voice clone:** `voiceclone.py` — record + upload. MCP: `voiceclone_start/stop/list/delete()`
- **Alert:** Text notification to session. MCP: `alert(text, to)`
- **Config:** `tts:` → `TTSConfig`, `stt:` → `STTConfig` (stay at top level, not under channels)

---

## Phases

### Phase 1: Refactor existing integrations into `agentwire/channels/`

**Goal:** Move Telegram and email into channels directory with consistent structure based on archetypes. Primitives stay where they are. All existing functionality unchanged.

**Target structure:**
```
agentwire/channels/
├── __init__.py                    # Registry, discovery, list_channels()
├── base.py                        # ServiceChannel + SendOnlyChannel base classes
├── telegram/
│   ├── __init__.py                # TelegramChannel(ServiceChannel)
│   ├── bot.py                     # Bot logic (from bridges/telegram.py)
│   └── config.py                  # TelegramConfig dataclass
├── email/
│   ├── __init__.py                # EmailChannel(SendOnlyChannel)
│   ├── sender.py                  # Send logic (refactored from notifications.py)
│   ├── config.py                  # EmailConfig dataclass
│   └── templates/
│       └── notification.html      # Branded HTML template
```

**Base classes (`base.py`):**
```python
class ServiceChannel:
    """Bidirectional channel with long-lived service process."""
    name: str
    enabled: bool

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def status(self) -> dict: ...

    # Outbound handled via portal WebSocket subscription (per channel)
    # Inbound handled via platform SDK → agentwire send

class SendOnlyChannel:
    """Stateless outbound-only channel."""
    name: str
    enabled: bool

    async def send(self, text: str, **kwargs) -> dict: ...
```

**Registry (`__init__.py`):**
```python
def discover_channels() -> list[ServiceChannel | SendOnlyChannel]:
    """Auto-discover channel modules in agentwire/channels/."""

def list_channels() -> list[dict]:
    """List all channels with type, enabled status, config summary."""
```

**Config migration:**
```yaml
# NEW (consolidated):
channels:
  telegram:
    enabled: true
    bot_token: ""
    allowed_users: [8669226777]
    default_session: "main"
    voice_replies: true
    forward_questions: true
    forward_alerts: true

  email:
    enabled: true
    api_key: ""
    from_address: "Echo <echo@agentwire.dev>"
    default_to: "user@example.com"
    banner_image_url: ""
    echo_image_url: ""
    echo_small_url: ""
    logo_image_url: ""

# Primitives stay top-level (NOT under channels):
tts:
  backend: "kokoro"
  url: "http://localhost:8100"
stt:
  url: "http://localhost:8101"
```

**Backwards compat:** Config loading checks both old paths (`telegram:`, `notifications.email:`) and new (`channels.telegram:`, `channels.email:`). New takes precedence. No forced migration.

**New CLI:**
- `agentwire channels list [--json]` — show all channels with type + enabled status

**New MCP tool:**
- `channels_list()` — agents discover available channels

**Existing CLI preserved:** `agentwire telegram start`, `agentwire email`, etc. continue to work.

**Files to move/refactor:**

| From | To | Notes |
|------|----|-------|
| `agentwire/bridges/telegram.py` | `agentwire/channels/telegram/bot.py` | Split into Channel class + bot logic |
| `agentwire/notifications.py` | `agentwire/channels/email/sender.py` | Thorough refactor — review and clean up |
| `agentwire/templates/email_notification.html` | `agentwire/channels/email/templates/notification.html` | Template |
| `EmailConfig` from `config.py` | `agentwire/channels/email/config.py` | Dataclass |
| New | `agentwire/channels/base.py` | ServiceChannel + SendOnlyChannel |
| New | `agentwire/channels/__init__.py` | Registry + discovery |

**Done when:**
- [ ] All existing functionality unchanged (Telegram bot, email send, voice all work)
- [ ] Code organized in `agentwire/channels/`
- [ ] `agentwire channels list` shows: telegram (service), email (send-only)
- [ ] `channels_list()` MCP tool works
- [ ] Config supports both old and new paths
- [ ] Email code reviewed and cleaned up during move

### Phase 2: Add SMS, webhook, Discord, and Slack

**Send-only channels (quick wins):**

**SMS (Twilio):**
- `agentwire/channels/sms/` — `SmsChannel(SendOnlyChannel)`
- Config: `channels.sms.account_sid`, `auth_token`, `from_number`, `default_to`
- MCP: `sms_send(body, to)`
- CLI: `agentwire sms --body "..." --to "+1234567890"`
- Dependencies: `twilio`

**Webhook:**
- `agentwire/channels/webhook/` — `WebhookChannel(SendOnlyChannel)`
- Config: `channels.webhook.url`, `headers`, `method` (POST/PUT)
- MCP: `webhook_send(payload)`
- CLI: `agentwire webhook --body "..." [--url override]`
- Dependencies: `requests` (already available)

**Service channels:**

**Discord:**
- `agentwire/channels/discord/` — `DiscordChannel(ServiceChannel)`
- Bot using `discord.py`
- DM-based: messages route to/from sessions (like Telegram)
- In-chat commands: `/list`, `/send`, `/output`
- Config: `channels.discord.bot_token`, `guild_ids`, `allowed_users`, `default_session`
- Service: tmux `agentwire-discord`
- CLI: `agentwire discord start|serve|stop|status`
- Can use TTS primitive for voice notes in DMs

**Slack:**
- `agentwire/channels/slack/` — `SlackChannel(ServiceChannel)`
- Bot using `slack-bolt` SDK, Socket Mode (no public URL needed)
- DM and channel-based messaging
- Slash commands: `/aw list`, `/aw send session "msg"`, `/aw output session`
- Config: `channels.slack.bot_token`, `app_token`, `signing_secret`, `default_session`
- Service: tmux `agentwire-slack`
- CLI: `agentwire slack start|serve|stop|status`

**Done when:**
- [ ] SMS send works via Twilio
- [ ] Webhook POST works to configured URL
- [ ] Discord bot handles DMs to/from sessions
- [ ] Slack bot handles DMs, mentions, slash commands
- [ ] All show in `agentwire channels list`
- [ ] All follow patterns from Phase 1

### Phase 3: Channel development guide + example template

**Files:**
- `agentwire/channels/_example.py` — annotated example channel (underscore = not auto-registered)
- `docs/channels.md` — developer guide

**Guide covers:**
1. Architecture overview (primitives vs service vs send-only)
2. Directory structure for a new channel
3. `ServiceChannel` vs `SendOnlyChannel` — which to subclass
4. Config section registration
5. How to use primitives (TTS for voice notes, STT for audio transcription)
6. Inbound message handling (routing to sessions via `agentwire send`)
7. Outbound message handling (subscribing to portal WebSocket events)
8. Service lifecycle (start/stop for long-running bots)
9. MCP tool registration
10. Testing checklist
11. Publishing/sharing custom channels

**The example channel** should be a minimal but complete service channel implementation (~100 lines) that a developer can copy and modify — something like a Matrix or IRC bridge.

**Done when:**
- [ ] Developer can read guide, copy example, have working custom channel
- [ ] Custom channels in `agentwire/channels/` auto-detected by registry
- [ ] Example demonstrates: config, inbound, outbound, service lifecycle, primitive usage

---

## Architecture Notes

- **Primitives are infrastructure, not channels.** TTS/STT/voice stay at top level. Channels consume them.
- **Each channel owns its own MCP tools.** `email_send()`, `sms_send()`, `webhook_send()`. No forced generic interface.
- **Agents discover channels via `channels_list()`**, then use channel-specific tools.
- **Service channels follow existing patterns:** tmux sessions, `start|serve|stop|status` CLI, portal WebSocket subscription.
- **Auto-discovery:** Registry scans `agentwire/channels/` for modules with a Channel subclass. No manual registration.

## Open Questions

- **CLI namespace:** Keep `agentwire telegram start` or add `agentwire channels telegram start`? Recommendation: keep both, top-level is shorthand.
- **Email inbox monitoring:** Future service channel, not in this mission. Note it in the guide as an example of how one platform can have both a send-only and service channel.

## Overnight Suitability

- **Phase 1** — great overnight candidate. Prep session with: "here's every file, here's the target structure, consolidate without breaking anything."
- **Phase 2 send-only** (SMS, webhook) — quick, could run overnight easily.
- **Phase 2 service** (Discord, Slack) — needs human to provision bot tokens first, then coding overnight.
- **Phase 3** — documentation, better done interactively.
