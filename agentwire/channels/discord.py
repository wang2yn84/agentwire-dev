"""Discord channel — service channel via discord.py bot.

Requires the `discord.py` package (optional dependency).
Install: pip install discord.py

Mirrors the Telegram bridge pattern: DM-based commands, portal WebSocket
subscription for outbound events, voice via TTS/STT primitives.
"""

import asyncio
import json
import os
import ssl
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .base import (
    ChannelRegistry,
    ChannelResult,
    NotificationError,
    ServiceChannel,
    _run_cmd,
    _run_cmd_raw,
)


class DiscordConfigError(NotificationError):
    """Raised when Discord configuration is missing."""

    pass


@dataclass
class DiscordConfig:
    """Discord bot configuration."""

    bot_token: str = ""
    allowed_user_ids: list[int] = field(default_factory=list)
    default_session: str = "main"
    voice_replies: bool = True
    forward_questions: bool = True
    forward_alerts: bool = True
    session_name: str = "agentwire-discord"

    def __post_init__(self):
        if not self.bot_token:
            self.bot_token = os.environ.get("DISCORD_BOT_TOKEN", "")


def _get_discord_config() -> DiscordConfig:
    """Get Discord config from channels registry."""
    from agentwire.config import get_config

    config = get_config()
    dc_config = config.channels.get("discord")
    if dc_config:
        return dc_config
    return DiscordConfig()


# State file for per-user session tracking
STATE_FILE = Path.home() / ".agentwire" / "discord-state.json"

HELP_TEXT = """**AgentWire Discord Bot**

Commands (DM only):
`/start` — Welcome, list sessions
`/list` — List active sessions
`/s <name>` — Switch to session
`/output` — Show recent output from current session
`/new <name>` — Create a new session
`/kill <name>` — Kill a session
`/help` — Show this message

Send any text to route it to your current session.
Send a voice message for speech-to-text transcription.
"""


