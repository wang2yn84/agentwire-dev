"""Telegram bridge for AgentWire.

Maps Telegram messages to agentwire CLI commands. Subscribes to portal
WebSocket for outbound events (questions, TTS audio, alerts).
"""

import asyncio
import io
import json
import logging
import os
import re
import subprocess
import ssl
from pathlib import Path

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

log = logging.getLogger(__name__)

router = Router()

# ANSI escape code pattern
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return ANSI_RE.sub("", text)


def _run_cmd(args: list[str]) -> dict:
    """Run agentwire CLI command synchronously and return parsed JSON."""
    cmd = ["agentwire", *args, "--json"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        # Try parsing JSON from stdout first (even on failure — CLI returns JSON errors)
        if result.stdout.strip():
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass
        if result.returncode != 0:
            return {"error": result.stderr.strip() or f"Exit code {result.returncode}"}
        return {}
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out"}
    except Exception as e:
        return {"error": str(e)}


async def run_cmd(args: list[str]) -> dict:
    """Run agentwire CLI command async."""
    return await asyncio.get_event_loop().run_in_executor(None, _run_cmd, args)


async def run_cmd_raw(args: list[str]) -> str:
    """Run agentwire CLI command and return raw stdout."""
    cmd = ["agentwire", *args]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode().strip()


class TelegramBridge:
    """Telegram bot that bridges messages to agentwire sessions."""

    def __init__(self, bot_token: str, allowed_users: list[int], config: dict):
        self.bot = Bot(token=bot_token)
        self.dp = Dispatcher()
        self.dp.include_router(router)
        self.allowed_users = set(allowed_users)
        self.config = config
        self.default_session = config.get("default_session", "main")
        self.voice_replies = config.get("voice_replies", True)
        self.forward_questions = config.get("forward_questions", True)
        self.forward_alerts = config.get("forward_alerts", True)

        # Per-user active session (persisted)
        self._state_file = Path.home() / ".agentwire" / "telegram-state.json"
        self.user_sessions: dict[int, str] = {}
        self.user_chats: dict[int, int] = {}
        self._load_state()
        # Track portal WS tasks
        self._ws_tasks: dict[str, asyncio.Task] = {}
        self._running = False

        # Register middleware and handlers
        self.dp.message.middleware(AuthMiddleware(self.allowed_users))
        self.dp.callback_query.middleware(AuthMiddleware(self.allowed_users))
        self._register_handlers()

    def _register_handlers(self):
        """Register message and command handlers."""
        # Store bridge reference on router for handlers
        router.bridge = self

        self.dp.message.register(handle_start, CommandStart())
        self.dp.message.register(handle_list, Command("list"))
        self.dp.message.register(handle_session, Command("s"))
        self.dp.message.register(handle_output, Command("output"))
        self.dp.message.register(handle_new, Command("new"))
        self.dp.message.register(handle_kill, Command("kill"))
        self.dp.message.register(handle_help, Command("help"))
        self.dp.message.register(handle_voice, F.voice)
        self.dp.message.register(handle_text, F.text)
        self.dp.callback_query.register(handle_callback)

    def _load_state(self):
        """Load persisted user sessions from disk."""
        try:
            if self._state_file.exists():
                data = json.loads(self._state_file.read_text())
                # Keys are strings in JSON, convert to int
                self.user_sessions = {int(k): v for k, v in data.get("sessions", {}).items()}
                self.user_chats = {int(k): v for k, v in data.get("chats", {}).items()}
        except Exception:
            pass

    def _save_state(self):
        """Persist user sessions to disk."""
        try:
            data = {"sessions": self.user_sessions, "chats": self.user_chats}
            self._state_file.write_text(json.dumps(data))
        except Exception:
            pass

    def get_session(self, user_id: int) -> str:
        """Get active session for user."""
        return self.user_sessions.get(user_id, self.default_session)

    def set_session(self, user_id: int, session: str):
        """Set active session for user."""
        self.user_sessions[user_id] = session
        self._save_state()

    def set_chat(self, user_id: int, chat_id: int):
        """Set chat ID for user and persist."""
        self.user_chats[user_id] = chat_id
        self._save_state()

    async def start(self):
        """Start the bot polling loop."""
        self._running = True
        log.info("Telegram bridge starting...")

        # Get bot info
        me = await self.bot.get_me()
        log.info(f"Bot: @{me.username} ({me.first_name})")

        # Start polling (runs until stopped)
        await self.dp.start_polling(self.bot)

    async def stop(self):
        """Stop the bot."""
        self._running = False
        # Cancel WS subscriptions
        for task in self._ws_tasks.values():
            task.cancel()
        self._ws_tasks.clear()
        await self.dp.stop_polling()
        await self.bot.session.close()

    async def subscribe_session(self, session: str, chat_id: int):
        """Subscribe to portal WebSocket for a session's events."""
        key = f"{session}:{chat_id}"
        if key in self._ws_tasks:
            return  # Already subscribed

        task = asyncio.create_task(self._ws_listener(session, chat_id))
        self._ws_tasks[key] = task

    async def unsubscribe_session(self, session: str, chat_id: int):
        """Unsubscribe from a session's events."""
        key = f"{session}:{chat_id}"
        if task := self._ws_tasks.pop(key, None):
            task.cancel()

    async def _ws_listener(self, session: str, chat_id: int):
        """Listen to portal WebSocket for session events."""
        portal_url = self.config.get("portal_url", "wss://localhost:8765")
        ws_url = f"{portal_url}/ws/{session}"

        # Trust self-signed certs for localhost
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        last_question = None

        while self._running:
            try:
                async with aiohttp.ClientSession() as http:
                    async with http.ws_connect(ws_url, ssl=ssl_ctx) as ws:
                        log.info(f"Connected to portal WS for session '{session}'")
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    data = json.loads(msg.data)
                                    await self._handle_ws_event(
                                        data, session, chat_id, last_question
                                    )
                                    if data.get("type") == "question":
                                        last_question = data
                                except json.JSONDecodeError:
                                    pass
                            elif msg.type in (
                                aiohttp.WSMsgType.CLOSED,
                                aiohttp.WSMsgType.ERROR,
                            ):
                                break
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning(f"WS connection to '{session}' failed: {e}")
                await asyncio.sleep(5)  # Reconnect delay

    async def _handle_ws_event(
        self, data: dict, session: str, chat_id: int, last_question: dict | None
    ):
        """Handle a WebSocket event from the portal."""
        event_type = data.get("type")

        if event_type == "question" and self.forward_questions:
            question = data.get("question", "")
            options = data.get("options", [])
            header = data.get("header", "")

            text = f"*{session}*"
            if header:
                text += f" — {_escape_md(header)}"
            text += f"\n\n{_escape_md(question)}"

            # Build inline keyboard
            buttons = []
            for opt in options:
                idx = opt.get("index", 0)
                label = opt.get("text", f"Option {idx}")
                cb_data = f"answer:{session}:{idx}"
                buttons.append([InlineKeyboardButton(text=label, callback_data=cb_data)])

            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            await self.bot.send_message(
                chat_id, text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
            )

        elif event_type == "audio" and self.voice_replies:
            audio_b64 = data.get("data")
            if audio_b64:
                await self._send_voice_note(chat_id, audio_b64, session)

    async def _send_voice_note(self, chat_id: int, audio_b64: str, session: str):
        """Convert base64 WAV to OGG/Opus and send as voice note."""
        import base64

        try:
            wav_bytes = base64.b64decode(audio_b64)

            # Convert WAV to OGG/Opus via ffmpeg
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-i", "pipe:0",
                "-c:a", "libopus", "-b:a", "128k",
                "-f", "ogg", "pipe:1",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            ogg_data, _ = await proc.communicate(input=wav_bytes)

            if proc.returncode == 0 and ogg_data:
                voice_file = BufferedInputFile(ogg_data, filename="voice.ogg")
                await self.bot.send_voice(chat_id, voice=voice_file)
            else:
                log.warning("ffmpeg WAV→OGG conversion failed")
        except Exception as e:
            log.warning(f"Voice note send failed: {e}")

    async def _transcribe_voice(self, voice_bytes: bytes) -> str | None:
        """Transcribe voice note via STT server."""
        stt_url = _get_stt_url(self.config)
        if not stt_url:
            return None

        try:
            data = aiohttp.FormData()
            data.add_field("file", voice_bytes, filename="voice.ogg", content_type="audio/ogg")

            async with aiohttp.ClientSession() as http:
                async with http.post(f"{stt_url}/transcribe", data=data, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        return result.get("text", "").strip()
        except Exception as e:
            log.warning(f"STT transcription failed: {e}")

        return None


def _get_stt_url(config: dict) -> str | None:
    """Get STT server URL from config."""
    # Load from main agentwire config
    try:
        import yaml
        config_path = Path.home() / ".agentwire" / "config.yaml"
        if config_path.exists():
            with open(config_path) as f:
                full_config = yaml.safe_load(f) or {}
                return full_config.get("stt", {}).get("url", "http://localhost:8101")
    except Exception:
        pass
    return "http://localhost:8101"


def _escape_md(text: str) -> str:
    """Escape text for Telegram MarkdownV2."""
    special = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in text)


class AuthMiddleware:
    """Middleware that only allows whitelisted Telegram user IDs."""

    def __init__(self, allowed_users: set[int]):
        self.allowed_users = allowed_users

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user and user.id not in self.allowed_users:
            log.warning(f"Unauthorized access from user {user.id} ({user.username})")
            return  # Silently ignore
        return await handler(event, data)


# === Command Handlers ===


async def handle_start(message: Message):
    """Handle /start command."""
    bridge: TelegramBridge = router.bridge
    user_id = message.from_user.id
    bridge.set_chat(user_id, message.chat.id)

    # Get sessions list
    sessions = await _list_sessions()

    current = bridge.get_session(user_id)
    text = f"AgentWire connected\\.\n\nActive session: *{_escape_md(current)}*\n\n"

    if sessions:
        text += "Sessions:\n"
        for s in sessions:
            name = s.get("name", "?")
            marker = " ◀" if name == current else ""
            text += f"• `{_escape_md(name)}`{_escape_md(marker)}\n"
    else:
        text += "_No sessions running\\._"

    text += "\n\nSend a message to talk to the active session\\.\nUse /s name to switch sessions\\."

    await message.answer(text, parse_mode=ParseMode.MARKDOWN_V2)

    # Subscribe to portal WS for the active session
    await bridge.subscribe_session(current, message.chat.id)


SERVICE_SESSIONS = {"agentwire-portal", "agentwire-tts", "agentwire-stt", "agentwire-telegram", "agentwire-scheduler"}


async def _list_sessions() -> list[dict]:
    """List sessions via CLI, excluding service sessions."""
    result = await run_cmd(["list", "--sessions"])
    sessions = result.get("sessions", [])
    return [s for s in sessions if s.get("name") not in SERVICE_SESSIONS]


async def handle_list(message: Message):
    """Handle /list command."""
    bridge: TelegramBridge = router.bridge
    sessions = await _list_sessions()

    if not sessions:
        await message.answer("No sessions running.")
        return

    current = bridge.get_session(message.from_user.id)
    lines = ["*Sessions*\n"]
    for s in sessions:
        name = s.get("name", "?")
        marker = " ◀" if name == current else ""
        lines.append(f"• `{_escape_md(name)}`{_escape_md(marker)}")

    await message.answer("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)


async def handle_session(message: Message):
    """Handle /s <name> — switch active session."""
    bridge: TelegramBridge = router.bridge
    user_id = message.from_user.id
    chat_id = message.chat.id

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        current = bridge.get_session(user_id)
        await message.answer(f"Active session: `{_escape_md(current)}`\n\nUse /s name to switch\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    new_session = args[1].strip()

    # Unsubscribe from old session, subscribe to new
    old_session = bridge.get_session(user_id)
    await bridge.unsubscribe_session(old_session, chat_id)

    bridge.set_session(user_id, new_session)
    bridge.set_chat(user_id, chat_id)

    await bridge.subscribe_session(new_session, chat_id)
    await message.answer(f"Switched to *{_escape_md(new_session)}*", parse_mode=ParseMode.MARKDOWN_V2)


async def handle_output(message: Message):
    """Handle /output — show recent session output."""
    bridge: TelegramBridge = router.bridge
    session = bridge.get_session(message.from_user.id)

    output = await run_cmd_raw(["output", "-s", session])
    output = strip_ansi(output)

    if not output:
        await message.answer(f"No output from `{_escape_md(session)}`", parse_mode=ParseMode.MARKDOWN_V2)
        return

    # Truncate to Telegram's 4096 char limit
    if len(output) > 3900:
        output = output[-3900:]
        output = "…" + output

    await message.answer(f"```\n{output}\n```", parse_mode=ParseMode.MARKDOWN_V2)


async def handle_new(message: Message):
    """Handle /new <name> — create new session."""
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Usage: /new session\\_name", parse_mode=ParseMode.MARKDOWN_V2)
        return

    name = args[1].strip()
    result = await run_cmd(["new", "-s", name])

    if result.get("error"):
        await message.answer(f"Error: {result['error']}")
    else:
        bridge: TelegramBridge = router.bridge
        bridge.set_session(message.from_user.id, name)
        bridge.set_chat(message.from_user.id, message.chat.id)
        await bridge.subscribe_session(name, message.chat.id)
        await message.answer(f"Created and switched to *{_escape_md(name)}*", parse_mode=ParseMode.MARKDOWN_V2)


async def handle_kill(message: Message):
    """Handle /kill <name> — kill session with confirmation."""
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Usage: /kill session\\_name", parse_mode=ParseMode.MARKDOWN_V2)
        return

    name = args[1].strip()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Yes, kill it", callback_data=f"kill:{name}"),
            InlineKeyboardButton(text="Cancel", callback_data="kill:_cancel"),
        ]
    ])
    await message.answer(
        f"Kill session *{_escape_md(name)}*?",
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def handle_help(message: Message):
    """Handle /help command."""
    text = (
        "*Commands*\n\n"
        "/s name — switch session\n"
        "/list — list sessions\n"
        "/output — show session output\n"
        "/new name — create session\n"
        "/kill name — kill session\n"
        "/help — this message\n\n"
        "Send text or voice to talk to the active session\\."
    )
    await message.answer(text, parse_mode=ParseMode.MARKDOWN_V2)


async def handle_voice(message: Message):
    """Handle voice messages — transcribe and send to session."""
    bridge: TelegramBridge = router.bridge
    session = bridge.get_session(message.from_user.id)

    # Download voice file
    voice = message.voice
    file = await bridge.bot.get_file(voice.file_id)
    voice_bytes = io.BytesIO()
    await bridge.bot.download_file(file.file_path, voice_bytes)
    voice_bytes.seek(0)

    # Transcribe
    text = await bridge._transcribe_voice(voice_bytes.read())

    if not text:
        await message.answer("Could not transcribe voice message.")
        return

    # Send to session with voice hint
    prompt = f"[User said: '{text}' - respond using MCP tool: agentwire_say(text=\"...\")]"
    result = await run_cmd(["send", "-s", session, prompt])

    if result.get("error"):
        await message.answer(f"Error: {result['error']}")
    else:
        await message.answer(f"Sent to *{_escape_md(session)}*: _{_escape_md(text)}_", parse_mode=ParseMode.MARKDOWN_V2)


async def handle_text(message: Message):
    """Handle plain text messages — send to active session."""
    bridge: TelegramBridge = router.bridge
    user_id = message.from_user.id
    session = bridge.get_session(user_id)
    bridge.set_chat(user_id, message.chat.id)

    # Ensure WS subscription
    await bridge.subscribe_session(session, message.chat.id)

    text = message.text.strip()
    if not text:
        return

    prefixed = f"[Telegram from {message.from_user.first_name}: '{text}']"
    result = await run_cmd(["send", "-s", session, prefixed])

    if result.get("error"):
        await message.answer(f"Error: {result['error']}")
    else:
        await message.answer(f"Sent to *{_escape_md(session)}*", parse_mode=ParseMode.MARKDOWN_V2)


async def handle_callback(callback: CallbackQuery):
    """Handle inline keyboard callbacks."""
    bridge: TelegramBridge = router.bridge
    data = callback.data

    if data == "kill:_cancel":
        await callback.message.edit_text("Cancelled.")
        await callback.answer()
        return

    if data.startswith("kill:"):
        name = data[5:]
        result = await run_cmd(["kill", "-s", name])
        if result.get("error"):
            await callback.message.edit_text(f"Error: {result['error']}")
        else:
            await callback.message.edit_text(f"Session '{name}' killed.")
            # Unsubscribe
            await bridge.unsubscribe_session(name, callback.message.chat.id)
        await callback.answer()
        return

    if data.startswith("answer:"):
        # answer:session:option_index
        parts = data.split(":", 2)
        if len(parts) == 3:
            session = parts[1]
            option = parts[2]

            # Send option number to session
            result = await run_cmd(["send", "-s", session, option])

            if result.get("error"):
                await callback.answer(f"Error: {result['error']}", show_alert=True)
            else:
                await callback.answer(f"Sent option {option}")
                await callback.message.edit_text(
                    callback.message.text + f"\n\n_Selected: {option}_",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
        return

    await callback.answer()


def load_telegram_config() -> dict:
    """Load telegram config from ~/.agentwire/config.yaml."""
    try:
        import yaml
        config_path = Path.home() / ".agentwire" / "config.yaml"
        if config_path.exists():
            with open(config_path) as f:
                full_config = yaml.safe_load(f) or {}
                return full_config.get("telegram", {})
    except Exception:
        pass
    return {}


def run_bridge():
    """Entry point for running the Telegram bridge.

    Can be invoked via:
      - agentwire telegram serve
      - python -m agentwire.bridges.telegram
    """
    from dotenv import load_dotenv

    # Load .env files (same order as CLI)
    load_dotenv()  # CWD
    load_dotenv(Path.home() / ".agentwire" / ".env")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    config = load_telegram_config()

    # Bot token: env var takes precedence
    bot_token = os.environ.get("TELEGRAM_AGENTWIRE_BOT_TOKEN") or config.get("bot_token", "")
    if not bot_token:
        print("Error: No bot token. Set TELEGRAM_AGENTWIRE_BOT_TOKEN or telegram.bot_token in config.", file=__import__("sys").stderr)
        __import__("sys").exit(1)

    # Allowed users: env var (comma-separated) or config
    allowed_users_env = os.environ.get("TELEGRAM_USER_ID", "")
    if allowed_users_env:
        allowed_users = [int(uid.strip()) for uid in allowed_users_env.split(",") if uid.strip()]
    else:
        allowed_users = config.get("allowed_users", [])

    if not allowed_users:
        print("Error: No allowed users. Set TELEGRAM_USER_ID or telegram.allowed_users in config.", file=__import__("sys").stderr)
        __import__("sys").exit(1)

    # Portal URL for WS subscription
    portal_config_url = "wss://localhost:8765"
    try:
        import yaml
        config_path = Path.home() / ".agentwire" / "config.yaml"
        if config_path.exists():
            with open(config_path) as f:
                full = yaml.safe_load(f) or {}
                port = full.get("server", {}).get("port", 8765)
                portal_config_url = f"wss://localhost:{port}"
    except Exception:
        pass

    config["portal_url"] = portal_config_url

    bridge = TelegramBridge(bot_token, allowed_users, config)

    print(f"Starting Telegram bridge (allowed users: {allowed_users})")
    asyncio.run(bridge.start())


if __name__ == "__main__":
    run_bridge()
