"""Tests for agentwire/channels/ — registry, base classes, config, and channel modules."""

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from agentwire.channels.base import (
    Channel,
    ChannelRegistry,
    ChannelResult,
    NotificationError,
    SendOnlyChannel,
    ServiceChannel,
    _run_cmd,
    _run_cmd_raw,
)


# =============================================================================
# ChannelResult
# =============================================================================


class TestChannelResult:
    def test_success_result(self):
        r = ChannelResult(success=True, message_id="abc123")
        assert r.success is True
        assert r.message_id == "abc123"
        assert r.error is None

    def test_error_result(self):
        r = ChannelResult(success=False, error="Something failed")
        assert r.success is False
        assert r.message_id is None
        assert r.error == "Something failed"

    def test_int_message_id(self):
        r = ChannelResult(success=True, message_id=42)
        assert r.message_id == 42


# =============================================================================
# ChannelRegistry
# =============================================================================


class TestChannelRegistry:
    def test_builtin_channels_registered(self):
        """All 6 built-in channels should be registered."""
        channels = ChannelRegistry.all()
        assert "email" in channels
        assert "telegram" in channels
        assert "quo" in channels
        assert "sms" in channels
        assert "webhook" in channels
        assert "discord" in channels
        assert "slack" in channels

    def test_exactly_seven_builtins(self):
        channels = ChannelRegistry.all()
        assert len(channels) == 7

    def test_get_existing(self):
        cls = ChannelRegistry.get("email")
        assert cls is not None
        assert cls.name == "email"

    def test_get_nonexistent(self):
        assert ChannelRegistry.get("nonexistent_channel") is None

    def test_builtin_set_matches_registered(self):
        """BUILTIN_CHANNELS set should match what's actually registered."""
        for name in ChannelRegistry.BUILTIN_CHANNELS:
            assert name in ChannelRegistry._channels, f"{name} in BUILTIN_CHANNELS but not registered"

    def test_register_decorator(self):
        """@register decorator should add class to registry."""
        # Temporarily register, then clean up
        @ChannelRegistry.register("_test_channel")
        class _TestChannel(SendOnlyChannel):
            name = "_test_channel"

        assert "_test_channel" in ChannelRegistry._channels
        # Clean up
        del ChannelRegistry._channels["_test_channel"]

    def test_channel_types(self):
        """Verify channel types are correct."""
        from agentwire.channels.email import EmailChannel
        from agentwire.channels.telegram import TelegramChannel
        from agentwire.channels.sms import SMSChannel
        from agentwire.channels.webhook import WebhookChannel
        from agentwire.channels.discord import DiscordChannel
        from agentwire.channels.slack import SlackChannel

        assert EmailChannel.channel_type == "send_only"
        assert TelegramChannel.channel_type == "service"
        assert SMSChannel.channel_type == "send_only"
        assert WebhookChannel.channel_type == "send_only"
        assert DiscordChannel.channel_type == "service"
        assert SlackChannel.channel_type == "service"


# =============================================================================
# ChannelRegistry.resolve_config
# =============================================================================


