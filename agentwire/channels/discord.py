"""Discord channel — service channel via discord.py bot.

Requires the `discord.py` package (optional dependency).
Install: pip install discord.py

Features:
- DMs route to default_session (main brain/hub)
- Server channels route to mapped agentwire sessions via channel_map
- Auto-creates sessions if they're not running
- Commands via DM: /help, /list, /s, /output, /new, /kill
- Voice messages transcribed via STT primitive
- Portal WebSocket subscription for outbound events
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
    _run_cmd_no_json,
    _run_cmd_raw,
)


class DiscordConfigError(NotificationError):
    """Raised when Discord configuration is missing."""

    pass


@dataclass
class ChannelMapping:
    """Maps a Discord channel to an agentwire session."""

    session: str = ""
    project: str = ""  # Project path for auto-creation


@dataclass
class DiscordConfig:
    """Discord bot configuration."""

    bot_token: str = ""
    allowed_user_ids: list[int] = field(default_factory=list)
    default_session: str = "agentwire"
    voice_replies: bool = True
    forward_questions: bool = True
    forward_alerts: bool = True
    session_name: str = "agentwire-discord"
    dm_project: str = "~/projects/discord-dms"  # Base dir for per-user DM sessions
    dm_session_prefix: str = "discord-dm"  # Session naming prefix
    channel_map: dict = field(default_factory=dict)  # discord_channel_id → {session, project}

    def __post_init__(self):
        if not self.bot_token:
            self.bot_token = os.environ.get("DISCORD_BOT_TOKEN", "")
        # Normalize channel_map: accept both string and dict values
        normalized = {}
        for channel_id, value in self.channel_map.items():
            channel_id = str(channel_id)
            if isinstance(value, str):
                normalized[channel_id] = ChannelMapping(session=value)
            elif isinstance(value, dict):
                normalized[channel_id] = ChannelMapping(
                    session=value.get("session", ""),
                    project=value.get("project", ""),
                )
            elif isinstance(value, ChannelMapping):
                normalized[channel_id] = value
        self.channel_map = normalized


def _get_discord_config() -> DiscordConfig:
    """Get Discord config from channels registry."""
    from agentwire.config import get_config

    config = get_config()
    dc_config = config.channels.get("discord")
    if dc_config:
        return dc_config
    return DiscordConfig()


# State file for per-user session tracking (DMs only)
STATE_FILE = Path.home() / ".agentwire" / "discord-state.json"

HELP_TEXT = """**AgentWire Discord Bot**

**DM Commands:**
`/help` — Show this message
`/list` — List active sessions
`/s <name>` — Switch your DM session (overrides auto-session)
`/output [name]` — Show recent session output
`/new <name>` — Create a new session
`/kill <name>` — Kill a session

**DMs** auto-create a per-user session (`{dm_prefix}-<your_id>`).
**Server channels** route to mapped agentwire sessions.

Send any text to route it to your session.
"""


def _setup_dm_project(project_dir: str, user_id: int, display_name: str, username: str):
    """Set up a DM user's project folder with config, CLAUDE.md, and git repo on first contact."""
    import subprocess

    project = Path(project_dir)
    project.mkdir(parents=True, exist_ok=True)

    # Init git repo if not already one
    git_dir = project / ".git"
    if not git_dir.exists():
        subprocess.run(["git", "init"], cwd=str(project), capture_output=True)
        # .gitignore
        gitignore = project / ".gitignore"
        gitignore.write_text(
            "# AgentWire discord DM project\n"
            ".agentwire/\n"
            "__pycache__/\n"
            "*.pyc\n"
        )

    # Write .agentwire.yml if it doesn't exist
    config_file = project / ".agentwire.yml"
    if not config_file.exists():
        config_file.write_text(
            "type: claude-bypass\n"
            "roles:\n"
            "  - agentwire\n"
            "  - discord-dm\n"
            "parent: agentwire\n"
        )

    # Write CLAUDE.md if it doesn't exist
    claude_file = project / "CLAUDE.md"
    if not claude_file.exists():
        from datetime import datetime
        claude_file.write_text(
            f"# Discord DM — {display_name}\n\n"
            f"**Discord user:** {display_name} ({username})\n"
            f"**User ID:** {user_id}\n"
            f"**First contact:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            f"## About This User\n\n"
            f"<!-- Add notes about this user here. The agent reads this on every message. -->\n\n"
            f"## Instructions\n\n"
            f"<!-- Add user-specific instructions here. -->\n"
        )

    # Initial commit if repo is empty (no commits yet)
    result = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=str(project), capture_output=True, text=True,
    )
    if result.returncode != 0:
        subprocess.run(["git", "add", "-A"], cwd=str(project), capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"init: discord DM project for {display_name} ({user_id})"],
            cwd=str(project), capture_output=True,
        )


