"""SMS channel — send-only via Twilio API.

Requires the `twilio` package (optional dependency).
Install: pip install twilio
"""

import os
import sys
from dataclasses import dataclass
from typing import Optional

from .base import (
    ChannelRegistry,
    ChannelResult,
    NotificationError,
    SendOnlyChannel,
)


class SMSConfigError(NotificationError):
    """Raised when SMS configuration is missing or invalid."""

    pass


@dataclass
class SMSConfig:
    """Twilio SMS configuration."""

    account_sid: str = ""
    auth_token: str = ""
    from_number: str = ""
    default_to: str = ""

    def __post_init__(self):
        if not self.account_sid:
            self.account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
        if not self.auth_token:
            self.auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")


def _get_sms_config() -> SMSConfig:
    """Get SMS config from channels registry."""
    from agentwire.config import get_config

    config = get_config()
    sms_config = config.channels.get("sms")
    if sms_config:
        return sms_config
    return SMSConfig()


def send_sms(
    body: str,
    to: Optional[str] = None,
) -> ChannelResult:
    """Send an SMS via Twilio.

    Args:
        body: Message text.
        to: Recipient phone number (+E.164 format). Uses config default_to if not specified.

    Returns:
        ChannelResult with success status.
    """
    try:
        from twilio.rest import Client
    except ImportError:
        return ChannelResult(
            success=False,
            error="Twilio not installed. Install: pip install twilio",
        )

    sms_config = _get_sms_config()

    if not sms_config.account_sid or not sms_config.auth_token:
        raise SMSConfigError(
            "Twilio credentials not configured. Set TWILIO_ACCOUNT_SID and "
            "TWILIO_AUTH_TOKEN env vars or channels.sms in ~/.agentwire/config.yaml"
        )

    recipient = to or sms_config.default_to
    if not recipient:
        raise SMSConfigError(
            "No recipient specified and no default_to configured in channels.sms"
        )

    if not sms_config.from_number:
        raise SMSConfigError(
            "No from_number configured in channels.sms"
        )

    try:
        client = Client(sms_config.account_sid, sms_config.auth_token)
        msg = client.messages.create(
            body=body,
            from_=sms_config.from_number,
            to=recipient,
        )
        return ChannelResult(success=True, message_id=msg.sid)
    except Exception as e:
        return ChannelResult(success=False, error=str(e))


def cmd_sms(args) -> int:
    """CLI handler for sms command."""
    body = args.body
    if not body and not sys.stdin.isatty():
        body = sys.stdin.read()

    if not body:
        print("Error: No message body provided. Use --body or pipe content.", file=sys.stderr)
        return 1

    try:
        result = send_sms(body=body, to=getattr(args, "to", None))

        if result.success:
            if not getattr(args, "quiet", False):
                print(f"SMS sent (id: {result.message_id})")
            return 0
        else:
            print(f"Error sending SMS: {result.error}", file=sys.stderr)
            return 1

    except SMSConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


@ChannelRegistry.register("sms")
class SMSChannel(SendOnlyChannel):
    """SMS send-only channel via Twilio API."""

    name = "sms"
    config_class = SMSConfig
    config_key = "sms"

    async def send(self, text: str, **kwargs) -> ChannelResult:
        return send_sms(body=text, to=kwargs.get("to"))