class TestResolveConfig:
    def test_new_path_only(self):
        """channels.email: in YAML should be found."""
        data = {"channels": {"email": {"api_key": "new-key", "from_address": "a@b.com"}}}
        resolved = ChannelRegistry.resolve_config("email", data)
        assert resolved["api_key"] == "new-key"
        assert resolved["from_address"] == "a@b.com"

    def test_legacy_path_only(self):
        """notifications.email: (legacy) should be found for built-in."""
        data = {"notifications": {"email": {"api_key": "legacy-key"}}}
        resolved = ChannelRegistry.resolve_config("email", data)
        assert resolved["api_key"] == "legacy-key"

    def test_new_overrides_legacy(self):
        """New path takes precedence over legacy."""
        data = {
            "notifications": {"email": {"api_key": "legacy", "from_address": "old@a.com"}},
            "channels": {"email": {"api_key": "new"}},
        }
        resolved = ChannelRegistry.resolve_config("email", data)
        assert resolved["api_key"] == "new"
        # Legacy field preserved if not overridden
        assert resolved["from_address"] == "old@a.com"

    def test_telegram_legacy_path(self):
        """telegram: top-level (legacy) should resolve."""
        data = {"telegram": {"bot_token": "tok123", "allowed_users": [111]}}
        resolved = ChannelRegistry.resolve_config("telegram", data)
        assert resolved["bot_token"] == "tok123"
        assert resolved["allowed_users"] == [111]

    def test_empty_data(self):
        resolved = ChannelRegistry.resolve_config("email", {})
        assert resolved == {}

    def test_nonexistent_channel(self):
        resolved = ChannelRegistry.resolve_config("nonexistent", {"channels": {"email": {}}})
        assert resolved == {}

    def test_custom_channel_no_legacy(self):
        """Custom channels should NOT get legacy_config_key access."""
        @ChannelRegistry.register("_custom_test")
        class _CustomChannel(SendOnlyChannel):
            name = "_custom_test"
            config_key = "_custom_test"
            legacy_config_key = "steal.from.email"  # Should be ignored!

        data = {"steal": {"from": {"email": {"api_key": "stolen"}}}}
        resolved = ChannelRegistry.resolve_config("_custom_test", data)
        assert resolved == {}  # Legacy path blocked for non-builtins

        # Clean up
        del ChannelRegistry._channels["_custom_test"]


# =============================================================================
# Config loading integration
# =============================================================================


class TestConfigChannelLoading:
    def test_channels_loaded_from_yaml(self, tmp_path):
        """Config should load channel configs from channels: key."""
        config_data = {
            "server": {"port": 8765},
            "channels": {
                "email": {
                    "api_key": "test-key",
                    "from_address": "test@test.com",
                    "default_to": "user@test.com",
                },
            },
        }
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.safe_dump(config_data, f)

        from agentwire.config import load_config
        config = load_config(config_path)

        assert "email" in config.channels
        email_config = config.channels["email"]
        assert email_config.api_key == "test-key"
        assert email_config.from_address == "test@test.com"
        assert email_config.default_to == "user@test.com"

    def test_legacy_email_config_loaded(self, tmp_path):
        """Old notifications.email: path should still work."""
        config_data = {
            "notifications": {
                "email": {
                    "api_key": "legacy-key",
                    "from_address": "legacy@test.com",
                },
            },
        }
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.safe_dump(config_data, f)

        from agentwire.config import load_config
        config = load_config(config_path)

        assert "email" in config.channels
        assert config.channels["email"].api_key == "legacy-key"

    def test_legacy_telegram_config_loaded(self, tmp_path):
        """Old telegram: top-level path should still work."""
        config_data = {
            "telegram": {
                "bot_token": "tok-legacy",
                "allowed_users": [123],
                "default_session": "main",
            },
        }
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.safe_dump(config_data, f)

        from agentwire.config import load_config
        config = load_config(config_path)

        assert "telegram" in config.channels
        assert config.channels["telegram"].bot_token == "tok-legacy"
        assert config.channels["telegram"].allowed_users == [123]

    def test_all_six_channels_in_config(self, tmp_path):
        """Even with no YAML config, all 6 channels should get default configs."""
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.safe_dump({}, f)

        from agentwire.config import load_config
        config = load_config(config_path)

        for name in ["email", "telegram", "quo", "sms", "webhook", "discord", "slack"]:
            assert name in config.channels, f"Channel {name} missing from config.channels"

    def test_channels_field_is_dict(self, tmp_path):
        """config.channels should be a dict, not a dataclass."""
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.safe_dump({}, f)

        from agentwire.config import load_config
        config = load_config(config_path)
        assert isinstance(config.channels, dict)

    def test_new_path_overrides_legacy(self, tmp_path):
        """channels.email: should override notifications.email:"""
        config_data = {
            "notifications": {"email": {"api_key": "old", "from_address": "old@x.com"}},
            "channels": {"email": {"api_key": "new"}},
        }
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.safe_dump(config_data, f)

        from agentwire.config import load_config
        config = load_config(config_path)

        assert config.channels["email"].api_key == "new"
        assert config.channels["email"].from_address == "old@x.com"


# =============================================================================
# Email channel
# =============================================================================