def _session_exists(session: str) -> bool:
    """Check if an agentwire session is running."""
    # Use info command to check specific session
    result = _run_cmd(["info", "-s", session])
    return result.get("success", False)


def _ensure_session(session: str, project: str = "") -> bool:
    """Ensure a session exists, creating it if needed. Returns True if ready."""
    if _session_exists(session):
        return True

    args = ["new", "-s", session]
    if project:
        expanded = str(Path(project).expanduser())
        args.extend(["-p", expanded])

    result = _run_cmd(args)
    return result.get("success", False)


# Emoji status indicators
EMOJI_QUEUED = "⏳"
EMOJI_STARTING = "🚀"
EMOJI_SENT = "✅"
EMOJI_ERROR = "❌"


@dataclass
class QueuedMessage:
    """A message waiting to be processed."""

    message: object  # discord.Message
    text: str
    session: str
    project: str  # Empty string if no project needed
    prefix: str  # Pre-formatted prefix string


class SessionQueueManager:
    """Manages per-session message queues with async workers.

    Each session gets its own queue and worker task. Messages are
    processed in order, one at a time per session. Different sessions
    process concurrently.
    """

    def __init__(self):
        self._queues: dict[str, asyncio.Queue] = {}
        self._workers: dict[str, asyncio.Task] = {}

    async def enqueue(self, msg: QueuedMessage):
        """Add a message to the session's queue. Starts worker if needed."""
        session = msg.session
        print(f"[discord] Enqueuing message for session '{session}': {msg.text[:50]}")

        # React with queued emoji immediately
        try:
            await msg.message.add_reaction(EMOJI_QUEUED)
        except Exception as e:
            print(f"[discord] Failed to add reaction: {e}")

        # Create queue and worker if this is a new session
        if session not in self._queues:
            print(f"[discord] Starting new queue worker for session '{session}'")
            self._queues[session] = asyncio.Queue()
            self._workers[session] = asyncio.create_task(self._worker(session))

        await self._queues[session].put(msg)

    async def _worker(self, session: str):
        """Process messages for a session, one at a time, in order."""
        queue = self._queues[session]

        while True:
            try:
                msg: QueuedMessage = await asyncio.wait_for(queue.get(), timeout=300)
            except asyncio.TimeoutError:
                # No messages for 5 min — shut down worker
                del self._queues[session]
                del self._workers[session]
                return

            try:
                await self._process_message(msg)
            except Exception as e:
                print(f"[discord] Queue error for {msg.session}: {e}")
                import traceback
                traceback.print_exc()
                try:
                    await msg.message.remove_reaction(EMOJI_QUEUED, msg.message.guild.me if msg.message.guild else msg.message.channel.me)
                    await msg.message.add_reaction(EMOJI_ERROR)
                except Exception:
                    pass

            queue.task_done()

    async def _process_message(self, msg: QueuedMessage):
        """Process a single queued message."""
        discord_msg = msg.message
        loop = asyncio.get_event_loop()

        # Run blocking subprocess calls in thread executor
        print(f"[discord] Processing message for session '{msg.session}'")

        # Swap queued → starting if session needs creation
        exists = await loop.run_in_executor(None, _session_exists, msg.session)
        if not exists:
            print(f"[discord] Session '{msg.session}' not found, creating...")
            try:
                await self._swap_reaction(discord_msg, EMOJI_QUEUED, EMOJI_STARTING)
            except Exception:
                pass

            created = await loop.run_in_executor(None, _ensure_session, msg.session, msg.project)
            if not created:
                print(f"[discord] Failed to create session '{msg.session}'")
                try:
                    await self._swap_reaction(discord_msg, EMOJI_STARTING, EMOJI_ERROR)
                    await discord_msg.reply(f"Failed to start session `{msg.session}`")
                except Exception:
                    pass
                return

        # Send to session (no --json flag — send doesn't support it)
        print(f"[discord] Sending to session '{msg.session}'")
        result = await loop.run_in_executor(None, _run_cmd_no_json, ["send", "-s", msg.session, msg.prefix])

        if result.get("success", False):
            # Swap to checkmark
            try:
                # Remove whichever emoji is current
                for emoji in (EMOJI_QUEUED, EMOJI_STARTING):
                    try:
                        bot_user = discord_msg.guild.me if discord_msg.guild else None
                        if bot_user:
                            await discord_msg.remove_reaction(emoji, bot_user)
                    except Exception:
                        pass
                await discord_msg.add_reaction(EMOJI_SENT)
            except Exception:
                pass
        else:
            try:
                for emoji in (EMOJI_QUEUED, EMOJI_STARTING):
                    try:
                        bot_user = discord_msg.guild.me if discord_msg.guild else None
                        if bot_user:
                            await discord_msg.remove_reaction(emoji, bot_user)
                    except Exception:
                        pass
                await discord_msg.add_reaction(EMOJI_ERROR)
                await discord_msg.reply(f"Error: {result.get('error', 'unknown')}")
            except Exception:
                pass

    async def _swap_reaction(self, message, old_emoji: str, new_emoji: str):
        """Swap one reaction for another."""
        try:
            bot_user = message.guild.me if message.guild else None
            if bot_user:
                await message.remove_reaction(old_emoji, bot_user)
        except Exception:
            pass
        await message.add_reaction(new_emoji)


