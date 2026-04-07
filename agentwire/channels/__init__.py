"""AgentWire channels — pluggable communication integrations."""

from .base import (
    Channel,
    ChannelRegistry,
    ChannelResult,
    MessageQueueManager,
    NotificationError,
    QueuedMessage,
    SendOnlyChannel,
    ServiceChannel,
    compose_session_config,
    ensure_session,
    inject_instructions,
    session_exists,
    wait_for_session_ready,
)

# Auto-register built-in channels
from . import email  # noqa: F401
from . import telegram  # noqa: F401
from . import quo  # noqa: F401
from . import sms  # noqa: F401
from . import webhook  # noqa: F401
from . import discord  # noqa: F401
from . import slack  # noqa: F401

__all__ = [
    "Channel",
    "ChannelRegistry",
    "ChannelResult",
    "MessageQueueManager",
    "NotificationError",
    "QueuedMessage",
    "SendOnlyChannel",
    "ServiceChannel",
    "compose_session_config",
    "ensure_session",
    "inject_instructions",
    "session_exists",
    "wait_for_session_ready",
]
