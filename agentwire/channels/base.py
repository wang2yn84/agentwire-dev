"""Channel base classes, registry, and primitives for AgentWire.

Three-layer architecture:
- Primitives (TTS/STT) — infrastructure channels consume via base class
- ServiceChannel — bidirectional, long-lived (Telegram, Discord, Slack)
- SendOnlyChannel — stateless outbound (email, SMS, webhook)
"""

import json
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass


class NotificationError(Exception):
    """Base exception for channel/notification errors."""

    pass


@dataclass
class ChannelResult:
    """Result of a channel send operation."""

    success: bool
    message_id: str | int | None = None
    error: str | None = None


class ChannelRegistry:
    """Registry for channel classes with security constraints.

    Built-in channels can use legacy_config_key to read from old config paths.
    Custom channels are restricted to channels.{their_name}: in YAML.
    """

    _channels: dict[str, type] = {}
    BUILTIN_CHANNELS = {"email", "telegram", "quo", "sms", "webhook", "discord", "slack"}

    @classmethod
    def register(cls, name: str):
        """Decorator to register a channel class."""

        def decorator(channel_cls):
            cls._channels[name] = channel_cls
            return channel_cls

        return decorator

    @classmethod
    def get(cls, name: str):
        """Get a registered channel class by name."""
        return cls._channels.get(name)

    @classmethod
    def all(cls) -> dict[str, type]:
        """Return all registered channels."""
        return dict(cls._channels)

    @classmethod
    def resolve_config(cls, name: str, data: dict) -> dict:
        """Resolve config for a channel from YAML data dict.

        Checks channels.{config_key} first (new path), then legacy_config_key
        if the channel is a built-in. Returns merged dict: {**legacy, **new}.
        """
        channel_cls = cls._channels.get(name)
        if not channel_cls:
            return {}

        config_key = getattr(channel_cls, "config_key", name)
        legacy_key = getattr(channel_cls, "legacy_config_key", None)

        # New path: channels.{config_key}
        new_config = data.get("channels", {}).get(config_key, {})

        # Legacy path: only for built-in channels
        legacy_config = {}
        if legacy_key and name in cls.BUILTIN_CHANNELS:
            # Support dotted keys like "notifications.email"
            parts = legacy_key.split(".")
            node = data
            for part in parts:
                if isinstance(node, dict):
                    node = node.get(part, {})
                else:
                    node = {}
                    break
            if isinstance(node, dict):
                legacy_config = node

        # Merge: new takes precedence over legacy
        if legacy_config or new_config:
            return {**legacy_config, **new_config}
        return {}


# === Shared CLI runners ===


def _run_cmd(args: list[str]) -> dict:
    """Run agentwire CLI command, return parsed JSON dict."""
    try:
        result = subprocess.run(
            ["agentwire"] + args + ["--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.stdout.strip():
            return json.loads(result.stdout)
        return {"success": False, "error": result.stderr.strip() or "No output"}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Command timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _run_cmd_no_json(args: list[str]) -> dict:
    """Run agentwire CLI command WITHOUT --json flag. Returns success/error dict."""
    try:
        result = subprocess.run(
            ["agentwire"] + args,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return {"success": True, "output": result.stdout.strip()}
        return {"success": False, "error": result.stderr.strip() or result.stdout.strip() or "Command failed"}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Command timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _run_cmd_raw(args: list[str]) -> str:
    """Run agentwire CLI command, return raw stdout."""
    try:
        result = subprocess.run(
            ["agentwire"] + args,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout
    except Exception as e:
        return f"Error: {e}"


# === Base Channel Classes ===


class Channel:
    """Base class for all channels. Provides access to primitives and session helpers."""

    name: str = ""
    channel_type: str = ""
    config_class = None
    config_key: str = ""
    legacy_config_key: str | None = None  # BUILT-IN ONLY

    def __init__(self, config=None):
        self.config = config

    # --- Primitives (TTS/STT as infrastructure) ---

    async def tts(self, text: str, voice: str | None = None) -> bytes:
        """Generate audio from text via TTS server. Returns WAV bytes."""
        from agentwire.config import get_config

        config = get_config()
        tts_url = getattr(config.tts, "url", "http://localhost:8100")
        if not tts_url:
            tts_url = "http://localhost:8100"

        payload = {"text": text}
        if voice:
            payload["voice"] = voice

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{tts_url}/tts",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except Exception as e:
            raise NotificationError(f"TTS failed: {e}") from e

    async def stt(self, audio: bytes, format: str = "wav") -> str:
        """Transcribe audio to text via STT server."""
        from agentwire.config import get_config

        config = get_config()
        stt_url = getattr(config.stt, "url", "http://localhost:8101")
        if not stt_url:
            stt_url = "http://localhost:8101"

        boundary = "----ChannelSTTBoundary"
        filename = f"audio.{format}"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: audio/{format}\r\n\r\n"
        ).encode("utf-8") + audio + f"\r\n--{boundary}--\r\n".encode("utf-8")

        req = urllib.request.Request(
            f"{stt_url}/transcribe",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode())
                return result.get("text", "")
        except Exception as e:
            raise NotificationError(f"STT failed: {e}") from e

    def voices_available(self) -> list[str]:
        """List available TTS voices."""
        from agentwire.config import get_config

        config = get_config()
        tts_url = getattr(config.tts, "url", "http://localhost:8100")
        if not tts_url:
            tts_url = "http://localhost:8100"

        try:
            req = urllib.request.Request(f"{tts_url}/voices")
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
                return [v["name"] for v in result.get("voices", [])]
        except Exception:
            return []

    # --- Session interaction helpers ---

    def send_to_session(self, session: str, text: str) -> dict:
        """Route a message to a session. Wraps `agentwire send`."""
        return _run_cmd(["send", "-s", session, text])

    def get_session_output(self, session: str, lines: int = 20) -> str:
        """Read recent output from a session. Wraps `agentwire output`."""
        return _run_cmd_raw(["output", "-s", session, "-n", str(lines)])

    def list_sessions(self) -> list[dict]:
        """List active sessions. Wraps `agentwire list --json`."""
        result = _run_cmd(["list"])
        if isinstance(result, dict) and "sessions" in result:
            return result["sessions"]
        return []


class ServiceChannel(Channel):
    """Bidirectional channel with long-lived service process.

    Service channels run in their own tmux session and handle both
    inbound (platform → session) and outbound (session → platform).
    """

    channel_type = "service"

    async def start(self) -> None:
        """Start the service channel."""
        raise NotImplementedError

    async def stop(self) -> None:
        """Stop the service channel."""
        raise NotImplementedError

    async def status(self) -> dict:
        """Check service channel status."""
        raise NotImplementedError


class SendOnlyChannel(Channel):
    """Stateless outbound-only channel.

    No service process — just a send() method.
    """

    channel_type = "send_only"

    async def send(self, text: str, **kwargs) -> ChannelResult:
        """Send a message through this channel."""
        raise NotImplementedError