class DiscordBridge:
    """Discord bot bridge to AgentWire sessions."""

    def __init__(self, config: DiscordConfig):
        self.config = config
        self.user_sessions: dict[int, str] = {}  # user_id → session_name (DMs only)
        self.queue_manager = SessionQueueManager()
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

    def _get_dm_session(self, user_id: int) -> str:
        """Get session for a DM user. Auto-generates per-user session name."""
        if user_id in self.user_sessions:
            return self.user_sessions[user_id]
        return f"{self.config.dm_session_prefix}-{user_id}"

    def _get_dm_project(self, user_id: int) -> str:
        """Get project dir for a DM user's session."""
        base = str(Path(self.config.dm_project).expanduser())
        return f"{base}/{user_id}"

    def _set_dm_session(self, user_id: int, session: str):
        self.user_sessions[user_id] = session
        self._save_state()

    def _get_channel_mapping(self, channel_id: int) -> ChannelMapping | None:
        """Get the agentwire session mapping for a Discord channel."""
        return self.config.channel_map.get(str(channel_id))

    def _is_allowed(self, user_id: int) -> bool:
        if not self.config.allowed_user_ids:
            return True
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
        bridge = self

        @client.event
        async def on_ready():
            print(f"Discord bot connected as {client.user}")
            if bridge.config.channel_map:
                print(f"  Channel mappings: {len(bridge.config.channel_map)}")
                for ch_id, mapping in bridge.config.channel_map.items():
                    print(f"    #{ch_id} → {mapping.session}")

        @client.event
        async def on_message(message):
            if message.author == client.user:
                return

            if not bridge._is_allowed(message.author.id):
                print(f"[discord] Blocked user {message.author.id} ({message.author.display_name}) — not in allowed_user_ids")
                return

            if message.guild:
                print(f"[discord] Channel message in #{message.channel.name} ({message.channel.id}) from {message.author.display_name}")
                await bridge._handle_channel_message(message)
            else:
                print(f"[discord] DM from {message.author.display_name} ({message.author.id})")
                await bridge._handle_dm(message)

        # Start portal WebSocket listeners for mapped channel sessions
        ws_tasks = []
        if self.config.forward_questions or self.config.forward_alerts:
            for ch_id, mapping in self.config.channel_map.items():
                ws_tasks.append(asyncio.create_task(
                    self._listen_portal_ws(client, mapping.session, target_type="channel", target_id=int(ch_id))
                ))

        try:
            await client.start(self.config.bot_token)
        finally:
            for task in ws_tasks:
                task.cancel()

    async def _handle_channel_message(self, message):
        """Handle a message from a server channel.

        Only responds to @mentions of the bot. Strips the mention from the text.
        """
        mapping = self._get_channel_mapping(message.channel.id)
        if not mapping:
            return  # Unmapped channel — ignore

        # Only respond if the bot is @mentioned
        bot_id = message.guild.me.id
        mention_ids = [m.id for m in message.mentions]
        print(f"[discord] Bot ID: {bot_id}, mentions: {mention_ids}, content: {message.content[:100]}")
        if not message.mentions or not any(m.id == bot_id for m in message.mentions):
            print(f"[discord] Bot not mentioned, ignoring")
            return

        # Strip the @mention from the text
        import re
        text = re.sub(r'<@!?\d+>\s*', '', message.content).strip()
        if not text:
            return

        # Handle commands in server channels too
        if text.startswith("/"):
            await self._handle_channel_command(message, text, mapping)
            return

        # Enqueue for processing
        session = mapping.session
        author_name = message.author.display_name or message.author.name
        channel_name = message.channel.name
        prefixed = f"[Discord #{channel_name} from {author_name}: '{text}']"
        await self.queue_manager.enqueue(QueuedMessage(
            message=message,
            text=text,
            session=session,
            project=mapping.project,
            prefix=prefixed,
        ))

    async def _handle_channel_command(self, message, text: str, mapping: ChannelMapping):
        """Handle commands in server channels."""
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        session = mapping.session

        if cmd == "/output":
            if not _session_exists(session):
                await message.reply(f"Session `{session}` is not running.")
                return
            output = _run_cmd_raw(["output", "-s", session])
            truncated = output[-1800:] if len(output) > 1800 else output
            await message.reply(f"**Output from `{session}`:**\n```\n{truncated}\n```")

        elif cmd == "/status":
            running = _session_exists(session)
            status = "running" if running else "not running"
            await message.reply(f"Session `{session}`: **{status}**")

        else:
            # Not a recognized channel command — treat as regular message
            text_clean = text.strip()
            author_name = message.author.display_name or message.author.name
            channel_name = message.channel.name
            prefixed = f"[Discord #{channel_name} from {author_name}: '{text_clean}']"
            await self.queue_manager.enqueue(QueuedMessage(
                message=message,
                text=text_clean,
                session=session,
                project=mapping.project,
                prefix=prefixed,
            ))

    async def _handle_dm(self, message):
        """Handle incoming DM."""
        user_id = message.author.id
        text = message.content.strip()

        # Handle commands
        if text.startswith("/"):
            await self._handle_dm_command(message, text)
            return

        # Handle voice attachments
        for attachment in message.attachments:
            if attachment.content_type and attachment.content_type.startswith("audio/"):
                await self._handle_voice(message, attachment)
                return

        if not text:
            return

        # Set up DM project and enqueue
        session = self._get_dm_session(user_id)
        project = self._get_dm_project(user_id)
        author_name = message.author.display_name or message.author.name
        username = str(message.author)
        _setup_dm_project(project, user_id, author_name, username)

        prefixed = f"[Discord DM from {author_name}: '{text}']"
        await self.queue_manager.enqueue(QueuedMessage(
            message=message,
            text=text,
            session=session,
            project=project,
            prefix=prefixed,
        ))

    async def _handle_dm_command(self, message, text: str):
        """Handle /command messages in DMs."""
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        user_id = message.author.id

        if cmd == "/start" or cmd == "/help":
            sessions = _run_cmd(["list"])
            session_list = sessions.get("sessions", [])
            names = [s.get("name", "?") for s in session_list]
            current = self._get_dm_session(user_id)
            help_text = HELP_TEXT.format(dm_prefix=self.config.dm_session_prefix)
            reply = help_text + f"\n**Your DM session:** `{current}`\n"
            if names:
                reply += f"**Active sessions:** {', '.join(f'`{n}`' for n in names)}"
            if self.config.channel_map:
                reply += "\n\n**Channel mappings:**\n"
                for ch_id, mapping in self.config.channel_map.items():
                    reply += f"  <#{ch_id}> → `{mapping.session}`\n"
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
            self._set_dm_session(user_id, arg)
            await message.reply(f"Switched DM session to `{arg}`")

        elif cmd == "/output":
            session = arg or self._get_dm_session(user_id)
            if not _session_exists(session):
                await message.reply(f"Session `{session}` is not running.")
                return
            output = _run_cmd_raw(["output", "-s", session])
            truncated = output[-1800:] if len(output) > 1800 else output
            await message.reply(f"**Output from `{session}`:**\n```\n{truncated}\n```")

        elif cmd == "/new":
            if not arg:
                await message.reply("Usage: `/new <session_name>`")
                return
            result = _run_cmd(["new", "-s", arg])
            if result.get("success"):
                self._set_dm_session(user_id, arg)
                await message.reply(f"Created and switched DM session to `{arg}`")
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
                session = self._get_dm_session(message.author.id)
                project = self._get_dm_project(message.author.id)
                author_name = message.author.display_name or message.author.name
                username = str(message.author)
                _setup_dm_project(project, message.author.id, author_name, username)
                if not _ensure_session(session, project):
                    await message.reply(f"Failed to start session `{session}`")
                    return
                author_name = message.author.display_name or message.author.name
                prefixed = f"[Discord voice from {author_name}: '{text}']"
                _run_cmd(["send", "-s", session, prefixed])
                await message.reply(f"Transcribed and sent to `{session}`: _{text}_")
            else:
                await message.reply("Could not transcribe audio.")
        except Exception as e:
            await message.reply(f"STT error: {e}")

    async def _listen_portal_ws(self, client, session_name: str, target_type: str = "dm", target_id: int = 0):
        """Subscribe to portal WebSocket for outbound events.

        target_type: "dm" sends to DM users, "channel" sends to a Discord channel.
        target_id: Discord channel ID (when target_type="channel").
        """
        try:
            import aiohttp

            from agentwire.config import get_config
            config = get_config()
            portal_url = config.portal.url.replace("https://", "wss://").replace("http://", "ws://")

            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE

            while True:
                try:
                    async with aiohttp.ClientSession() as http_session:
                        ws_url = f"{portal_url}/ws/{session_name}"
                        async with http_session.ws_connect(ws_url, ssl=ssl_ctx) as ws:
                            async for msg in ws:
                                if msg.type == aiohttp.WSMsgType.TEXT:
                                    await self._handle_ws_event(
                                        client, json.loads(msg.data),
                                        target_type=target_type, target_id=target_id,
                                    )
                except Exception:
                    await asyncio.sleep(5)
        except asyncio.CancelledError:
            return
        except ImportError:
            pass

    async def _handle_ws_event(self, client, event: dict, target_type: str = "dm", target_id: int = 0):
        """Handle outbound event from portal WebSocket."""
        event_type = event.get("type", "")

        try:
            if target_type == "channel" and target_id:
                # Send to Discord channel
                channel = client.get_channel(target_id)
                if not channel:
                    return

                if event_type == "question" and self.config.forward_questions:
                    await channel.send(f"**Question from agent:**\n{event.get('question', '')}")
                elif event_type == "alert" and self.config.forward_alerts:
                    await channel.send(f"**Alert:**\n{event.get('text', '')}")

            else:
                # Send to DM users
                if not self.user_sessions:
                    return
                target_user_id = next(iter(self.user_sessions.keys()))
                user = await client.fetch_user(target_user_id)
                if not user:
                    return

                if event_type == "question" and self.config.forward_questions:
                    await user.send(f"**Question from agent:**\n{event.get('question', '')}")
                elif event_type == "alert" and self.config.forward_alerts:
                    await user.send(f"**Alert:**\n{event.get('text', '')}")
                elif event_type == "audio" and self.config.voice_replies:
                    import base64
                    audio_b64 = event.get("audio", "")
                    if audio_b64:
                        import io
                        import discord
                        audio_bytes = base64.b64decode(audio_b64)
                        file = discord.File(fp=io.BytesIO(audio_bytes), filename="voice.wav")
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

    DMs auto-create per-user sessions (discord-dm-<user_id>).
    Server channels route to mapped sessions via channel_map.
    Sessions auto-created if not running.

    Config:
        channels:
          discord:
            default_session: "agentwire"           # Fallback session
            dm_project: "~/projects/discord-dms"   # Base dir for DM sessions
            dm_session_prefix: "discord-dm"        # Session naming prefix
            allowed_user_ids: [252979...]           # User whitelist (empty = allow all)
            channel_map:                            # Server channel → session mapping
              "1234567890":
                session: "website/main"
                project: "~/projects/website"
              "0987654321": "api/main"              # Shorthand: just session name
    """

    name = "discord"
    config_class = DiscordConfig
    config_key = "discord"
