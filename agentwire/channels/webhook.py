"""Webhook channel — send-only via HTTP POST.

No external dependencies — uses urllib from stdlib.
"""

import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

from .base import (
    ChannelRegistry,
    ChannelResult,
    NotificationError,
    SendOnlyChannel,
)


class WebhookConfigError(NotificationError):
    """Raised when webhook configuration is missing or invalid."""

    pass


@dataclass
class WebhookConfig:
    """Webhook configuration."""

    url: str = ""
    method: str = "POST"
    headers: dict = field(default_factory=dict)
    content_type: str = "application/json"


def _get_webhook_config() -> WebhookConfig:
    """Get webhook config from channels registry."""
    from agentwire.config import get_config

    config = get_config()
    wh_config = config.channels.get("webhook")
    if wh_config:
        return wh_config
    return WebhookConfig()


def send_webhook(
    text: str,
    url: Optional[str] = None,
    extra: Optional[dict] = None,
) -> ChannelResult:
    """Send a message via HTTP webhook.

    Args:
        text: Message text.
        url: Target URL. Uses config url if not specified.
        extra: Additional fields to include in the JSON payload.

    Returns:
        ChannelResult with success status.
    """
    wh_config = _get_webhook_config()

    target_url = url or wh_config.url
    if not target_url:
        raise WebhookConfigError(
            "No URL specified and no default url configured in channels.webhook"
        )

    payload = {"text": text}
    if extra:
        payload.update(extra)

    data = json.dumps(payload).encode("utf-8")

    headers = {"Content-Type": wh_config.content_type}
    headers.update(wh_config.headers)

    req = urllib.request.Request(
        target_url,
        data=data,
        headers=headers,
        method=wh_config.method,
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return ChannelResult(success=resp.status < 400)
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200] if e.fp else ""
        return ChannelResult(success=False, error=f"HTTP {e.code}: {body}")
    except Exception as e:
        return ChannelResult(success=False, error=str(e))


def cmd_webhook(args) -> int:
    """CLI handler for webhook command."""
    body = args.body
    if not body and not sys.stdin.isatty():
        body = sys.stdin.read()

    if not body:
        print("Error: No message body provided. Use --body or pipe content.", file=sys.stderr)
        return 1

    try:
        result = send_webhook(body=body, url=getattr(args, "url", None))

        if result.success:
            if not getattr(args, "quiet", False):
                print("Webhook sent successfully.")
            return 0
        else:
            print(f"Error sending webhook: {result.error}", file=sys.stderr)
            return 1

    except WebhookConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


@ChannelRegistry.register("webhook")
class WebhookChannel(SendOnlyChannel):
    """Webhook send-only channel via HTTP POST."""

    name = "webhook"
    config_class = WebhookConfig
    config_key = "webhook"

    async def send(self, text: str, **kwargs) -> ChannelResult:
        return send_webhook(
            text=text,
            url=kwargs.get("url"),
            extra=kwargs.get("extra"),
        )
