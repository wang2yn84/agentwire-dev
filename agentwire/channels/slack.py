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
import time
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


class SlackConfigError(NotificationError):
    """Raised when Slack configuration is missing."""

    pass


@dataclass
class SlackChannelMapping:
    """Maps a Slack channel to an agentwire session."""

    session: str = ""
    project: str = ""


@dataclass
class SlackConfig:
    """Slack bot configuration."""

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

    def __post_init__(self):
        if not self.bot_token:
            self.bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
        if not self.app_token:
            self.app_token = os.environ.get("SLACK_APP_TOKEN", "")
        self.channels_dir = str(Path(self.channels_dir).expanduser())
        # Normalize channel_map
        normalized = {}
        for channel_id, value in self.channel_map.items():
            channel_id = str(channel_id)
            if isinstance(value, str):
                normalized[channel_id] = SlackChannelMapping(session=f"slack-ch-{value}")
            elif isinstance(value, dict):
                label = value.get("label", channel_id)
                session = value.get("session", f"slack-ch-{label}")
                normalized[channel_id] = SlackChannelMapping(
                    session=session,
                    project=value.get("project", ""),
                )
            elif isinstance(value, SlackChannelMapping):
                normalized[channel_id] = value
        self.channel_map = normalized


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


def _setup_dm_project(project_dir: str, user_id: str, display_name: str):
    """Set up a DM user's project folder with config, CLAUDE.md, and git repo."""
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

    config_file = project / ".agentwire.yml"
    if not config_file.exists():
        config_file.write_text(
            "type: claude-bypass\n"
            "roles:\n"
            "  - agentwire\n"
            "  - slack-dm\n"
        )

    claude_file = project / "CLAUDE.md"
    if not claude_file.exists():
        from datetime import datetime
        claude_file.write_text(
            f"# Slack DM — {display_name}\n\n"
            f"**Slack user:** {display_name}\n"
            f"**User ID:** {user_id}\n"
            f"**First contact:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            f"## About This User\n\n"
            f"<!-- Add notes about this user here. The agent reads this on every message. -->\n\n"
            f"## Instructions\n\n"
            f"<!-- Add user-specific instructions here. -->\n"
        )

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


def _setup_channel_project(project_dir: str, channel_id: str, channel_name: str, workspace_name: str):
    """Set up a Slack channel's project folder."""
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

    config_file = project / ".agentwire.yml"
    if not config_file.exists():
        config_file.write_text(
            "type: claude-bypass\n"
            "roles:\n"
            "  - agentwire\n"
            "  - slack-dm\n"
        )

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
            f"<!-- Describe what this channel is for. -->\n\n"
            f"## Instructions\n\n"
            f"<!-- Add channel-specific instructions here. -->\n"
        )

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


def _session_exists(session: str) -> bool:
    """Check if an agentwire session is running."""
    result = _run_cmd(["info", "-s", session])
    return result.get("success", False)


def _ensure_session(session: str, project: str = "") -> bool:
    """Ensure a session exists, creating it if needed."""
    if _session_exists(session):
        return True

    args = ["new", "-s", session]
    if project:
        expanded = str(Path(project).expanduser())
        args.extend(["-p", expanded])

    result = _run_cmd(args)
    return result.get("success", False)


def _wait_for_session_ready(session: str, max_wait: int = 30):
    """Wait for session to be ready. Uses agentwire's core _wait_for_agent_ready."""
    try:
        from agentwire.__main__ import _wait_for_agent_ready
        ready = _wait_for_agent_ready(session, timeout=max_wait)
        if ready:
            print(f"[slack] Session '{session}' ready")
        else:
            print(f"[slack] Session '{session}' may not be fully loaded after {max_wait}s, sending anyway")
    except ImportError:
        # Fallback if import fails
        time.sleep(8)


# Emoji status indicators (Slack reaction names, no colons)
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
                except Exception:
                    await asyncio.sleep(5)
        except Exception:
            pass

    def _process_message(self, session: str, project: str, text: str, prefix: str,
                         message: dict, say, client, is_new_session: bool):
        """Process a message synchronously (called from slack-bolt handler thread)."""
        channel = message.get("channel", "")
        ts = message.get("ts", "")

        # Add queued reaction
        try:
            client.reactions_add(channel=channel, timestamp=ts, name=EMOJI_QUEUED)
        except Exception:
            pass

        # Wait for session if just created
        if is_new_session:
            # Swap to rocket
            try:
                client.reactions_remove(channel=channel, timestamp=ts, name=EMOJI_QUEUED)
                client.reactions_add(channel=channel, timestamp=ts, name=EMOJI_STARTING)
            except Exception:
                pass

            _wait_for_session_ready(session)

            try:
                client.reactions_remove(channel=channel, timestamp=ts, name=EMOJI_STARTING)
            except Exception:
                pass

        # Send to session
        print(f"[slack] Sending to session '{session}': {text[:50]}")
        result = _run_cmd_no_json(["send", "-s", session, prefix])
        print(f"[slack] Send result: {result}")

        if result.get("success", False):
            try:
                # Remove queued if still there
                try:
                    client.reactions_remove(channel=channel, timestamp=ts, name=EMOJI_QUEUED)
                except Exception:
                    pass
                client.reactions_add(channel=channel, timestamp=ts, name=EMOJI_SENT)
            except Exception:
                pass
        else:
            try:
                try:
                    client.reactions_remove(channel=channel, timestamp=ts, name=EMOJI_QUEUED)
                except Exception:
                    pass
                client.reactions_add(channel=channel, timestamp=ts, name=EMOJI_ERROR)
            except Exception:
                pass
            say(f"Error sending to `{session}`: {result.get('error', 'unknown')}")

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

            _setup_channel_project(project, channel_id, f"ch-{channel_id}", workspace_name)

            # Ensure session
            is_new = not _session_exists(session)
            if is_new:
                if not _ensure_session(session, project):
                    say(f"Failed to start session `{session}`")
                    return

            # Start portal listener for replies
            def reply_to_channel(reply_text):
                client.chat_postMessage(channel=channel_id, text=reply_text)

            bridge._ensure_portal_listener(session, reply_to_channel)

            # Build prefix and process
            channel_name = channel_id
            try:
                ch_info = client.conversations_info(channel=channel_id)
                channel_name = ch_info["channel"].get("name", channel_id)
            except Exception:
                pass

            prefix = f"[Slack #{channel_name} from {display_name}: '{text}']"
            print(f"[slack] Channel message in #{channel_name} from {display_name}")

            bridge._process_message(
                session=session, project=project, text=text, prefix=prefix,
                message=event, say=say, client=client, is_new_session=is_new,
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
            _setup_dm_project(project, user_id, display_name)

            # Ensure session
            is_new = not _session_exists(session)
            if is_new:
                if not _ensure_session(session, project):
                    say(f"Failed to start session `{session}`")
                    return

            # Start portal listener for DM replies
            channel_id = event.get("channel", "")

            def reply_to_dm(reply_text):
                client.chat_postMessage(channel=channel_id, text=reply_text)

            bridge._ensure_portal_listener(session, reply_to_dm)

            prefix = f"[Slack DM from {display_name}: '{text}']"
            print(f"[slack] DM from {display_name} ({user_id})")

            bridge._process_message(
                session=session, project=project, text=text, prefix=prefix,
                message=event, say=say, client=client, is_new_session=is_new,
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
            if not _session_exists(session):
                say(f"Session `{session}` is not running.")
                return
            output = _run_cmd_raw(["output", "-s", session])
            truncated = output[-2800:] if len(output) > 2800 else output
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
