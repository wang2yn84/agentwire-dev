> Living document. Update this, don't create new versions.

# Mission: Telegram Bridge (Phase 1)

## Status: Planning

## Summary

Thin Telegram bot that maps messages to existing agentwire CLI commands. Makes agentwire accessible from any phone, anywhere. No new session logic — just a new input/output channel to the existing infrastructure.

## Architecture

```
Telegram Bot API
    ↕
agentwire/bridges/telegram.py (polling or webhook)
    ↕
agentwire CLI (send, output, say, list, etc.)
    ↕
tmux sessions (unchanged)
```

## Core Features

| Telegram Input | AgentWire Action |
|----------------|------------------|
| Text message | `agentwire send -s <session> "text"` |
| Voice note | STT transcribe → `agentwire send` |
| `/list` command | `agentwire list --json` |
| `/session <name>` | Switch active session context |
| `/output` | `agentwire output -s <session>` |
| `/kill <name>` | `agentwire kill -s <name>` |
| `/new <name>` | `agentwire new -s <name>` |

| AgentWire Output | Telegram Action |
|------------------|-----------------|
| `agentwire say` | Send as voice note (TTS → opus) |
| `agentwire alert` | Send as text message |
| Session output (polling) | Send as text (formatted) |
| Permission prompts | Inline keyboard buttons |

## Key Decisions

- **Polling vs Webhook**: Start with polling (simpler, no public URL needed)
- **Session binding**: Each Telegram chat binds to one active session at a time
- **Auth**: Telegram user ID whitelist (single-user, personal tool)
- **Output delivery**: Hook-based (idle hook triggers Telegram notification) + on-demand `/output`
- **Voice notes back**: TTS → ogg/opus → Telegram voice message API

## Prior Art

| Project | Approach | Takeaway |
|---------|----------|----------|
| CCBot | tmux + JSONL transcript polling | Best session mapping |
| CCC | Go, systemd, Whisper transcription | Production patterns |
| Claudegram | Agent SDK + Grammy | Voice I/O patterns |
| hanxiao/claudecode-telegram | Write-only + stop hook | Simplest viable bridge |

## Dependencies

- `python-telegram-bot` or `aiogram` (async Telegram framework)
- Existing: STT server, TTS (RunPod/Chatterbox), agentwire CLI

## CLI Integration

```bash
# New commands
agentwire telegram start       # start bot (in tmux or foreground)
agentwire telegram stop        # stop bot
agentwire telegram status      # check bot health

# Config in ~/.agentwire/config.yaml
telegram:
  bot_token: "..."             # from @BotFather
  allowed_users: [123456789]   # Telegram user IDs
  default_session: "main"      # default session to send messages to
  voice_replies: true          # send TTS as voice notes
  output_polling: false        # continuous output polling (vs hook-based)
```
