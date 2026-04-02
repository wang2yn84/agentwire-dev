> Living document. Update this, don't create new versions.

# Mission: Channels — Pluggable Communication Integrations

**Status:** Planning

## Summary

Organize all communication integrations (Telegram, email, voice, future Slack/Discord) into a consistent, discoverable, pluggable pattern. Then add Slack + Discord as built-in channels, and document how developers can add their own.

## Why

Today each integration is wired independently — Telegram is a standalone bot process, email is a utility function, voice is deeply integrated into the portal. Adding a new channel means figuring out each integration from scratch. There's no pattern to follow, no auto-discovery, no consistent config.

All communication channels ultimately exist to get messages to/from Claude Code sessions. The channel is just the transport. A clean channel architecture makes the transport pluggable without touching session logic.

## Phases

### Phase 1: Refactor existing integrations into `agentwire/channels/`

Move and organize existing code:

```
agentwire/channels/
├── __init__.py          # Channel registry, discovery, base class
├── base.py              # Base channel interface
├── telegram.py          # Existing bot, moved from telegram.py
├── email.py             # Existing Resend integration
├── voice.py             # Existing say/listen (thin wrapper, voice is special)
```

Each channel follows the same pattern:
- **Config section** in `config.yaml` under `channels:` (migrate existing `telegram:`, `notifications.email:` sections)
- **`enabled`** flag — disabled channels don't register MCP tools or start services
- **`start()`/`stop()`** lifecycle for channels that run as services (Telegram bot, future Discord bot)
- **MCP tools** auto-registered when channel is enabled
- **`agentwire channels list`** CLI command — shows available/enabled channels

Voice is special — it's real-time, bidirectional, tightly coupled to the portal. The voice "channel" is mostly a documentation wrapper so it shows up in `channels list`, not a full refactor.

**Done when:**
- Existing functionality unchanged (Telegram, email, voice all work exactly as before)
- Code lives in `agentwire/channels/`
- `agentwire channels list` shows configured channels
- Config consolidated under `channels:` section (with backwards compat for old paths during migration)

### Phase 2: Add Slack and Discord as built-in channels

These are the two most popular team communication platforms. Both should ship as built-in channels.

**Discord:**
- Bot using discord.py or similar
- DM-based (like Telegram) — messages route to/from sessions
- Voice channel support would be interesting but not required initially
- Config: bot token, allowed users/servers, default session

**Slack:**
- Bot using Slack Bolt SDK
- DM and channel-based messaging
- Slash commands for session management (`/aw list`, `/aw send piinpoint "check the deploy"`)
- Config: bot token, signing secret, allowed workspaces, default session

**Done when:**
- Discord bot works like Telegram bot (DMs route to sessions, replies come back)
- Slack bot handles DMs and slash commands
- Both follow the channel pattern from Phase 1
- Both have config sections and show in `channels list`

### Phase 3: Channel development guide + example template

Write documentation + example so developers can add custom channels:

```
agentwire/channels/example.py    # Annotated example channel (not registered by default)
docs/channels.md                 # Development guide
```

The guide covers:
- Base class / interface to implement
- How to register config section
- How MCP tools get auto-discovered
- How to handle inbound messages (routing to sessions)
- How to handle outbound messages (from agents)
- Service lifecycle (start/stop for long-running bots)
- Testing a custom channel

**Done when:**
- A developer can read the guide, copy the example, and have a working custom channel
- Custom channels in `agentwire/channels/` are auto-detected (no registration boilerplate)

## Architecture Notes

- Channels are **not** an abstraction over voice. Voice is the primary real-time interface. Channels are async messaging transports.
- Agents should be able to discover available channels via `channels_list()` MCP tool
- Each channel owns its own MCP tools (e.g., Telegram has `telegram_send`, Slack has `slack_send`) — no forced generic interface
- The `notify()` concept could exist as a convenience that broadcasts to all enabled channels, but individual channel tools remain available for targeted communication

## Config Vision

```yaml
channels:
  telegram:
    enabled: true
    bot_token: ""
    allowed_users: [8669226777]
    default_session: "main"
    voice_replies: true

  email:
    enabled: true
    api_key: ""
    from_address: "Echo <echo@agentwire.dev>"
    default_to: "user@example.com"

  discord:
    enabled: false
    bot_token: ""
    allowed_users: []
    default_session: "main"

  slack:
    enabled: false
    bot_token: ""
    signing_secret: ""
    default_session: "main"

  voice:
    enabled: true  # Always true when portal is running
```

## Overnight Suitability

- **Phase 1** is a great overnight candidate — refactoring existing code into a new structure with clear prep ("here's how each integration works today, consolidate into this pattern")
- **Phase 2** (Slack/Discord) needs some human prep for bot setup and API key provisioning, but the coding work could run overnight
- **Phase 3** is documentation — better done interactively
