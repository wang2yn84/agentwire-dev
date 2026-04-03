> Living document. Update this, don't create new versions.

# Channels — Developer Guide

Communication channels connect external platforms (email, SMS, Discord, Slack, webhooks) to AgentWire sessions. This guide explains how they work and how to add your own.

## Architecture: Three Layers

| Layer | What | Examples |
|-------|------|----------|
| **Primitives** | Infrastructure channels consume | TTS, STT, voice cloning |
| **Service channels** | Bidirectional, long-lived processes | Telegram, Discord, Slack |
| **Send-only channels** | Stateless outbound | Email, SMS, webhook |

**Voice/TTS/STT are primitives, not channels.** They're infrastructure that any channel can consume via the base class. `say()` and `listen()` remain top-level agent tools.

## Quick Start

1. Copy `agentwire/channels/_template.py` → `agentwire/channels/my_channel.py`
2. Define your `MyChannelConfig` dataclass
3. Uncomment `@ChannelRegistry.register("my_channel")`
4. Implement `send()` (send-only) or `start()/stop()/status()` (service)
5. Add import to `agentwire/channels/__init__.py`
6. `agentwire rebuild && agentwire channels list`

## Channel Types

### SendOnlyChannel

Stateless, fire-and-forget outbound. No process, no inbound.

```python
@ChannelRegistry.register("my_channel")
class MyChannel(SendOnlyChannel):
    name = "my_channel"
    config_class = MyConfig
    config_key = "my_channel"

    async def send(self, text, **kwargs) -> ChannelResult:
        # Send the message, return result
        return ChannelResult(success=True, message_id="123")
```

### ServiceChannel

Long-lived process that bridges an external platform to AgentWire sessions. Handles both inbound (user → session) and outbound (session → user).

```python
@ChannelRegistry.register("my_bot")
class MyBotChannel(ServiceChannel):
    name = "my_bot"
    config_class = MyBotConfig
    config_key = "my_bot"

    # Service channels are managed via CLI:
    # agentwire my_bot start|serve|stop|status
```

Service channels typically:
- Run in their own tmux session
- Subscribe to portal WebSocket for outbound events
- Route inbound messages via `self.send_to_session()`
- Maintain per-user state in `~/.agentwire/{name}-state.json`

## Config

Each channel defines its own config dataclass. Config lives in YAML under `channels.{config_key}:`.

```python
@dataclass
class MyConfig:
    api_key: str = ""
    default_recipient: str = ""

    def __post_init__(self):
        if not self.api_key:
            self.api_key = os.environ.get("MY_API_KEY", "")
```

```yaml
# ~/.agentwire/config.yaml
channels:
  my_channel:
    api_key: "your-key"
    default_recipient: "user@example.com"
```

The channel registry automatically loads config from YAML and builds the dataclass. No changes to `config.py` needed.

### Legacy Config (Built-in Only)

Built-in channels can read from old config paths via `legacy_config_key`. Custom channels **cannot** use this — they're restricted to `channels.{name}:` to prevent config key squatting.

## Primitives

The base class provides TTS/STT as infrastructure:

```python
# In your channel code:
audio = await self.tts("Hello!", voice="my_voice")  # → WAV bytes
text = await self.stt(audio_bytes, format="ogg")     # → transcribed text
voices = self.voices_available()                       # → ["default", "custom1", ...]
```

These call the TTS/STT HTTP servers. Channel developers never need to know about server URLs or backends.

## Session Helpers

Interact with AgentWire sessions from your channel:

```python
# Route inbound message to a session
self.send_to_session("main", "User said: hello")

# Read recent output from a session
output = self.get_session_output("main", lines=20)

# List active sessions
sessions = self.list_sessions()  # → [{"name": "main", "status": "idle"}, ...]
```

## Service Channel Lifecycle

Service channels follow the same tmux pattern:

```bash
agentwire my_bot start   # Start in tmux session
agentwire my_bot serve   # Run in foreground (for dev)
agentwire my_bot stop    # Stop tmux session
agentwire my_bot status  # Check if running
```

### Portal WebSocket (Outbound Events)

Subscribe to `wss://localhost:{port}/ws/{session}` for outbound events:

| Event | Fields | Use Case |
|-------|--------|----------|
| `question` | `question`, `options` | Show agent question with buttons |
| `alert` | `text` | Forward alerts to user |
| `audio` | `audio` (base64) | Send voice messages |
| `output` | `text` | Forward session output |

### Inbound Messages

Platform SDK → your handler → `self.send_to_session(session, text)`

## CLI Integration

### Send-Only Pattern

```python
# In __main__.py argparse:
my_parser = subparsers.add_parser("my_channel", help="...")
my_parser.add_argument("--body", "-b", type=str, help="Message body")
my_parser.add_argument("--to", type=str, help="Recipient")
my_parser.add_argument("-q", "--quiet", action="store_true")
my_parser.set_defaults(func=cmd_my_channel)
```

### Service Pattern

```python
my_parser = subparsers.add_parser("my_bot", help="...")
my_sub = my_parser.add_subparsers(dest="my_bot_command")
my_sub.add_parser("start", ...).set_defaults(func=cmd_my_bot_start)
my_sub.add_parser("serve", ...).set_defaults(func=cmd_my_bot_serve)
my_sub.add_parser("stop", ...).set_defaults(func=cmd_my_bot_stop)
my_sub.add_parser("status", ...).set_defaults(func=cmd_my_bot_status)
```

## MCP Tools

Add channel-specific tools in `mcp_server.py`:

```python
@mcp.tool()
def my_channel_send(text: str, to: str | None = None) -> str:
    data = run_agentwire_cmd(["my_channel", "--body", text])
    if data.get("success"):
        return "Sent."
    return f"Error: {data.get('error')}"
```

## Optional Dependencies

Channels with external deps use try/except:

```python
try:
    from twilio.rest import Client
    HAS_TWILIO = True
except ImportError:
    HAS_TWILIO = False
```

`send()` returns a clear error if the dependency is missing.
`channels list` shows all channels regardless of installed deps.

## Security

- `legacy_config_key` is restricted to `BUILTIN_CHANNELS` set
- Custom channels can only read from `channels.{their_name}:` in YAML
- This prevents a custom channel from reading another channel's config

## Testing Checklist

- [ ] `agentwire channels list` shows your channel
- [ ] Config loads correctly from YAML
- [ ] Env var fallback works
- [ ] `send()` succeeds with valid config
- [ ] `send()` returns clear error with invalid/missing config
- [ ] CLI command works (`agentwire my_channel --body "test"`)
- [ ] MCP tool works (if added)
- [ ] For service: start/serve/stop/status all work
- [ ] For service: inbound messages route to sessions
- [ ] For service: outbound events forwarded to users

## Built-in Channels

| Channel | Type | Library | Config Key |
|---------|------|---------|------------|
| Email | send-only | resend | `email` |
| Telegram | service | aiogram | `telegram` |
| Quo | send-only | stdlib | `quo` |
| SMS | send-only | twilio | `sms` |
| Webhook | send-only | stdlib | `webhook` |
| Discord | service | discord.py | `discord` |
| Slack | service | slack-bolt | `slack` |

## Example: Email Has Two Personas

One platform can have both channel types:
- **Email (send-only)** — `EmailChannel` sends branded notifications via Resend
- **Email inbox (service, future)** — would monitor inbound emails and route to sessions

They share config under `channels.email:` but are different channel classes. The send-only channel exists now; the inbox monitor is a future addition.