class TestEmailChannel:
    def test_email_config_env_fallback(self, monkeypatch):
        """EmailConfig should fall back to RESEND_API_KEY env var."""
        monkeypatch.setenv("RESEND_API_KEY", "env-key-123")
        from agentwire.channels.email import EmailConfig
        config = EmailConfig()
        assert config.api_key == "env-key-123"

    def test_email_config_explicit_overrides_env(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "env-key")
        from agentwire.channels.email import EmailConfig
        config = EmailConfig(api_key="explicit-key")
        assert config.api_key == "explicit-key"

    def test_email_channel_class_attributes(self):
        from agentwire.channels.email import EmailChannel
        assert EmailChannel.name == "email"
        assert EmailChannel.channel_type == "send_only"
        assert EmailChannel.config_key == "email"
        assert EmailChannel.legacy_config_key == "notifications.email"

    def test_is_html_content(self):
        from agentwire.channels.email import _is_html_content
        assert _is_html_content("<h1>Hello</h1>") is True
        assert _is_html_content("<div style='color:red'>") is True
        assert _is_html_content("<!DOCTYPE html>") is True
        assert _is_html_content("Just plain text") is False
        assert _is_html_content("") is False

    def test_markdown_to_html(self):
        from agentwire.channels.email import _markdown_to_html
        result = _markdown_to_html("**bold**")
        assert "<strong>bold</strong>" in result

    def test_markdown_passthrough_html(self):
        from agentwire.channels.email import _markdown_to_html
        html = "<h1>Already HTML</h1>"
        assert _markdown_to_html(html) == html

    def test_send_email_no_api_key(self, tmp_path, monkeypatch):
        """send_email should raise EmailConfigError if no API key."""
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        config_data = {"channels": {"email": {"api_key": "", "from_address": "x@y.com"}}}
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.safe_dump(config_data, f)

        from agentwire.config import load_config
        import agentwire.config as config_mod
        old = config_mod._config
        config_mod._config = load_config(config_path)

        from agentwire.channels.email import EmailConfigError, send_email
        try:
            with pytest.raises(EmailConfigError, match="API key"):
                send_email(body="test")
        finally:
            config_mod._config = old

    def test_greetings_list(self):
        from agentwire.channels.email import GREETINGS
        assert len(GREETINGS) == 8
        assert all(isinstance(g, str) for g in GREETINGS)

    def test_attachment_dataclass(self):
        from agentwire.channels.email import Attachment
        a = Attachment(filename="test.pdf", content=b"bytes")
        assert a.filename == "test.pdf"
        assert a.content == b"bytes"
        assert a.content_type is None

    def test_email_result_dataclass(self):
        from agentwire.channels.email import EmailResult
        r = EmailResult(success=True, message_id="msg-1")
        assert r.success is True
        assert r.message_id == "msg-1"


# =============================================================================
# Telegram channel
# =============================================================================


class TestTelegramChannel:
    def test_telegram_config_env_fallback(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_AGENTWIRE_BOT_TOKEN", "env-token")
        from agentwire.channels.telegram import TelegramConfig
        config = TelegramConfig()
        assert config.bot_token == "env-token"

    def test_telegram_channel_class_attributes(self):
        from agentwire.channels.telegram import TelegramChannel
        assert TelegramChannel.name == "telegram"
        assert TelegramChannel.channel_type == "service"
        assert TelegramChannel.config_key == "telegram"
        assert TelegramChannel.legacy_config_key == "telegram"

    def test_telegram_config_defaults(self):
        from agentwire.channels.telegram import TelegramConfig
        # Don't rely on env state — test explicit defaults
        config = TelegramConfig(bot_token="tok")
        assert config.default_session == "main"
        assert config.voice_replies is True
        assert config.forward_questions is True
        assert config.forward_alerts is True
        assert config.session_name == "agentwire-telegram"
        assert config.allowed_users == []

    def test_telegram_result_dataclass(self):
        from agentwire.channels.telegram import TelegramResult
        r = TelegramResult(success=True, message_id=999)
        assert r.success is True
        assert r.message_id == 999

    def test_get_telegram_config_no_token(self, monkeypatch, tmp_path):
        """Should raise TelegramConfigError if no token anywhere."""
        monkeypatch.delenv("TELEGRAM_AGENTWIRE_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_USER_ID", raising=False)
        # Mock load_dotenv to prevent it from loading real .env files
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: None)
        # Point HOME to tmp_path so config.yaml isn't found
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        from agentwire.channels.telegram import TelegramConfigError, _get_telegram_config
        with pytest.raises(TelegramConfigError, match="No Telegram bot token"):
            _get_telegram_config()


