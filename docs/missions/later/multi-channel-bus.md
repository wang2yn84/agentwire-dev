> Living document. Update this, don't create new versions.

# Mission: Multi-Channel Message Bus (Phase 3)

## Status: Later

## Summary

Abstract input/output into a channel-agnostic message bus. Any platform (Telegram, Discord, Slack, browser, email) implements a simple interface. Sessions don't care where messages come from.

## Architecture

```
Telegram  ──┐
Discord   ──┤
Slack     ──┼──→ MessageBus ──→ agentwire session
Browser   ──┤                ←── response routing
Email     ──┘
```

## Message Envelope

```python
@dataclass
class Message:
    source: str          # "telegram", "browser", "slack", "email"
    sender: str          # user identifier per platform
    session: str         # target session
    content: str         # text content
    voice_audio: bytes   # optional voice data
    attachments: list    # optional files
    reply_to: str        # optional message ID for threading
    metadata: dict       # platform-specific extras
```

## Channel Interface

```python
class Channel(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def send_text(self, session: str, text: str) -> None: ...
    async def send_voice(self, session: str, audio: bytes) -> None: ...
    async def send_file(self, session: str, path: str) -> None: ...
```

## Dependencies

- Phase 1 (Telegram Bridge) — proves the pattern
- Phase 2 (Agent SDK) — structured events make routing cleaner
