"""Slack channel — service channel via slack-bolt with Socket Mode.

Requires the `slack-bolt` package (optional dependency).
Install: pip install slack-bolt

Features:
- DMs auto-create per-user sessions in ~/.agentwire/channels/slack/
- Channel @mentions route to mapped agentwire sessions via channel_map
- Auto-creates sessions if they're not running
- Smart session readiness polling before sending
- Message queue with Slack reaction status (⏳→✅/❌)
- Portal WebSocket subscription for outbound alert delivery
- Socket Mode = outbound WebSocket, no public URL needed
"""

import asyncio
import json
import os
import re
import ssl
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .base import (
    ChannelRegistry,
    MessageQueueManager,
    NotificationError,
    QueuedMessage,
    ServiceChannel,
    _run_cmd,
    _run_cmd_raw,
    compose_session_config,
    inject_instructions,
    session_exists,
)


class SlackConfigError(NotificationError):
    """Raised when Slack configuration is missing."""

    pass


@dataclass
class SlackChannelMapping:
    """Maps a Slack channel to an agentwire session.

    Fields beyond session/project are per-channel overrides for the composable
    session config hierarchy (type/roles/instructions).
    """

    session: str = ""
    project: str = ""
    type: str = ""                               # override session type
    roles: list[str] = field(default_factory=list)  # appended to scope+platform roles
    instructions: str = ""                       # appended to scope+platform instructions


@dataclass
class SlackUserMapping:
    """Per-user overrides for DM sessions (DM scope only).

    Channel messages from this user still use the channel's config — user_map
    only applies when the user sends a DM.
    """

    type: str = ""
    roles: list[str] = field(default_factory=list)
    instructions: str = ""


@dataclass
class SlackConfig:
    """Slack bot configuration with composable session config hierarchy.

    Session type/roles/instructions compose across 3 levels:
      1. Platform defaults — default_type / default_roles / default_instructions
         (apply to all Slack sessions)
      2. Scope defaults — dm_roles+dm_instructions (for DMs) OR
         channel_roles+channel_instructions (for channel sessions)
      3. Specific overrides — per-channel in channel_map, per-user in user_map

    Roles are appended and deduped (preserving order). Instructions are
    joined with blank lines. Session type uses first-non-empty precedence:
    specific → scope → platform → "claude-bypass".
    """

    bot_token: str = ""  # xoxb-...
    app_token: str = ""  # xapp-... (for Socket Mode)
    allowed_user_ids: list[str] = field(default_factory=list)  # Slack user IDs are strings
    default_session: str = "agentwire"
    voice_replies: bool = True
    forward_questions: bool = True
    forward_alerts: bool = True
    session_name: str = "agentwire-slack"
    channels_dir: str = "~/.agentwire/channels/slack"
    channel_map: dict = field(default_factory=dict)
    user_map: dict = field(default_factory=dict)  # DM-only: slack_user_id → SlackUserMapping

    # --- Composable session config (platform level) ---
    default_type: str = "claude-bypass"
    default_roles: list[str] = field(default_factory=lambda: ["agentwire"])
    default_instructions: str = ""

    # --- Scope: DM ---
    dm_roles: list[str] = field(default_factory=lambda: ["slack-dm"])
    dm_instructions: str = ""

    # --- Scope: channel (non-DM) ---
    channel_roles: list[str] = field(default_factory=lambda: ["slack-dm"])
    channel_instructions: str = ""

    def __post_init__(self):
        if not self.bot_token:
            self.bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
        if not self.app_token:
            self.app_token = os.environ.get("SLACK_APP_TOKEN", "")
        self.channels_dir = str(Path(self.channels_dir).expanduser())

        # Normalize channel_map
        normalized_ch = {}
        for channel_id, value in self.channel_map.items():
            channel_id = str(channel_id)
            if isinstance(value, str):
                # Shorthand: "label" → slack-ch-label with no overrides
                normalized_ch[channel_id] = SlackChannelMapping(session=f"slack-ch-{value}")
            elif isinstance(value, dict):
                label = value.get("label", channel_id)
                session = value.get("session", f"slack-ch-{label}")
                normalized_ch[channel_id] = SlackChannelMapping(
                    session=session,
                    project=value.get("project", ""),
                    type=value.get("type", ""),
                    roles=list(value.get("roles", [])),
                    instructions=value.get("instructions", ""),
                )
            elif isinstance(value, SlackChannelMapping):
                normalized_ch[channel_id] = value
        self.channel_map = normalized_ch

        # Normalize user_map (DM-only overrides)
        normalized_users = {}
        for user_id, value in self.user_map.items():
            user_id = str(user_id)
            if isinstance(value, dict):
                normalized_users[user_id] = SlackUserMapping(
                    type=value.get("type", ""),
                    roles=list(value.get("roles", [])),
                    instructions=value.get("instructions", ""),
                )
            elif isinstance(value, SlackUserMapping):
                normalized_users[user_id] = value
        self.user_map = normalized_users