class DiscordBridge:
    """Discord bot bridge to AgentWire sessions."""

    def __init__(self, config: DiscordConfig):
        self.config = config
        self.user_sessions: dict[int, str] = {}  # user_id → session_name
        self._load_state()

    def _load_state(self):
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text())
                self.user_sessions = {int(k): v for k, v in data.get("user_sessions", {}).items()}
            except Exception:
                pass

    def _save_state(self):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({
            "user_sessions": {str(k): v for k, v in self.user_sessions.items()},
        }))

    def _get_session(self, user_id: int) -> str:
        return self.user_sessions.get(user_id, self.config.default_session)

    def _set_session(self, user_id: int, session: str):
        self.user_sessions[user_id] = session
        self._save_state()

    def _is_allowed(self, user_id: int) -> bool:
        if not self.config.allowed_user_ids:
            return True  # No whitelist = allow all
        return user_id in self.config.allowed_user_ids

    async def run(self):
        """Run the Discord bot."""
        try:
            import discord
        except ImportError:
            print("Error: discord.py not installed. Install: pip install discord.py", file=sys.stderr)
            return

        intents = discord.Intents.default()
        intents.message_content = True
        intents.dm_messages = True

        client = discord.Client(intents=intents)
        channel_ref = self  # Reference for nested handler

        @client.event
        async def on_ready():
            print(f"Discord bot connected as {client.user}")

        @client.event
        async def on_message(message):
            if message.author == client.user:
                return
            if not message.guild:  # DM only
                await channel_ref._handle_dm(message)

        # Start portal WebSocket listener for outbound events
        ws_task = None
        if self.config.forward_questions or self.config.forward_alerts:
            ws_task = asyncio.create_task(self._listen_portal_ws(client))

        try:
            await client.start(self.config.bot_token)
        finally:
            if ws_task:
                ws_task.cancel()

    async def _handle_dm(self, message):
        """Handle incoming DM."""
        import discord

        user_id = message.author.id
        if not self._is_allowed(user_id):
            return

        text = message.content.strip()

        # Handle commands
        if text.startswith("/"):
            await self._handle_command(message, text)
            return

        # Handle voice attachments
        for attachment in message.attachments:
            if attachment.content_type and attachment.content_type.startswith("audio/"):
                await self._handle_voice(message, attachment)
                return

        # Route text to session with source prefix
        session = self._get_session(user_id)
        author_name = message.author.display_name or message.author.name
        prefixed = f"[Discord from {author_name}: '{text}']"
        result = _run_cmd(["send", "-s", session, prefixed])
        if not result.get("success", False):
            await message.reply(f"Error sending to session `{session}`: {result.get('error', 'unknown')}")

    async def _handle_command(self, message, text: str):
        """Handle /command messages."""
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        user_id = message.author.id

        if cmd == "/start" or cmd == "/help":
            sessions = _run_cmd(["list"])
            session_list = sessions.get("sessions", [])
            names = [s.get("name", "?") for s in session_list]
            current = self._get_session(user_id)
            reply = HELP_TEXT + f"\n**Current session:** `{current}`\n"
            if names:
                reply += f"**Sessions:** {', '.join(f'`{n}`' for n in names)}"
            await message.reply(reply)

        elif cmd == "/list":
            sessions = _run_cmd(["list"])
            session_list = sessions.get("sessions", [])
            if session_list:
                lines = [f"`{s.get('name', '?')}` — {s.get('status', '?')}" for s in session_list]
                await message.reply("**Sessions:**\n" + "\n".join(lines))
            else:
                await message.reply("No active sessions.")

        elif cmd == "/s":
            if not arg:
                await message.reply("Usage: `/s <session_name>`")
                return
            self._set_session(user_id, arg)
            await message.reply(f"Switched to session `{arg}`")

        elif cmd == "/output":
            session = self._get_session(user_id)
            output = _run_cmd_raw(["output", "-s", session])
            truncated = output[-1800:] if len(output) > 1800 else output
            await message.reply(f"**Output from `{session}`:**\n```\n{truncated}\n```")

        elif cmd == "/new":
            if not arg:
                await message.reply("Usage: `/new <session_name>`")
                return
            result = _run_cmd(["new", "-s", arg])
            if result.get("success"):
                self._set_session(user_id, arg)
                await message.reply(f"Created and switched to session `{arg}`")
            else:
                await message.reply(f"Error: {result.get('error', 'unknown')}")

        elif cmd == "/kill":
            if not arg:
                await message.reply("Usage: `/kill <session_name>`")
                return
            result = _run_cmd(["kill", "-s", arg])
            if result.get("success"):
                await message.reply(f"Killed session `{arg}`")
            else:
                await message.reply(f"Error: {result.get('error', 'unknown')}")

        else:
            await message.reply(f"Unknown command: `{cmd}`. Try `/help`.")

    async def _handle_voice(self, message, attachment):
        """Handle voice message attachment — transcribe via STT."""
        try:
            audio_data = await attachment.read()
            channel = DiscordChannel(self.config)
            text = await channel.stt(audio_data, format="ogg")
            if text:
                session = self._get_session(message.author.id)
                _run_cmd(["send", "-s", session, text])
                await message.reply(f"Transcribed and sent to `{session}`: _{text}_")
            else:
                await message.reply("Could not transcribe audio.")
        except Exception as e:
            await message.reply(f"STT error: {e}")

    async def _listen_portal_ws(self, client):
        """Subscribe to portal WebSocket for outbound events."""
        try:
            import aiohttp

            from agentwire.config import get_config
            config = get_config()
            portal_url = config.portal.url.replace("https://", "wss://").replace("http://", "ws://")

            # Create SSL context that trusts self-signed certs
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE

            while True:
                try:
                    async with aiohttp.ClientSession() as http_session:
                        session_name = self._get_session(list(self.user_sessions.keys())[0]) if self.user_sessions else self.config.default_session
                        ws_url = f"{portal_url}/ws/{session_name}"
                        async with http_session.ws_connect(ws_url, ssl=ssl_ctx) as ws:
                            async for msg in ws:
                                if msg.type == aiohttp.WSMsgType.TEXT:
                                    await self._handle_ws_event(client, json.loads(msg.data))
                except Exception:
                    await asyncio.sleep(5)  # Reconnect delay
        except asyncio.CancelledError:
            return
        except ImportError:
            pass  # aiohttp not available

    async def _handle_ws_event(self, client, event: dict):
        """Handle outbound event from portal WebSocket."""
        event_type = event.get("type", "")

        if not self.user_sessions:
            return

        # Send to first allowed user (could be extended to all)
        target_user_id = next(iter(self.user_sessions.keys()))

        try:
            user = await client.fetch_user(target_user_id)
            if not user:
                return

            if event_type == "question" and self.config.forward_questions:
                question = event.get("question", "")
                await user.send(f"**Question from agent:**\n{question}")

            elif event_type == "alert" and self.config.forward_alerts:
                text = event.get("text", "")
                await user.send(f"**Alert:**\n{text}")

            elif event_type == "audio" and self.config.voice_replies:
                # TTS audio comes as base64
                import base64
                audio_b64 = event.get("audio", "")
                if audio_b64:
                    audio_bytes = base64.b64decode(audio_b64)
                    import discord
                    file = discord.File(
                        fp=__import__("io").BytesIO(audio_bytes),
                        filename="voice.wav",
                    )
                    await user.send(file=file)
        except Exception:
            pass


def run_bridge():
    """Run the Discord bridge (foreground, blocking)."""
    config = _get_discord_config()
    if not config.bot_token:
        print("Error: Discord bot token not configured.", file=sys.stderr)
        print("Set DISCORD_BOT_TOKEN env var or channels.discord.bot_token in config.yaml", file=sys.stderr)
        sys.exit(1)

    bridge = DiscordBridge(config)
    asyncio.run(bridge.run())


@ChannelRegistry.register("discord")
class DiscordChannel(ServiceChannel):
    """Discord service channel via discord.py bot.

    Run with: agentwire discord start|serve|stop|status
    """

    name = "discord"
    config_class = DiscordConfig
    config_key = "discord"
