"""Notification channels for AgentWire.

Supports sending branded notifications via email (Resend) and Telegram.
"""

import base64
import json
import os
import random
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import resend
from jinja2 import Environment, PackageLoader, select_autoescape

from agentwire.config import get_config


class NotificationError(Exception):
    """Base exception for notification errors."""

    pass


class EmailConfigError(NotificationError):
    """Raised when email configuration is missing or invalid."""

    pass


class TelegramConfigError(NotificationError):
    """Raised when Telegram configuration is missing."""

    pass


# Playful greetings from Echo
GREETINGS = [
    "Hey there! 👋",
    "Psst! Got something for you...",
    "Hoot hoot! 🦉",
    "Quick update for you!",
    "Echo here with news...",
    "Hey! Just popping in...",
    "Got a moment? Here's an update!",
    "Fresh from the wire! ⚡",
]


@dataclass
class Attachment:
    """Email attachment."""

    filename: str
    content: bytes  # Raw bytes
    content_type: Optional[str] = None


@dataclass
class EmailResult:
    """Result of sending an email."""

    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None


def _is_html_content(text: str) -> bool:
    """Check if text appears to be HTML content.

    Detects common HTML patterns to avoid escaping user-provided HTML.
    """
    if not text:
        return False

    # Check for common HTML indicators
    html_patterns = [
        r'<(h[1-6]|p|div|span|table|tr|td|th|ul|ol|li|a|strong|em|br|hr)\b',  # Common tags
        r'<[a-zA-Z][^>]*style\s*=',  # Inline styles
        r'<!DOCTYPE',  # DOCTYPE declaration
        r'<html',  # HTML root
    ]

    for pattern in html_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True

    return False


def _markdown_to_html(text: str) -> str:
    """Convert markdown to HTML using the markdown library.

    Supports tables, nested lists, code blocks, and all standard markdown.
    If text already appears to be HTML, returns it unchanged.
    """
    if not text:
        return ""

    if _is_html_content(text):
        return text

    import markdown

    return markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "nl2br"],
    )


def _render_email_template(
    subject: str,
    body: str,
    attachments: Optional[list[Attachment]] = None,
    greeting: Optional[str] = None,
    banner_image_url: Optional[str] = None,
    echo_image_url: Optional[str] = None,
    echo_small_url: Optional[str] = None,
    logo_image_url: Optional[str] = None,
) -> str:
    """Render the branded email HTML template.

    Args:
        subject: Email subject (shown as heading in email body)
        body: Email body content (markdown or plain text)
        attachments: List of attachments (for display, not actual sending)
        greeting: Custom greeting (random if not specified)
        echo_image_url: URL to Echo owl image for header
        echo_small_url: URL to small Echo image for sign-off
        logo_image_url: URL to AgentWire text logo

    Returns:
        Rendered HTML string
    """
    env = Environment(
        loader=PackageLoader("agentwire", "templates"),
        autoescape=select_autoescape(["html", "xml"]),
    )

    template = env.get_template("email_notification.html")

    # Convert markdown body to HTML
    body_html = _markdown_to_html(body)

    # Pick random greeting if not specified
    if greeting is None:
        greeting = random.choice(GREETINGS)

    return template.render(
        subject=subject,
        body_html=body_html,
        greeting=greeting,
        attachments=attachments or [],
        banner_image_url=banner_image_url,
        echo_image_url=echo_image_url,
        echo_small_url=echo_small_url,
        logo_image_url=logo_image_url,
    )


def send_email(
    to: Optional[str] = None,
    subject: str = "",
    body: str = "",
    attachments: Optional[list[Path | str]] = None,
    from_address: Optional[str] = None,
    greeting: Optional[str] = None,
    plain_text: bool = False,
) -> EmailResult:
    """Send a branded email via Resend.

    Args:
        to: Recipient email address. Uses config default_to if not specified.
        subject: Email subject line.
        body: Email body (markdown supported, converted to HTML).
        attachments: List of file paths to attach.
        from_address: Sender address. Uses config from_address if not specified.
        greeting: Custom greeting. Random if not specified.
        plain_text: If True, send as plain text only (no HTML template).

    Returns:
        EmailResult with success status and message_id or error.

    Raises:
        EmailConfigError: If required configuration is missing.
    """
    config = get_config()
    email_config = config.notifications.email

    # Validate API key
    if not email_config.api_key:
        raise EmailConfigError(
            "Email API key not configured. "
            "Set RESEND_API_KEY in ~/.agentwire/.env "
            "or set notifications.email.api_key in ~/.agentwire/config.yaml"
        )

    # Determine recipient
    recipient = to or email_config.default_to
    if not recipient:
        raise EmailConfigError(
            "No recipient specified and no default_to configured in "
            "notifications.email.default_to"
        )

    # Determine sender
    sender = from_address or email_config.from_address
    if not sender:
        raise EmailConfigError(
            "No sender address configured. "
            "Set notifications.email.from_address in ~/.agentwire/config.yaml"
        )

    # Configure Resend
    resend.api_key = email_config.api_key

    # Process attachments
    attachment_objects: list[Attachment] = []
    resend_attachments = []

    if attachments:
        for attachment_path in attachments:
            path = Path(attachment_path)
            if not path.exists():
                return EmailResult(success=False, error=f"Attachment not found: {path}")

            content = path.read_bytes()
            attachment_objects.append(Attachment(filename=path.name, content=content))

            # Resend wants base64 content as string
            resend_attachments.append({
                "filename": path.name,
                "content": base64.b64encode(content).decode("utf-8"),
            })

    # Build email params
    email_subject = subject or "(no subject)"

    params: dict = {
        "from": sender,
        "to": [recipient],
        "subject": email_subject,
    }

    if plain_text:
        params["text"] = body
    else:
        # Render branded HTML template
        html_content = _render_email_template(
            subject=email_subject,
            body=body,
            attachments=attachment_objects,
            greeting=greeting,
            banner_image_url=email_config.banner_image_url or None,
            echo_image_url=email_config.echo_image_url or None,
            echo_small_url=email_config.echo_small_url or None,
            logo_image_url=email_config.logo_image_url or None,
        )
        params["html"] = html_content
        # Also include plain text version
        params["text"] = body

    if resend_attachments:
        params["attachments"] = resend_attachments

    # Send email
    try:
        response = resend.Emails.send(params)
        message_id = response.get("id") if isinstance(response, dict) else str(response)
        return EmailResult(success=True, message_id=message_id)
    except resend.exceptions.ResendError as e:
        return EmailResult(success=False, error=str(e))
    except Exception as e:
        return EmailResult(success=False, error=f"Unexpected error: {e}")