# =============================================================================
# Quo channel
# =============================================================================


class TestQuoChannel:
    def test_quo_config_env_fallback(self, monkeypatch):
        monkeypatch.setenv("QUO_API_KEY", "quo-key-123")
        from agentwire.channels.quo import QuoConfig
        config = QuoConfig()
        assert config.api_key == "quo-key-123"

    def test_quo_config_openphone_env_fallback(self, monkeypatch, tmp_path):
        # Patch HOME to prevent load_dotenv from reading real ~/.agentwire/.env
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("QUO_API_KEY", raising=False)
        monkeypatch.setenv("OPENPHONE_API_KEY", "op-key-456")
        from agentwire.channels.quo import QuoConfig
        config = QuoConfig(api_key="")  # Force empty to test env fallback
        assert config.api_key == "op-key-456"

    def test_quo_channel_class_attributes(self):
        from agentwire.channels.quo import QuoChannel
        assert QuoChannel.name == "quo"
        assert QuoChannel.channel_type == "send_only"
        assert QuoChannel.config_key == "quo"

    def test_quo_config_defaults(self):
        from agentwire.channels.quo import QuoConfig
        config = QuoConfig(api_key="k")
        assert config.from_number == ""
        assert config.default_to == ""

    def test_send_quo_no_api_key(self, tmp_path, monkeypatch):
        """Should raise QuoConfigError if no API key."""
        monkeypatch.delenv("QUO_API_KEY", raising=False)
        monkeypatch.delenv("OPENPHONE_API_KEY", raising=False)
        config_data = {"channels": {"quo": {"api_key": ""}}}
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.safe_dump(config_data, f)

        from agentwire.config import load_config
        import agentwire.config as config_mod
        old = config_mod._config
        config_mod._config = load_config(config_path)

        from agentwire.channels.quo import QuoConfigError, send_quo_sms
        try:
            with pytest.raises(QuoConfigError):
                send_quo_sms(body="test")
        finally:
            config_mod._config = old


# =============================================================================
# SMS channel
# =============================================================================


class TestSMSChannel:
    def test_sms_config_env_fallback(self, monkeypatch):
        monkeypatch.setenv("TWILIO_ACCOUNT_SID", "sid-env")
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok-env")
        from agentwire.channels.sms import SMSConfig
        config = SMSConfig()
        assert config.account_sid == "sid-env"
        assert config.auth_token == "tok-env"

    def test_sms_channel_class_attributes(self):
        from agentwire.channels.sms import SMSChannel
        assert SMSChannel.name == "sms"
        assert SMSChannel.channel_type == "send_only"
        assert SMSChannel.config_key == "sms"

    def test_sms_no_twilio_installed(self):
        """send_sms should return error if twilio not installed."""
        from agentwire.channels.sms import send_sms
        # twilio is likely not installed in test env — this tests the fallback
        try:
            import twilio
            pytest.skip("twilio is installed, can't test missing dep path")
        except ImportError:
            pass

        result = send_sms(body="test", to="+1234567890")
        assert result.success is False
        assert "not installed" in result.error.lower() or "twilio" in result.error.lower()


# =============================================================================
# Webhook channel
# =============================================================================


