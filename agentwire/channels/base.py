"""Channel base classes, registry, and primitives for AgentWire.

Three-layer architecture:
- Primitives (TTS/STT) — infrastructure channels consume via base class
- ServiceChannel — bidirectional, long-lived (Telegram, Discord, Slack)
- SendOnlyChannel — stateless outbound (email, SMS, webhook)
"""

import asyncio
import json
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


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


# === Shared session helpers ===
# Used by service channels to check/create sessions before routing messages.


def session_exists(session: str) -> bool:
    """Check if an agentwire session is running."""
    result = _run_cmd(["info", "-s", session])
    return result.get("success", False)


def ensure_session(session: str, project: str = "") -> bool:
    """Ensure a session exists, creating it if needed. Returns True if ready."""
    if session_exists(session):
        return True

    args = ["new", "-s", session]
    if project:
        expanded = str(Path(project).expanduser())
        args.extend(["-p", expanded])

    result = _run_cmd(args)
    return result.get("success", False)


def wait_for_session_ready(session: str, timeout: int = 30) -> bool:
    """Wait for a session's agent to be ready (prompt loaded, trust accepted).

    Uses agentwire's core _wait_for_agent_ready which handles Claude Code's
    first-time folder trust prompt and polls for the agent prompt indicator.
    Falls back to a simple delay if the import fails.
    """
    try:
        from agentwire.__main__ import _wait_for_agent_ready
        return _wait_for_agent_ready(session, timeout=timeout)
    except ImportError:
        time.sleep(8)
        return True


# === Message queue for service channels ===


@dataclass
class QueuedMessage:
    """A message waiting to be processed by a service channel.

    Platform-specific message objects are stored as `platform_msg` so the
    queue manager can pass them to reaction callbacks without knowing the type.
    """

    platform_msg: Any  # discord.Message, Slack event dict, etc.
    text: str
    session: str
    project: str  # Empty string if no project needed
    prefix: str  # Pre-formatted prefix string (e.g., "[Discord DM from User: 'text']")


class MessageQueueManager:
    """Per-session message queue with async workers.

    Each session gets its own queue and worker task. Messages are processed
    in order, one at a time per session. Different sessions run concurrently.

    Platform-specific behavior (emoji reactions, error replies) is injected
    via callbacks so the queue logic stays reusable across Discord, Slack, etc.
    """

    def __init__(
        self,
        channel_name: str,
        on_queued: Callable | None = None,
        on_starting: Callable | None = None,
        on_sent: Callable | None = None,
        on_error: Callable | None = None,
        worker_idle_timeout: int = 300,
    ):
        self.channel_name = channel_name
        self._queues: dict[str, asyncio.Queue] = {}
        self._workers: dict[str, asyncio.Task] = {}
        # Reaction callbacks — called with (platform_msg,). Must be async.
        self._on_queued = on_queued
        self._on_starting = on_starting
        self._on_sent = on_sent
        self._on_error = on_error
        self._worker_idle_timeout = worker_idle_timeout

    async def enqueue(self, msg: QueuedMessage):
        """Add a message to the session's queue. Starts worker if needed."""
        session = msg.session
        print(f"[{self.channel_name}] Enqueuing message for session '{session}': {msg.text[:50]}")

        if self._on_queued:
            try:
                await self._on_queued(msg.platform_msg)
            except Exception as e:
                print(f"[{self.channel_name}] Failed to mark queued: {e}")

        if session not in self._queues:
            print(f"[{self.channel_name}] Starting new queue worker for session '{session}'")
            self._queues[session] = asyncio.Queue()
            self._workers[session] = asyncio.create_task(self._worker(session))

        await self._queues[session].put(msg)

    async def _worker(self, session: str):
        """Process messages for a session, one at a time, in order."""
        queue = self._queues[session]

        while True:
            try:
                msg: QueuedMessage = await asyncio.wait_for(
                    queue.get(), timeout=self._worker_idle_timeout
                )
            except asyncio.TimeoutError:
                # No messages within timeout — shut down worker
                del self._queues[session]
                del self._workers[session]
                return

            try:
                await self._process_message(msg)
            except Exception as e:
                print(f"[{self.channel_name}] Queue error for {msg.session}: {e}")
                import traceback
                traceback.print_exc()
                if self._on_error:
                    try:
                        await self._on_error(msg.platform_msg)
                    except Exception:
                        pass

            queue.task_done()

    async def _process_message(self, msg: QueuedMessage):
        """Process a single queued message: ensure session, wait for ready, send."""
        loop = asyncio.get_event_loop()
        print(f"[{self.channel_name}] Processing message for session '{msg.session}'")

        # Check if session exists, create if needed
        exists = await loop.run_in_executor(None, session_exists, msg.session)
        if not exists:
            print(f"[{self.channel_name}] Session '{msg.session}' not found, creating...")
            if self._on_starting:
                try:
                    await self._on_starting(msg.platform_msg)
                except Exception:
                    pass

            created = await loop.run_in_executor(None, ensure_session, msg.session, msg.project)
            if not created:
                print(f"[{self.channel_name}] Failed to create session '{msg.session}'")
                if self._on_error:
                    try:
                        await self._on_error(msg.platform_msg)
                    except Exception:
                        pass
                return

        # Wait for agent to finish loading if we just created the session
        if not exists:
            print(f"[{self.channel_name}] Waiting for session '{msg.session}' to load...")
            ready = await loop.run_in_executor(None, wait_for_session_ready, msg.session, 30)
            if ready:
                print(f"[{self.channel_name}] Session '{msg.session}' ready")
            else:
                print(f"[{self.channel_name}] Session '{msg.session}' may not be fully loaded, sending anyway")

        # Send to session (send doesn't support --json)
        print(f"[{self.channel_name}] Sending to session '{msg.session}': {msg.text[:50]}")
        result = await loop.run_in_executor(None, _run_cmd_no_json, ["send", "-s", msg.session, msg.prefix])
        print(f"[{self.channel_name}] Send result: {result}")

        if result.get("success", False):
            if self._on_sent:
                try:
                    await self._on_sent(msg.platform_msg)
                except Exception:
                    pass
        else:
            if self._on_error:
                try:
                    await self._on_error(msg.platform_msg)
                except Exception:
                    pass


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

    async def voices_available(self) -> list[str]:
        """List available TTS voices."""
        from agentwire.config import get_config

        config = get_config()
        tts_url = getattr(config.tts, "url", "http://localhost:8100") or "http://localhost:8100"

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

    Subclasses should set max_message_length to their platform's limit.
    """

    channel_type = "service"
    max_message_length: int = 2000  # Override per-platform (Discord=1800, Slack=2800, etc.)

    def truncate_output(self, text: str) -> str:
        """Truncate text to platform's max message length, keeping the tail."""
        limit = self.max_message_length
        if len(text) <= limit:
            return text
        return text[-limit:]

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