def cmd_email(args) -> int:
    """CLI handler for email command.

    Sends a branded email notification via Resend.
    """
    # Get body from args or stdin
    body = args.body
    if not body and not sys.stdin.isatty():
        body = sys.stdin.read()

    if not body:
        print("Error: No message body provided. Use --body or pipe content.", file=sys.stderr)
        return 1

    subject = args.subject or "[AgentWire] Notification"

    # Process attachments
    attachments = None
    if hasattr(args, "attach") and args.attach:
        attachments = args.attach if isinstance(args.attach, list) else [args.attach]

    try:
        result = send_email(
            to=args.to,
            subject=subject,
            body=body,
            attachments=attachments,
            plain_text=getattr(args, "plain", False),
        )

        if result.success:
            if not getattr(args, "quiet", False):
                print(f"Email sent (id: {result.message_id})")
            return 0
        else:
            print(f"Error sending email: {result.error}", file=sys.stderr)
            return 1

    except EmailConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


# === Telegram Notifications ===


@dataclass
class TelegramResult:
    """Result of sending a Telegram message."""

    success: bool
    message_id: Optional[int] = None
    error: Optional[str] = None


def _get_telegram_config() -> tuple[str, list[int]]:
    """Get Telegram bot token and user IDs from env/config.

    Returns:
        (bot_token, user_ids) tuple.

    Raises:
        TelegramConfigError if not configured.
    """
    from dotenv import load_dotenv

    load_dotenv()
    load_dotenv(Path.home() / ".agentwire" / ".env")

    bot_token = os.environ.get("TELEGRAM_AGENTWIRE_BOT_TOKEN", "")
    if not bot_token:
        try:
            import yaml

            config_path = Path.home() / ".agentwire" / "config.yaml"
            if config_path.exists():
                with open(config_path) as f:
                    cfg = yaml.safe_load(f) or {}
                    bot_token = cfg.get("telegram", {}).get("bot_token", "")
        except Exception:
            pass

    if not bot_token:
        raise TelegramConfigError(
            "No Telegram bot token. Set TELEGRAM_AGENTWIRE_BOT_TOKEN or "
            "telegram.bot_token in ~/.agentwire/config.yaml"
        )

    user_ids_env = os.environ.get("TELEGRAM_USER_ID", "")
    if user_ids_env:
        user_ids = [int(uid.strip()) for uid in user_ids_env.split(",") if uid.strip()]
    else:
        try:
            import yaml

            config_path = Path.home() / ".agentwire" / "config.yaml"
            if config_path.exists():
                with open(config_path) as f:
                    cfg = yaml.safe_load(f) or {}
                    user_ids = cfg.get("telegram", {}).get("allowed_users", [])
        except Exception:
            user_ids = []

    if not user_ids:
        raise TelegramConfigError(
            "No Telegram user IDs. Set TELEGRAM_USER_ID or "
            "telegram.allowed_users in ~/.agentwire/config.yaml"
        )

    return bot_token, user_ids


def send_telegram(
    text: str,
    chat_id: Optional[int] = None,
    parse_mode: Optional[str] = None,
) -> TelegramResult:
    """Send a text message via Telegram Bot API.

    Uses urllib (no aiogram dependency) so it can be called from anywhere
    including shell scripts via CLI.

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


def cmd_telegram_notify(args) -> int:
    """CLI handler for sending Telegram notifications."""
    body = getattr(args, "body", None) or ""
    if not body and not sys.stdin.isatty():
        body = sys.stdin.read()

    if not body:
        print("Error: No message body. Use --body or pipe content.", file=sys.stderr)
        return 1

    chat_id = getattr(args, "chat_id", None)

    try:
        result = send_telegram(text=body, chat_id=chat_id)
        if result.success:
            if not getattr(args, "quiet", False):
                print(f"Telegram message sent (id: {result.message_id})")
            return 0
        else:
            print(f"Error: {result.error}", file=sys.stderr)
            return 1
    except TelegramConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1
