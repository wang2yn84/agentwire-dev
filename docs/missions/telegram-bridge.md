> Living document. Update this, don't create new versions.

# Mission: Telegram Bridge (Phase 1)

## Status: Planning

## Summary

Thin Telegram bot that maps messages to existing agentwire CLI commands. Makes agentwire accessible from any phone, anywhere. No new session logic — just a new input/output channel to the existing infrastructure.

## Library Choice: aiogram v3

| Library | Verdict | Reason |
|---------|---------|--------|
| **aiogram v3** | **Winner** | Built on aiohttp (same as portal), `start_polling()` is a coroutine, clean voice API, Pydantic callback data |
| python-telegram-bot | Rejected | Uses httpx (second HTTP lib), `run_polling()` blocks event loop, manual lifecycle dance needed |
| Telethon | Rejected | MTProto overkill, needs api_id+api_hash, uncertain v2 future |

## Architecture

```
Telegram Bot API
    ↕ (polling)
agentwire/bridges/telegram.py
    ├── text → run_agentwire_cmd(["send", "-s", session, text])
    ├── voice → STT → run_agentwire_cmd(["send", ...])
    ├── /commands → run_agentwire_cmd(["list"|"output"|...])
    ├── callback → run_agentwire_cmd(["send", "-s", session, option])
    │
    ├── portal WS ← subscribe to session events
    │   ├── question → inline keyboard
    │   ├── say/audio → WAV→OGG/Opus → voice note
    │   └── alert → text message
    │
    └── config from ~/.agentwire/config.yaml
```

**Key principle:** The bot is a standalone process. It talks to sessions via CLI (`run_agentwire_cmd`) and receives events by subscribing to the portal WebSocket. No new logic in the portal — the bot is a client.

## Implementation Plan

### Step 1: Bot Skeleton + Text Messages

**Files:** `agentwire/bridges/telegram.py`

Core bot with aiogram polling loop:
- `TelegramBridge` class with `start()` / `stop()` lifecycle
- User ID whitelist auth middleware
- Per-user active session tracking (in-memory dict)
- Text message handler → `run_agentwire_cmd(["send", "-s", session, text])`
- Bot commands:
  - `/start` — greeting, show active sessions
  - `/list` — `agentwire list --json` → formatted session list
  - `/s <name>` — switch active session (short alias)
  - `/output` — `agentwire output -s <session>` → last N lines
  - `/new <name>` — create new session
  - `/kill <name>` — kill session (with confirmation keyboard)

**CLI integration:** `agentwire telegram start|stop|status`
- `start`: launches bot in tmux session `agentwire-telegram` (like portal/tts/stt)
- `stop`: kills the tmux session
- `status`: check if running + bot info

**Config:**
```yaml
# ~/.agentwire/config.yaml
telegram:
  bot_token: ""                  # from @BotFather (or TELEGRAM_BOT_TOKEN env)
  allowed_users: []              # Telegram user IDs
  default_session: "main"        # fallback session
```

**Test:** Send text from Telegram → see it arrive in tmux session. Run `/list` → see sessions.

### Step 2: Voice Notes In (STT)

**Receive voice → transcribe → send to session**

Flow:
1. aiogram receives voice message (OGG/Opus)
2. Download via `bot.download(message.voice)` → BytesIO
3. Transcribe:
   - **Primary:** POST to STT server (`config.stt.url`) — same endpoint portal uses
   - **Fallback:** Write to temp file → `whisperkit-cli` (existing CLI fallback logic)
4. Send transcribed text: `run_agentwire_cmd(["send", "-s", session, text])`
5. Reply with transcription confirmation: "Sent to {session}: {text}"

**Reuse:** Follow the same STT flow as `agentwire/listen.py::transcribe_via_server()`.

**Test:** Send voice note from phone → see transcribed text arrive in session.

### Step 3: Portal WebSocket Subscription (Outbound Events)

**Subscribe to portal WS → forward events to Telegram**

The bot connects to `wss://localhost:8765/ws/{session}` for each active session (same as browser monitor mode). Events to handle:

| Portal WS Event | Telegram Action |
|------------------|-----------------|
| `{"type": "question", ...}` | Inline keyboard with options |
| `{"type": "output", "data": ...}` | Debounced text message (only on significant change) |
| `{"type": "audio", "data": ...}` | WAV→OGG/Opus → voice note |

**Question handling (inline keyboards):**
```python
# Portal sends: {"type": "question", "question": "Ready?", "options": [{"index": 1, "text": "Yes"}, ...]}
# Bot creates: InlineKeyboardMarkup with numbered buttons
# User taps button → bot sends option number to session via /api/answer/{session}
```

**Output forwarding:**
- NOT continuous polling — too noisy
- Only forward on significant output change (debounce 5s, skip if output unchanged)
- Strip ANSI codes before sending
- Truncate to Telegram's 4096 char limit, link to portal for full view