class TestWebhookChannel:
    def test_webhook_config_defaults(self):
        from agentwire.channels.webhook import WebhookConfig
        config = WebhookConfig()
        assert config.url == ""
        assert config.method == "POST"
        assert config.headers == {}
        assert config.content_type == "application/json"

    def test_webhook_channel_class_attributes(self):
        from agentwire.channels.webhook import WebhookChannel
        assert WebhookChannel.name == "webhook"
        assert WebhookChannel.channel_type == "send_only"
        assert WebhookChannel.config_key == "webhook"

    def test_send_webhook_no_url(self, tmp_path, monkeypatch):
        """Should raise WebhookConfigError if no URL."""
        config_data = {"channels": {"webhook": {"url": ""}}}
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.safe_dump(config_data, f)

        from agentwire.config import load_config
        import agentwire.config as config_mod
        config_mod._config_cache = None
        config = load_config(config_path)
        config_mod._config_cache = config

        from agentwire.channels.webhook import WebhookConfigError, send_webhook
        with pytest.raises(WebhookConfigError, match="No URL"):
            send_webhook(text="test")

        config_mod._config_cache = None


# =============================================================================
# Discord channel
# =============================================================================


class TestDiscordChannel:
    def test_discord_config_env_fallback(self, monkeypatch):
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "disc-tok")
        from agentwire.channels.discord import DiscordConfig
        config = DiscordConfig()
        assert config.bot_token == "disc-tok"

    def test_discord_config_defaults(self):
        from agentwire.channels.discord import DiscordConfig
        config = DiscordConfig(bot_token="tok")
        assert config.default_session == "agentwire"
        assert config.voice_replies is True
        assert config.session_name == "agentwire-discord"
        assert config.allowed_user_ids == []

    def test_discord_channel_class_attributes(self):
        from agentwire.channels.discord import DiscordChannel
        assert DiscordChannel.name == "discord"
        assert DiscordChannel.channel_type == "service"
        assert DiscordChannel.config_key == "discord"


# =============================================================================
# Slack channel
# =============================================================================


class TestSlackChannel:
    def test_slack_config_env_fallback(self, monkeypatch):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
        monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
        from agentwire.channels.slack import SlackConfig
        config = SlackConfig()
        assert config.bot_token == "xoxb-test"
        assert config.app_token == "xapp-test"

    def test_slack_config_defaults(self):
        from agentwire.channels.slack import SlackConfig
        config = SlackConfig(bot_token="tok", app_token="app")
        assert config.default_session == "agentwire"
        assert config.voice_replies is True
        assert config.session_name == "agentwire-slack"
        assert config.allowed_user_ids == []

    def test_slack_channel_class_attributes(self):
        from agentwire.channels.slack import SlackChannel
        assert SlackChannel.name == "slack"
        assert SlackChannel.channel_type == "service"
        assert SlackChannel.config_key == "slack"


# =============================================================================
# Base class
# =============================================================================


class TestBaseChannel:
    def test_channel_init(self):
        ch = Channel(config={"key": "val"})
        assert ch.config == {"key": "val"}

    def test_channel_init_no_config(self):
        ch = Channel()
        assert ch.config is None

    def test_service_channel_not_implemented(self):
        ch = ServiceChannel()
        with pytest.raises(NotImplementedError):
            import asyncio
            asyncio.get_event_loop().run_until_complete(ch.start())

    def test_send_only_channel_not_implemented(self):
        ch = SendOnlyChannel()
        with pytest.raises(NotImplementedError):
            import asyncio
            asyncio.get_event_loop().run_until_complete(ch.send("test"))

    def test_service_channel_default_max_message_length(self):
        ch = ServiceChannel()
        assert ch.max_message_length == 2000

    def test_truncate_output_short_text(self):
        ch = ServiceChannel()
        assert ch.truncate_output("hello") == "hello"

    def test_truncate_output_long_text(self):
        ch = ServiceChannel()
        long_text = "x" * 3000
        result = ch.truncate_output(long_text)
        assert len(result) == 2000
        # Should keep the tail
        assert result == long_text[-2000:]

    def test_truncate_output_exact_length(self):
        ch = ServiceChannel()
        exact = "x" * 2000
        assert ch.truncate_output(exact) == exact

    def test_discord_max_message_length(self):
        from agentwire.channels.discord import DiscordChannel
        assert DiscordChannel.max_message_length == 1800

    def test_slack_max_message_length(self):
        from agentwire.channels.slack import SlackChannel
        assert SlackChannel.max_message_length == 2800

    def test_discord_truncate_uses_own_limit(self):
        from agentwire.channels.discord import DiscordChannel
        ch = DiscordChannel()
        result = ch.truncate_output("x" * 3000)
        assert len(result) == 1800

    def test_slack_truncate_uses_own_limit(self):
        from agentwire.channels.slack import SlackChannel
        ch = SlackChannel()
        result = ch.truncate_output("x" * 5000)
        assert len(result) == 2800


