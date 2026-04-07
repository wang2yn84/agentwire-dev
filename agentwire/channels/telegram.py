"""Telegram channel — service channel via aiogram bot.

Send-only notification functions (send_telegram, check_telegram_bot) live here.
The full bot bridge lives in bridges/telegram.py (unchanged).
"""

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

from .base import (
    ChannelRegistry,
    ChannelResult,
    NotificationError,
    ServiceChannel,
)


class TelegramConfigError(NotificationError):
    """Raised when Telegram configuration is missing."""

    pass


@dataclass
class TelegramConfig:
    """Telegram bot configuration."""

    bot_token: str = ""
    allowed_users: list[int] = field(default_factory=list)
    default_session: str = "main"
    voice_replies: bool = True
    forward_questions: bool = True
    forward_alerts: bool = True
    session_name: str = "agentwire-telegram"

    def __post_init__(self):
        if not self.bot_token:
            self.bot_token = os.environ.get("TELEGRAM_AGENTWIRE_BOT_TOKEN", "")


@dataclass
class TelegramResult:
    """Result of sending a Telegram message."""

    success: bool
    message_id: Optional[int] = None
    error: Optional[str] = None


def _get_telegram_config() -> tuple[str, list[int]]:
    """Get Telegram bot token and user IDs from the channel registry config.

    Returns:
        (bot_token, user_ids) tuple.

    Raises:
        TelegramConfigError if not configured.
    """
    from agentwire.config import get_config

    config = get_config()
    tg_config = config.channels.get("telegram")
    if not tg_config:
        tg_config = TelegramConfig()

    if not tg_config.bot_token:
        raise TelegramConfigError(
            "No Telegram bot token. Set TELEGRAM_AGENTWIRE_BOT_TOKEN or "
            "channels.telegram.bot_token in ~/.agentwire/config.yaml"
        )

    if not tg_config.allowed_users:
        raise TelegramConfigError(
            "No Telegram user IDs. Set telegram.allowed_users "
            "in ~/.agentwire/config.yaml"
        )

    return tg_config.bot_token, tg_config.allowed_users


def send_telegram(
    text: str,
    chat_id: Optional[int] = None,
    parse_mode: Optional[str] = None,
) -> TelegramResult:
    """Send a text message via Telegram Bot API.

    Uses urllib (no aiogram dependency) so it can be called from anywhere.

    Args:
        text: Message text (max 4096 chars).
        chat_id: Target chat ID. Uses first allowed_user if not specified.
        parse_mode: Optional "Markdown" or "HTML".

    Returns:
        TelegramResult with success status.
    """
    bot_token, user_ids = _get_telegram_config()
    target = chat_id or user_ids[0]

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": target,
        "text": text[:4096],
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            if result.get("ok"):
                msg_id = result.get("result", {}).get("message_id")
                return TelegramResult(success=True, message_id=msg_id)
            return TelegramResult(success=False, error=result.get("description", "Unknown error"))
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        return TelegramResult(success=False, error=f"HTTP {e.code}: {body[:200]}")
    except Exception as e:
        return TelegramResult(success=False, error=str(e))


def check_telegram_bot() -> tuple[bool, str]:
    """Check if Telegram bot is configured and reachable.

    Returns:
        (healthy, info_string) tuple.
    """
    try:
        bot_token, _ = _get_telegram_config()
    except TelegramConfigError as e:
        return False, str(e)

    url = f"https://api.telegram.org/bot{bot_token}/getMe"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode())
            if result.get("ok"):
                username = result["result"].get("username", "?")
                return True, f"@{username}"
            return False, result.get("description", "Unknown error")
    except Exception as e:
        return False, str(e)


@ChannelRegistry.register("telegram")
class TelegramChannel(ServiceChannel):
    """Telegram service channel via aiogram bot.

    The full bot bridge lives in bridges/telegram.py.
    This channel class wraps it for the registry.
    """

    name = "telegram"
    config_class = TelegramConfig
    config_key = "telegram"