def _get_slack_config() -> SlackConfig:
    """Get Slack config from channels registry."""
    from agentwire.config import get_config

    config = get_config()
    sl_config = config.channels.get("slack")
    if sl_config:
        return sl_config
    return SlackConfig()


# State file for per-user DM session tracking
STATE_FILE = Path.home() / ".agentwire" / "slack-state.json"


def _write_agentwire_yml(project: Path, session_type: str, roles: list[str]) -> None:
    """Write .agentwire.yml from composed config, overwriting any previous version.

    Overwriting is safe because the file is entirely agent-managed (type + roles)
    and is expected to reflect current channel config on every session spawn.
    """
    lines = [f"type: {session_type}", "roles:"]
    for role in roles:
        lines.append(f"  - {role}")
    (project / ".agentwire.yml").write_text("\n".join(lines) + "\n")


def _setup_dm_project(
    project_dir: str,
    user_id: str,
    display_name: str,
    session_type: str,
    roles: list[str],
    instructions: str,
):
    """Set up (or refresh) a DM user's project folder.

    Creates git repo + .gitignore + CLAUDE.md on first contact. Always rewrites
    .agentwire.yml from the composed config and refreshes the instructions block
    inside CLAUDE.md. Human edits to CLAUDE.md outside the marker block are
    preserved.
    """
    project = Path(project_dir)
    project.mkdir(parents=True, exist_ok=True)

    git_dir = project / ".git"
    if not git_dir.exists():
        subprocess.run(["git", "init"], cwd=str(project), capture_output=True)
        gitignore = project / ".gitignore"
        gitignore.write_text(
            "# AgentWire slack DM project\n"
            ".agentwire/\n"
            "__pycache__/\n"
            "*.pyc\n"
        )

    # Always rewrite .agentwire.yml from current config
    _write_agentwire_yml(project, session_type, roles)

    claude_file = project / "CLAUDE.md"
    if not claude_file.exists():
        from datetime import datetime
        claude_file.write_text(
            f"# Slack DM — {display_name}\n\n"
            f"**Slack user:** {display_name}\n"
            f"**User ID:** {user_id}\n"
            f"**First contact:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            f"## About This User\n\n"
            f"<!-- Add notes about this user here. The agent reads this on every message. -->\n"
        )

    # Refresh the auto-managed instructions block (human edits outside preserved)
    inject_instructions(claude_file, instructions)

    result = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=str(project), capture_output=True, text=True,
    )
    if result.returncode != 0:
        subprocess.run(["git", "add", "-A"], cwd=str(project), capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"init: slack DM project for {display_name} ({user_id})"],
            cwd=str(project), capture_output=True,
        )


def _setup_channel_project(
    project_dir: str,
    channel_id: str,
    channel_name: str,
    workspace_name: str,
    session_type: str,
    roles: list[str],
    instructions: str,
):
    """Set up (or refresh) a Slack channel's project folder.

    Same regeneration semantics as _setup_dm_project: .agentwire.yml is
    rewritten from config on every call; the instructions block in CLAUDE.md
    is refreshed while human edits elsewhere in CLAUDE.md are preserved.
    """
    project = Path(project_dir)
    project.mkdir(parents=True, exist_ok=True)

    git_dir = project / ".git"
    if not git_dir.exists():
        subprocess.run(["git", "init"], cwd=str(project), capture_output=True)
        gitignore = project / ".gitignore"
        gitignore.write_text(
            "# AgentWire slack channel project\n"
            ".agentwire/\n"
            "__pycache__/\n"
            "*.pyc\n"
        )

    # Always rewrite .agentwire.yml from current config
    _write_agentwire_yml(project, session_type, roles)

    claude_file = project / "CLAUDE.md"
    if not claude_file.exists():
        from datetime import datetime
        claude_file.write_text(
            f"# Slack Channel — #{channel_name}\n\n"
            f"**Workspace:** {workspace_name}\n"
            f"**Channel:** #{channel_name}\n"
            f"**Channel ID:** {channel_id}\n"
            f"**Created:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            f"## Purpose\n\n"
            f"<!-- Describe what this channel is for. -->\n"
        )

    # Refresh the auto-managed instructions block (human edits outside preserved)
    inject_instructions(claude_file, instructions)

    result = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=str(project), capture_output=True, text=True,
    )
    if result.returncode != 0:
        subprocess.run(["git", "add", "-A"], cwd=str(project), capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"init: slack channel project for #{channel_name} ({channel_id})"],
            cwd=str(project), capture_output=True,
        )