class TestSharedSessionHelpers:
    """Tests for shared session_exists, ensure_session, wait_for_session_ready."""

    def test_session_exists_found(self):
        from agentwire.channels.base import session_exists
        with patch("agentwire.channels.base._run_cmd") as mock:
            mock.return_value = {"success": True}
            assert session_exists("test-session") is True
            mock.assert_called_once_with(["info", "-s", "test-session"])

    def test_session_exists_not_found(self):
        from agentwire.channels.base import session_exists
        with patch("agentwire.channels.base._run_cmd") as mock:
            mock.return_value = {"success": False}
            assert session_exists("missing") is False

    def test_ensure_session_already_exists(self):
        from agentwire.channels.base import ensure_session
        with patch("agentwire.channels.base.session_exists", return_value=True):
            assert ensure_session("existing") is True

    def test_ensure_session_creates_new(self):
        from agentwire.channels.base import ensure_session
        with patch("agentwire.channels.base.session_exists", return_value=False):
            with patch("agentwire.channels.base._run_cmd") as mock:
                mock.return_value = {"success": True}
                assert ensure_session("new-session", "/path/to/project") is True
                mock.assert_called_once()
                args = mock.call_args[0][0]
                assert "new" in args
                assert "-s" in args
                assert "new-session" in args
                assert "-p" in args

    def test_queued_message_dataclass(self):
        from agentwire.channels.base import QueuedMessage
        msg = QueuedMessage(
            platform_msg={"ts": "123"},
            text="hello",
            session="test",
            project="/tmp",
            prefix="[Test: 'hello']",
        )
        assert msg.text == "hello"
        assert msg.session == "test"
        assert msg.platform_msg == {"ts": "123"}


# =============================================================================
# Shared CLI runners
# =============================================================================


