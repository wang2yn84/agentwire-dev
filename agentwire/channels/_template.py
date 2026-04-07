"""Template: How to create a custom AgentWire channel.

Copy this file, rename it (remove underscore prefix), and implement your channel.
It will be auto-discovered by the channel registry when imported in __init__.py.

IMPORTANT: The underscore prefix prevents this template from being auto-registered.
When you copy it, remove the underscore: _template.py → my_channel.py

Two channel types:
  - SendOnlyChannel: Stateless outbound (email, SMS, webhook)
  - ServiceChannel: Bidirectional with service process (Telegram, Discord, Slack)
"""

import os
from dataclasses import dataclass
from typing import Optional

from .base import (
    ChannelRegistry,
    ChannelResult,
    NotificationError,
    SendOnlyChannel,
    ServiceChannel,
)


# === Step 1: Define your config dataclass ===
# Each channel owns its config. Fields map to YAML under channels.{config_key}:
#
# channels:
#   my_channel:
#     api_key: "your-key"
#     default_recipient: "user@example.com"


@dataclass
class MyChannelConfig:
    api_key: str = ""
    default_recipient: str = ""

    def __post_init__(self):
        # Env var fallback pattern — check env if YAML value is empty
        if not self.api_key:
            self.api_key = os.environ.get("MY_CHANNEL_API_KEY", "")


# === Step 2: Define your channel class ===
# Uncomment the @register decorator to activate this channel.


# @ChannelRegistry.register("my_channel")
class MyChannel(SendOnlyChannel):
    """Example send-only channel.

    For a service channel (bidirectional, long-lived), inherit from
    ServiceChannel instead and implement start()/stop()/status().
    """

    name = "my_channel"
    config_class = MyChannelConfig
    config_key = "my_channel"

    async def send(self, text: str, **kwargs) -> ChannelResult:
        """Send a message through your channel.

        Args:
            text: The message to send.
            **kwargs: Channel-specific options (e.g., recipient, subject).

        Returns:
            ChannelResult with success/error info.
        """
        # Your send logic here. Example:
        #
        # import my_sdk
        # client = my_sdk.Client(api_key=self.config.api_key)
        # result = client.send(text=text, to=kwargs.get("to", self.config.default_recipient))
        # return ChannelResult(success=True, message_id=result.id)
        #
        return ChannelResult(success=False, error="Not implemented")


# === Step 3: Use primitives (optional) ===
# The base class gives you TTS/STT and session interaction for free:
#
#   # Generate speech from text
#   audio_bytes = await self.tts("Hello world!", voice="my_voice")
#
#   # Transcribe audio to text
#   text = await self.stt(audio_bytes, format="wav")
#
#   # List available voices
#   voices = self.voices_available()
#
#   # Route a message to an agentwire session
#   self.send_to_session("main", "User said: hello")
#
#   # Read recent output from a session
#   output = self.get_session_output("main", lines=20)
#
#   # List active sessions
#   sessions = self.list_sessions()


# === Step 4: Service channel example ===
# For bidirectional channels (bots, bridges), inherit ServiceChannel:
#
# @ChannelRegistry.register("my_bot")
# class MyBotChannel(ServiceChannel):
#     name = "my_bot"
#     config_class = MyBotConfig
#     config_key = "my_bot"
#
#     async def start(self):
#         """Start the bot service."""
#         ...
#
#     async def stop(self):
#         """Stop the bot service."""
#         ...
#
#     async def status(self) -> dict:
#         """Return service status."""
#         return {"running": True, "connected_users": 5}
#
# Service channels typically:
# 1. Run in their own tmux session (agentwire my_bot start)
# 2. Subscribe to portal WebSocket for outbound events
# 3. Route inbound messages to sessions via self.send_to_session()
# 4. Maintain per-user state in ~/.agentwire/my_bot-state.json
#
# See channels/discord.py or channels/slack.py for full examples.


# === Step 5: Add CLI commands (optional) ===
# For send-only: add to __main__.py argparse with --body, --to, --json flags
# For service: add start|serve|stop|status subcommands
# See the existing patterns in __main__.py for examples.


# === Step 6: Add MCP tools (optional) ===
# Add to mcp_server.py:
#
# @mcp.tool()
# def my_channel_send(text: str, to: str | None = None) -> str:
#     data = run_agentwire_cmd(["my_channel", "--body", text], json_output=False)
#     ...


# === Step 7: Register in __init__.py ===
# Add to agentwire/channels/__init__.py:
#
# from . import my_channel  # noqa: F401
#
# This triggers the @register decorator and makes your channel discoverable.
