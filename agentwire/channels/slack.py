"""Slack channel — service channel via slack-bolt with Socket Mode.

Requires the `slack-bolt` package (optional dependency).
Install: pip install slack-bolt

Socket Mode = outbound WebSocket connection, no public URL needed.
DM-based interaction with slash commands.
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


class SlackConfigError(NotificationError):
    """Raised when Slack configuration is missing."""

    pass


@dataclass
class SlackConfig:
    """Slack bot configuration."""

    bot_token: str = ""  # xoxb-...
    app_token: str = ""  # xapp-... (for Socket Mode)
    allowed_user_ids: list[str] = field(default_factory=list)  # Slack user IDs are strings
    default_session: str = "main"
    voice_replies: bool = True
    forward_questions: bool = True
    forward_alerts: bool = True
    session_name: str = "agentwire-slack"

    def __post_init__(self):
        if not self.bot_token:
            self.bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
        if not self.app_token:
            self.app_token = os.environ.get("SLACK_APP_TOKEN", "")


def _get_slack_config() -> SlackConfig:
    """Get Slack config from channels registry."""
    from agentwire.config import get_config

    config = get_config()
    sl_config = config.channels.get("slack")
    if sl_config:
        return sl_config
    return SlackConfig()


# State file for per-user session tracking
STATE_FILE = Path.home() / ".agentwire" / "slack-state.json"


class SlackBridge:
    """Slack bot bridge to AgentWire sessions."""

    def __init__(self, config: SlackConfig):
        self.config = config
        self.user_sessions: dict[str, str] = {}  # slack_user_id → session_name
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

    def _get_session(self, user_id: str) -> str:
        return self.user_sessions.get(user_id, self.config.default_session)

    def _set_session(self, user_id: str, session: str):
        self.user_sessions[user_id] = session
        self._save_state()

    def _is_allowed(self, user_id: str) -> bool:
        if not self.config.allowed_user_ids:
            return True
        return user_id in self.config.allowed_user_ids

    def run(self):
        """Run the Slack bot with Socket Mode."""
        try:
            from slack_bolt import App
            from slack_bolt.adapter.socket_mode import SocketModeHandler
        except ImportError:
            print("Error: slack-bolt not installed. Install: pip install slack-bolt", file=sys.stderr)
            return

        app = App(token=self.config.bot_token)
        bridge = self

        @app.message("")
        def handle_message(message, say):
            """Handle any DM text message."""
            user_id = message.get("user", "")
            if not bridge._is_allowed(user_id):
                return

            text = message.get("text", "").strip()
            if not text:
                return

            # Check for commands
            if text.startswith("/"):
                bridge._handle_command(user_id, text, say)
                return

            # Route to session
            session = bridge._get_session(user_id)
            result = _run_cmd(["send", "-s", session, text])
            if not result.get("success", False):
                say(f"Error sending to session `{session}`: {result.get('error', 'unknown')}")

        @app.command("/aw-list")
        def handle_list(ack, respond):
            """List active sessions."""
            ack()
            sessions = _run_cmd(["list"])
            session_list = sessions.get("sessions", [])
            if session_list:
                lines = [f"`{s.get('name', '?')}` — {s.get('status', '?')}" for s in session_list]
                respond("*Sessions:*\n" + "\n".join(lines))
            else:
                respond("No active sessions.")

        @app.command("/aw-send")
        def handle_send(ack, respond, command):
            """Send message to a session."""
            ack()
            text = command.get("text", "").strip()
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                respond("Usage: `/aw-send <session> <message>`")
                return
            session, msg = parts
            result = _run_cmd(["send", "-s", session, msg])
            if result.get("success"):
                respond(f"Sent to `{session}`")
            else:
                respond(f"Error: {result.get('error', 'unknown')}")

        @app.command("/aw-output")
        def handle_output(ack, respond, command):
            """Show session output."""
            ack()
            session = command.get("text", "").strip()
            if not session:
                user_id = command.get("user_id", "")
                session = bridge._get_session(user_id)
            output = _run_cmd_raw(["output", "-s", session])
            truncated = output[-2800:] if len(output) > 2800 else output
            respond(f"*Output from `{session}`:*\n```\n{truncated}\n```")

        @app.event("file_shared")
        def handle_file_shared(event, say):
            """Handle audio file uploads for STT."""
            # File shared events need additional API calls to get file info
            # This is a placeholder — full implementation needs files.info call
            pass

        print("Slack bot starting with Socket Mode...")
        handler = SocketModeHandler(app, self.config.app_token)
        handler.start()

    def _handle_command(self, user_id: str, text: str, say):
        """Handle /command-style messages in DMs."""
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "/start" or cmd == "/help":
            sessions = _run_cmd(["list"])
            session_list = sessions.get("sessions", [])
            names = [s.get("name", "?") for s in session_list]
            current = self._get_session(user_id)
            reply = f"*AgentWire Slack Bot*\n\nCurrent session: `{current}`\n"
            if names:
                reply += f"Sessions: {', '.join(f'`{n}`' for n in names)}\n"
            reply += "\nSend any text to route it to your current session.\n"
            reply += "Commands: `/start`, `/list`, `/s <name>`, `/output`, `/new <name>`, `/kill <name>`"
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
            self._set_session(user_id, arg)
            say(f"Switched to session `{arg}`")

        elif cmd == "/output":
            session = self._get_session(user_id)
            output = _run_cmd_raw(["output", "-s", session])
            truncated = output[-2800:] if len(output) > 2800 else output
            say(f"*Output from `{session}`:*\n```\n{truncated}\n```")

        elif cmd == "/new":
            if not arg:
                say("Usage: `/new <session_name>`")
                return
            result = _run_cmd(["new", "-s", arg])
            if result.get("success"):
                self._set_session(user_id, arg)
                say(f"Created and switched to session `{arg}`")
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
    """

    name = "slack"
    config_class = SlackConfig
    config_key = "slack"