# Slack limits — 4000 char API max, leave room for markdown formatting
SLACK_MAX_MSG = 2800

# Slack emoji status indicators (reaction names, no colons)
EMOJI_QUEUED = "hourglass_flowing_sand"
EMOJI_STARTING = "rocket"
EMOJI_SENT = "white_check_mark"
EMOJI_ERROR = "x"


class SlackBridge:
    """Slack bot bridge to AgentWire sessions."""

    def __init__(self, config: SlackConfig):
        self.config = config
        self.user_sessions: dict[str, str] = {}  # slack_user_id → session_name (DMs only)
        self._portal_listeners: dict[str, threading.Thread] = {}  # session → listener thread
        self._app = None  # Set when bot starts
        self._load_state()

    def _load_state(self):
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text())
                self.user_sessions = data.get("user_sessions", {})
            except Exception:
                pass

    def _save_state(self):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({
            "user_sessions": self.user_sessions,
        }))

    def _get_dm_session(self, user_id: str) -> str:
        if user_id in self.user_sessions:
            return self.user_sessions[user_id]
        return f"slack-dm-{user_id}"

    def _get_dm_project(self, user_id: str) -> str:
        return f"{self.config.channels_dir}/dm-{user_id}"

    def _get_channel_project(self, channel_id: str) -> str:
        return f"{self.config.channels_dir}/ch-{channel_id}"

    def _set_dm_session(self, user_id: str, session: str):
        self.user_sessions[user_id] = session
        self._save_state()

    def _is_allowed(self, user_id: str) -> bool:
        if not self.config.allowed_user_ids:
            return True
        return user_id in self.config.allowed_user_ids

    def _get_channel_mapping(self, channel_id: str) -> SlackChannelMapping | None:
        return self.config.channel_map.get(str(channel_id))

    def _get_user_mapping(self, user_id: str) -> SlackUserMapping | None:
        """Get per-user DM overrides from user_map. DM scope only."""
        return self.config.user_map.get(str(user_id))

    def _platform_config(self) -> dict:
        """Level 1 config: applies to all Slack sessions."""
        return {
            "type": self.config.default_type,
            "roles": self.config.default_roles,
            "instructions": self.config.default_instructions,
        }

    def _dm_scope_config(self) -> dict:
        """Level 2a config: applies to all DM sessions."""
        return {
            "roles": self.config.dm_roles,
            "instructions": self.config.dm_instructions,
        }

    def _channel_scope_config(self) -> dict:
        """Level 2b config: applies to all channel (non-DM) sessions."""
        return {
            "roles": self.config.channel_roles,
            "instructions": self.config.channel_instructions,
        }

    def compose_dm_config(self, user_id: str) -> tuple[str, list[str], str]:
        """Compose session config for a DM with a specific user.

        Hierarchy: platform → dm_scope → user_map[user_id] (if present).
        Returns (type, roles, instructions).
        """
        user_mapping = self._get_user_mapping(user_id)
        specific: dict = {}
        if user_mapping:
            specific = {
                "type": user_mapping.type,
                "roles": user_mapping.roles,
                "instructions": user_mapping.instructions,
            }
        return compose_session_config(
            platform=self._platform_config(),
            scope=self._dm_scope_config(),
            specific=specific,
        )

    def compose_channel_config(self, channel_id: str) -> tuple[str, list[str], str]:
        """Compose session config for a Slack channel.

        Hierarchy: platform → channel_scope → channel_map[channel_id] (if present).
        Returns (type, roles, instructions).
        """
        ch_mapping = self._get_channel_mapping(channel_id)
        specific: dict = {}
        if ch_mapping:
            specific = {
                "type": ch_mapping.type,
                "roles": ch_mapping.roles,
                "instructions": ch_mapping.instructions,
            }
        return compose_session_config(
            platform=self._platform_config(),
            scope=self._channel_scope_config(),
            specific=specific,
        )

    def _ensure_portal_listener(self, session: str, reply_func):
        """Start a portal WebSocket listener for a session in a background thread."""
        if session in self._portal_listeners:
            return

        def listener():
            asyncio.run(self._listen_portal_ws(session, reply_func))

        t = threading.Thread(target=listener, daemon=True)
        t.start()
        self._portal_listeners[session] = t
        print(f"[slack] Started portal listener for session '{session}'")

    async def _listen_portal_ws(self, session_name: str, reply_func):
        """Subscribe to portal WebSocket for outbound events."""
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
                                    event = json.loads(msg.data)
                                    event_type = event.get("type", "")
                                    if event_type == "alert" and self.config.forward_alerts:
                                        text = event.get("text", "")
                                        print(f"[slack] Forwarding alert from '{session_name}': {text[:50]}")
                                        try:
                                            reply_func(text)
                                        except Exception as e:
                                            print(f"[slack] Reply error: {e}")
                                    elif event_type == "question" and self.config.forward_questions:
                                        question = event.get("question", "")
                                        try:
                                            reply_func(f"*Question from agent:*\n{question}")
                                        except Exception as e:
                                            print(f"[slack] Reply error: {e}")
                except Exception as e:
                    print(f"[slack] WS connection error for '{session_name}': {e}, reconnecting in 5s")
                    await asyncio.sleep(5)
        except ImportError:
            print("[slack] aiohttp not installed — portal WebSocket listener disabled")
        except Exception as e:
            print(f"[slack] Portal WS listener for '{session_name}' failed: {e}")

    def _init_queue_manager(self, client):
        """Initialize the async message queue manager with Slack reaction callbacks.

        Runs the queue's event loop in a background thread so slack-bolt's
        sync handlers can enqueue messages without blocking.
        """
        def _slack_reaction_callbacks(client):
            """Build async reaction callbacks for Slack's message queue."""
            async def _clear_and_react(msg, emoji_name):
                """Remove previous status reactions, add new one."""
                channel = msg.get("channel", "")
                ts = msg.get("ts", "")
                for old in (EMOJI_QUEUED, EMOJI_STARTING):
                    try:
                        client.reactions_remove(channel=channel, timestamp=ts, name=old)
                    except Exception:
                        pass
                try:
                    client.reactions_add(channel=channel, timestamp=ts, name=emoji_name)
                except Exception:
                    pass

            async def on_queued(msg):
                try:
                    client.reactions_add(
                        channel=msg.get("channel", ""), timestamp=msg.get("ts", ""), name=EMOJI_QUEUED
                    )
                except Exception:
                    pass

            async def on_starting(msg):
                channel = msg.get("channel", "")
                ts = msg.get("ts", "")
                try:
                    client.reactions_remove(channel=channel, timestamp=ts, name=EMOJI_QUEUED)
                except Exception:
                    pass
                try:
                    client.reactions_add(channel=channel, timestamp=ts, name=EMOJI_STARTING)
                except Exception:
                    pass

            async def on_sent(msg):
                await _clear_and_react(msg, EMOJI_SENT)

            async def on_error(msg):
                await _clear_and_react(msg, EMOJI_ERROR)

            return on_queued, on_starting, on_sent, on_error

        on_queued, on_starting, on_sent, on_error = _slack_reaction_callbacks(client)
        self._queue_manager = MessageQueueManager(
            channel_name="slack",
            on_queued=on_queued,
            on_starting=on_starting,
            on_sent=on_sent,
            on_error=on_error,
        )

        # Run queue event loop in background thread
        self._queue_loop = asyncio.new_event_loop()

        def _run_loop():
            asyncio.set_event_loop(self._queue_loop)
            self._queue_loop.run_forever()

        t = threading.Thread(target=_run_loop, daemon=True)
        t.start()

    def _enqueue_message(self, session: str, project: str, text: str, prefix: str, message: dict):
        """Enqueue a message for async processing via the shared queue manager.

        Called from slack-bolt's sync handler threads. Submits to the queue's
        async event loop running in a background thread.
        """
        msg = QueuedMessage(
            platform_msg=message,
            text=text,
            session=session,
            project=project,
            prefix=prefix,
        )
        asyncio.run_coroutine_threadsafe(self._queue_manager.enqueue(msg), self._queue_loop)

    def run(self):
        """Run the Slack bot with Socket Mode."""
        try:
            from slack_bolt import App
            from slack_bolt.adapter.socket_mode import SocketModeHandler
        except ImportError:
            print("Error: slack-bolt not installed. Install: pip install slack-bolt", file=sys.stderr)
            return

        app = App(token=self.config.bot_token)
        self._app = app

        # Initialize async message queue with Slack's web client for reactions
        self._init_queue_manager(app.client)
        bridge = self

        @app.event("app_mention")
        def handle_mention(event, say, client):
            """Handle @mentions in channels."""
            channel_id = event.get("channel", "")
            user_id = event.get("user", "")
            text = event.get("text", "")

            # Strip the @mention
            text = re.sub(r'<@\w+>\s*', '', text).strip()
            if not text:
                return

            mapping = bridge._get_channel_mapping(channel_id)
            if not mapping:
                # Unmapped channel — use channel ID as session
                session = f"slack-ch-{channel_id}"
                project = bridge._get_channel_project(channel_id)
            else:
                session = mapping.session
                project = mapping.project or bridge._get_channel_project(channel_id)

            # Get user display name
            try:
                user_info = client.users_info(user=user_id)
                display_name = user_info["user"]["profile"].get("display_name") or user_info["user"]["real_name"]
            except Exception:
                display_name = user_id

            # Set up channel project
            try:
                workspace_info = client.team_info()
                workspace_name = workspace_info.get("team", {}).get("name", "Unknown")
            except Exception:
                workspace_name = "Unknown"

            # Compose session config: platform → channel scope → per-channel override
            ch_type, ch_roles, ch_instructions = bridge.compose_channel_config(channel_id)
            _setup_channel_project(
                project, channel_id, f"ch-{channel_id}", workspace_name,
                session_type=ch_type, roles=ch_roles, instructions=ch_instructions,
            )

            # Start portal listener for replies
            def reply_to_channel(reply_text):
                client.chat_postMessage(channel=channel_id, text=reply_text)

            bridge._ensure_portal_listener(session, reply_to_channel)

            # Build prefix and enqueue for async processing
            channel_name = channel_id
            try:
                ch_info = client.conversations_info(channel=channel_id)
                channel_name = ch_info["channel"].get("name", channel_id)
            except Exception:
                pass

            prefix = f"[Slack #{channel_name} from {display_name}: '{text}']"
            print(f"[slack] Channel message in #{channel_name} from {display_name}")

            bridge._enqueue_message(
                session=session, project=project, text=text, prefix=prefix, message=event,
            )

        @app.event("message")
        def handle_dm(event, say, client):
            """Handle DM messages."""
            # Skip bot messages, subtypes (edits, joins, etc.)
            if event.get("bot_id") or event.get("subtype"):
                return

            channel_type = event.get("channel_type", "")
            if channel_type != "im":
                return  # Not a DM

            user_id = event.get("user", "")
            text = event.get("text", "").strip()
            if not text:
                return

            if not bridge._is_allowed(user_id):
                print(f"[slack] Blocked DM from {user_id} — not in allowed_user_ids")
                return

            # Handle commands
            if text.startswith("/"):
                bridge._handle_dm_command(user_id, text, say)
                return

            # Get display name
            try:
                user_info = client.users_info(user=user_id)
                display_name = user_info["user"]["profile"].get("display_name") or user_info["user"]["real_name"]
            except Exception:
                display_name = user_id

            # Set up DM project
            session = bridge._get_dm_session(user_id)
            project = bridge._get_dm_project(user_id)
            # Compose session config: platform → dm scope → per-user override
            dm_type, dm_roles, dm_instructions = bridge.compose_dm_config(user_id)
            _setup_dm_project(
                project, user_id, display_name,
                session_type=dm_type, roles=dm_roles, instructions=dm_instructions,
            )

            # Start portal listener for DM replies
            channel_id = event.get("channel", "")

            def reply_to_dm(reply_text):
                client.chat_postMessage(channel=channel_id, text=reply_text)

            bridge._ensure_portal_listener(session, reply_to_dm)

            prefix = f"[Slack DM from {display_name}: '{text}']"
            print(f"[slack] DM from {display_name} ({user_id})")

            bridge._enqueue_message(
                session=session, project=project, text=text, prefix=prefix, message=event,
            )

        print("Slack bot starting with Socket Mode...")
        if bridge.config.channel_map:
            print(f"  Channel mappings: {len(bridge.config.channel_map)}")
            for ch_id, mapping in bridge.config.channel_map.items():
                print(f"    #{ch_id} → {mapping.session}")
        handler = SocketModeHandler(app, self.config.app_token)
        handler.start()

    def _handle_dm_command(self, user_id: str, text: str, say):
        """Handle /command-style messages in DMs."""
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/start", "/help"):
            sessions = _run_cmd(["list"])
            session_list = sessions.get("sessions", [])
            names = [s.get("name", "?") for s in session_list]
            current = self._get_dm_session(user_id)
            reply = f"*AgentWire Slack Bot*\n\nYour DM session: `{current}`\n"
            if names:
                reply += f"Active sessions: {', '.join(f'`{n}`' for n in names)}\n"
            reply += "\nSend any text to route it to your session.\n"
            reply += "Commands: `/help`, `/list`, `/s <name>`, `/output`, `/new <name>`, `/kill <name>`"
            say(reply)

        elif cmd == "/list":
            sessions = _run_cmd(["list"])
            session_list = sessions.get("sessions", [])
            if session_list:
                lines = [f"`{s.get('name', '?')}` — {s.get('status', '?')}" for s in session_list]
                say("*Sessions:*\n" + "\n".join(lines))
            else:
                say("No active sessions.")

        elif cmd == "/s":
            if not arg:
                say("Usage: `/s <session_name>`")
                return
            self._set_dm_session(user_id, arg)
            say(f"Switched DM session to `{arg}`")

        elif cmd == "/output":
            session = arg or self._get_dm_session(user_id)
            if not session_exists(session):
                say(f"Session `{session}` is not running.")
                return
            output = _run_cmd_raw(["output", "-s", session])
            truncated = output[-SLACK_MAX_MSG:] if len(output) > SLACK_MAX_MSG else output
            say(f"*Output from `{session}`:*\n```\n{truncated}\n```")

        elif cmd == "/new":
            if not arg:
                say("Usage: `/new <session_name>`")
                return
            result = _run_cmd(["new", "-s", arg])
            if result.get("success"):
                self._set_dm_session(user_id, arg)
                say(f"Created and switched DM session to `{arg}`")
            else:
                say(f"Error: {result.get('error', 'unknown')}")

        elif cmd == "/kill":
            if not arg:
                say("Usage: `/kill <session_name>`")
                return
            result = _run_cmd(["kill", "-s", arg])
            if result.get("success"):
                say(f"Killed session `{arg}`")
            else:
                say(f"Error: {result.get('error', 'unknown')}")

        else:
            say(f"Unknown command: `{cmd}`. Try `/help`.")