class TestCLIRunners:
    def test_run_cmd_success(self):
        """_run_cmd with a working command."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout='{"success": true, "data": "test"}',
                stderr="",
            )
            result = _run_cmd(["list"])
            assert result["success"] is True
            assert result["data"] == "test"
            # Verify --json was appended
            call_args = mock_run.call_args[0][0]
            assert "--json" in call_args

    def test_run_cmd_timeout(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 30)):
            result = _run_cmd(["list"])
            assert result["success"] is False
            assert "timed out" in result["error"].lower()

    def test_run_cmd_raw(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="raw output here")
            result = _run_cmd_raw(["output", "-s", "main"])
            assert result == "raw output here"


# =============================================================================
# Notifications.py deleted — import should fail
# =============================================================================


class TestNotificationsDeleted:
    def test_old_import_fails(self):
        """from agentwire.notifications import ... should fail."""
        with pytest.raises(ModuleNotFoundError):
            from agentwire.notifications import send_email  # noqa: F401

    def test_old_import_check_telegram_fails(self):
        with pytest.raises(ModuleNotFoundError):
            from agentwire.notifications import check_telegram_bot  # noqa: F401


# =============================================================================
# Import rewiring — new paths work
# =============================================================================


class TestImportRewiring:
    def test_email_imports_from_channels(self):
        from agentwire.channels.email import send_email, cmd_email, EmailConfigError
        assert callable(send_email)
        assert callable(cmd_email)

    def test_telegram_imports_from_channels(self):
        from agentwire.channels.telegram import (
            send_telegram,
            check_telegram_bot,
            TelegramConfigError,
        )
        assert callable(send_telegram)
        assert callable(check_telegram_bot)

    def test_registry_from_init(self):
        from agentwire.channels import ChannelRegistry, Channel, SendOnlyChannel, ServiceChannel
        assert ChannelRegistry is not None
        assert Channel is not None

    def test_notification_error_from_channels(self):
        from agentwire.channels import NotificationError
        assert issubclass(NotificationError, Exception)


# =============================================================================
# Channel config classes — all have expected fields
# =============================================================================


class TestConfigDataclasses:
    def test_email_config_fields(self):
        from agentwire.channels.email import EmailConfig
        c = EmailConfig(
            api_key="k", from_address="a@b.com", default_to="c@d.com",
            banner_image_url="b", echo_image_url="e", echo_small_url="s",
            logo_image_url="l",
        )
        assert c.api_key == "k"
        assert c.from_address == "a@b.com"
        assert c.banner_image_url == "b"

    def test_telegram_config_fields(self):
        from agentwire.channels.telegram import TelegramConfig
        c = TelegramConfig(
            bot_token="t", allowed_users=[1, 2], default_session="dev",
            voice_replies=False, forward_questions=False, forward_alerts=False,
            session_name="my-tg",
        )
        assert c.bot_token == "t"
        assert c.allowed_users == [1, 2]
        assert c.voice_replies is False
        assert c.session_name == "my-tg"

    def test_quo_config_fields(self):
        from agentwire.channels.quo import QuoConfig
        c = QuoConfig(api_key="k", from_number="+1111", default_to="+2222")
        assert c.api_key == "k"
        assert c.from_number == "+1111"
        assert c.default_to == "+2222"

    def test_sms_config_fields(self):
        from agentwire.channels.sms import SMSConfig
        c = SMSConfig(
            account_sid="sid", auth_token="tok",
            from_number="+1111", default_to="+2222",
        )
        assert c.account_sid == "sid"
        assert c.from_number == "+1111"

    def test_webhook_config_fields(self):
        from agentwire.channels.webhook import WebhookConfig
        c = WebhookConfig(
            url="https://example.com", method="PUT",
            headers={"X-Key": "val"}, content_type="text/plain",
        )
        assert c.url == "https://example.com"
        assert c.method == "PUT"
        assert c.headers == {"X-Key": "val"}

    def test_discord_config_fields(self):
        from agentwire.channels.discord import DiscordConfig
        c = DiscordConfig(
            bot_token="d", allowed_user_ids=[100, 200],
            default_session="dev", session_name="my-dc",
        )
        assert c.bot_token == "d"
        assert c.allowed_user_ids == [100, 200]

    def test_slack_config_fields(self):
        from agentwire.channels.slack import SlackConfig
        c = SlackConfig(
            bot_token="xoxb", app_token="xapp",
            allowed_user_ids=["U123", "U456"],
            default_session="dev", session_name="my-sl",
        )
        assert c.bot_token == "xoxb"
        assert c.app_token == "xapp"
        assert c.allowed_user_ids == ["U123", "U456"]


# =============================================================================
# Security: legacy_config_key restriction
# =============================================================================


class TestLegacyConfigSecurity:
    def test_builtin_gets_legacy(self):
        """Built-in channel with legacy_config_key should resolve legacy path."""
        data = {"notifications": {"email": {"api_key": "from-legacy"}}}
        resolved = ChannelRegistry.resolve_config("email", data)
        assert resolved.get("api_key") == "from-legacy"

    def test_non_builtin_blocked(self):
        """Non-builtin channel should NOT resolve legacy_config_key."""
        @ChannelRegistry.register("_sec_test")
        class _SecTest(SendOnlyChannel):
            name = "_sec_test"
            config_key = "_sec_test"
            legacy_config_key = "notifications.email"

        # Even though legacy_config_key points to a real path, it should be blocked
        data = {"notifications": {"email": {"api_key": "stolen!"}}}
        resolved = ChannelRegistry.resolve_config("_sec_test", data)
        assert "api_key" not in resolved

        del ChannelRegistry._channels["_sec_test"]

    def test_builtin_no_legacy_key(self):
        """Built-in channel without legacy_config_key should work fine."""
        # webhook has no legacy_config_key
        from agentwire.channels.webhook import WebhookChannel
        assert WebhookChannel.legacy_config_key is None

        data = {"channels": {"webhook": {"url": "https://test.com"}}}
        resolved = ChannelRegistry.resolve_config("webhook", data)
        assert resolved.get("url") == "https://test.com"