**Audio (TTS voice notes):**
- Portal broadcasts `{"type": "audio", "data": "<base64_wav>"}` when `agentwire say` is called
- Bot decodes base64 → WAV bytes
- Convert WAV → OGG/Opus: `ffmpeg -i pipe:0 -c:a libopus -b:a 128k -f ogg pipe:1`
- Send as voice note via `bot.send_voice(chat_id, voice=BufferedInputFile(ogg_bytes))`

**Test:** Agent calls `say("hello")` → receive voice note on phone. Agent asks question → see keyboard buttons.

### Step 4: Alert Routing

**Extend alert system to include Telegram as a destination**

Two approaches (both):

1. **Bot-side:** The portal WS subscription already captures output. When idle hook fires and alerts appear in output, the bot naturally sees them. No change needed.

2. **Direct route:** Add Telegram notification to the queue processor (`~/.agentwire/queue-processor.sh`) or create a new notification channel:
   - New function in `agentwire/notifications.py`: `send_telegram(chat_id, text, bot_token)`
   - Called from queue processor alongside `agentwire alert`
   - Config: `telegram.notify_on: [alert, idle, task_complete]`

**Test:** Worker goes idle → get Telegram notification with summary.

### Step 5: Service Management

**Manage Telegram bot like other agentwire services**

```yaml
# ~/.agentwire/config.yaml
services:
  telegram:
    machine: null           # null = local
    session_name: "agentwire-telegram"
```

```bash
agentwire telegram start    # creates tmux session, runs bot
agentwire telegram stop     # kills tmux session
agentwire telegram status   # check running + connection health
agentwire doctor            # includes telegram bot check
```

**Portal integration (optional):**
- Add `/api/telegram/status` endpoint
- Show Telegram connection status on portal dashboard
- "Send to Telegram" button on session output (forward snippet)

## File Structure

```
agentwire/
├── bridges/
│   ├── __init__.py
│   └── telegram.py          # TelegramBridge class, handlers, WS subscriber
├── __main__.py              # + cmd_telegram() for start/stop/status
├── notifications.py         # + send_telegram() helper
└── ...

# No changes to:
# - server.py (portal is unchanged, bot is a WS client)
# - mcp_server.py (no new MCP tools needed yet)
# - hooks/ (idle hook unchanged, queue processor optionally extended)
```

## Config Schema

```yaml
telegram:
  bot_token: ""              # from @BotFather (or TELEGRAM_BOT_TOKEN env var)
  allowed_users: []          # list of Telegram user IDs (integers)
  default_session: "main"    # session to send to when no /s selected
  voice_replies: true        # convert TTS to voice notes (vs text)
  forward_output: false      # continuous output forwarding (noisy, default off)
  forward_questions: true    # forward AskUserQuestion as inline keyboards
  forward_alerts: true       # forward alerts to Telegram
  notify_on:                 # extra notification triggers
    - idle                   # session goes idle
    - task_complete          # scheduled task completes
```

## Dependencies

```
aiogram>=3.20
```

Plus existing: `aiohttp`, `ffmpeg` (for WAV→OGG), STT server, TTS backend.

## Audio Pipeline

```
Voice In:  Telegram OGG/Opus → download bytes → POST to STT server → text → send to session
Voice Out: agentwire say → TTS → WAV → ffmpeg → OGG/Opus → Telegram voice note
```

WAV→OGG conversion (in-process via ffmpeg subprocess):
```python
proc = await asyncio.create_subprocess_exec(
    "ffmpeg", "-i", "pipe:0", "-c:a", "libopus", "-b:a", "128k", "-f", "ogg", "pipe:1",
    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
)
ogg_data, _ = await proc.communicate(input=wav_bytes)
```

## Security

- **Auth:** Telegram user ID whitelist only. No token/password. IDs are integers that can't be spoofed via Telegram Bot API.
- **Single user:** This is a personal tool. No multi-tenant considerations.
- **No public URL:** Polling mode, no webhook endpoint exposed.
- **Session access:** Bot can only interact with sessions the agentwire CLI can see (same machine permissions).

## What's NOT in Phase 1

- Webhook mode (needs public URL / Cloudflare tunnel)
- File/image sharing (send screenshots, code files)
- Diff visualization
- Multi-chat support (one chat = one user)
- MCP tool for Telegram (e.g., `telegram_send()` from agents)
- Agent SDK integration (Phase 2)
- Message bus abstraction (Phase 3)

## Prior Art & Lessons

| Project | Key Takeaway |
|---------|-------------|
| CCBot | JSONL transcript polling is cleaner than capture-pane for output |
| CCC | systemd/launchd service management, voice note transcription patterns |
| hanxiao | Simplest viable approach: write-only + stop hook for responses |
| Claudegram | Agent SDK + Grammy shows voice I/O is straightforward |

**Our advantage:** We already have STT, TTS, session management, idle hooks, alert routing, and a portal WebSocket. These projects built all of that from scratch. We just need the Telegram adapter layer.