def run_bridge():
    """Run the Slack bridge (foreground, blocking)."""
    config = _get_slack_config()
    if not config.bot_token:
        print("Error: Slack bot token not configured.", file=sys.stderr)
        print("Set SLACK_BOT_TOKEN env var or channels.slack.bot_token in config.yaml", file=sys.stderr)
        sys.exit(1)
    if not config.app_token:
        print("Error: Slack app token not configured (needed for Socket Mode).", file=sys.stderr)
        print("Set SLACK_APP_TOKEN env var or channels.slack.app_token in config.yaml", file=sys.stderr)
        sys.exit(1)

    bridge = SlackBridge(config)
    bridge.run()


@ChannelRegistry.register("slack")
class SlackChannel(ServiceChannel):
    """Slack service channel via slack-bolt with Socket Mode.

    Run with: agentwire slack start|serve|stop|status

    DMs auto-create per-user sessions (slack-dm-<user_id>).
    Channel @mentions route to mapped sessions via channel_map.
    Sessions auto-created if not running.

    Config:
        channels:
          slack:
            default_session: "agentwire"
            channels_dir: "~/.agentwire/channels/slack"
            allowed_user_ids: ["U12345"]        # DM whitelist (empty = allow all)
            channel_map:
              "C12345": "general"                # Shorthand: label → slack-ch-general
              "C67890":
                session: "backend"
                project: "~/projects/api"
    """

    name = "slack"
    config_class = SlackConfig
    config_key = "slack"
    max_message_length = 2800  # Slack limit is ~4000, leave room for formatting
