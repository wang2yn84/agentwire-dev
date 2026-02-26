"""Agent SDK backend - pure Python async sessions, no tmux."""

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .base import AgentBackend

logger = logging.getLogger(__name__)

# Permission mode mapping from session type to SDK permission mode
PERMISSION_MODES = {
    "sdk-bypass": "bypassPermissions",
    "sdk-prompted": "default",
    "sdk-restricted": "plan",
}


@dataclass
class SdkSession:
    """State for a single SDK session."""

    name: str
    path: Path
    session_type: str
    client: object = None  # ClaudeSDKClient instance
    messages: list[dict] = field(default_factory=list)
    busy: bool = False
    session_id: str | None = None  # Claude session ID for resume
    callbacks: set = field(default_factory=set)
    created_at: float = field(default_factory=time.time)
    _response_task: asyncio.Task | None = None
    parent_session: str | None = None
    children: list[str] = field(default_factory=list)
    auto_kill_on_complete: bool = True
    system_prompt_append: str | None = None


def _message_to_dict(message) -> dict:
    """Convert an SDK Message object to a serializable dict.

    Handles AssistantMessage, UserMessage, SystemMessage, ResultMessage, StreamEvent.
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        SystemMessage,
        UserMessage,
    )
    from claude_agent_sdk.types import StreamEvent

    ts = time.time()

    if isinstance(message, AssistantMessage):
        content_blocks = []
        for block in message.content:
            content_blocks.append(_content_block_to_dict(block))
        return {
            "type": "assistant",
            "timestamp": ts,
            "model": getattr(message, "model", None),
            "content": content_blocks,
        }

    if isinstance(message, UserMessage):
        content = message.content
        if isinstance(content, str):
            text = content
        else:
            text = " ".join(
                _content_block_to_dict(b).get("text", "") for b in content
            )
        return {
            "type": "user",
            "timestamp": ts,
            "text": text,
        }

    if isinstance(message, SystemMessage):
        return {
            "type": "system",
            "timestamp": ts,
            "subtype": message.subtype,
            "data": message.data,
        }

    if isinstance(message, ResultMessage):
        return {
            "type": "result",
            "timestamp": ts,
            "subtype": message.subtype,
            "duration_ms": message.duration_ms,
            "duration_api_ms": message.duration_api_ms,
            "is_error": message.is_error,
            "num_turns": message.num_turns,
            "session_id": message.session_id,
            "total_cost_usd": message.total_cost_usd,
            "result": message.result,
        }

    if isinstance(message, StreamEvent):
        return {
            "type": "stream_event",
            "timestamp": ts,
            "uuid": message.uuid,
            "session_id": message.session_id,
            "event": message.event,
            "parent_tool_use_id": message.parent_tool_use_id,
        }

    # Fallback for unknown types
    return {
        "type": "unknown",
        "timestamp": ts,
        "repr": repr(message),
    }


def _content_block_to_dict(block) -> dict:
    """Convert a ContentBlock to a serializable dict."""
    from claude_agent_sdk import TextBlock, ToolUseBlock
    from claude_agent_sdk.types import ThinkingBlock, ToolResultBlock

    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}

    if isinstance(block, ToolUseBlock):
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input,
        }

    if isinstance(block, ToolResultBlock):
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": block.content,
            "is_error": block.is_error,
        }

    if isinstance(block, ThinkingBlock):
        return {
            "type": "thinking",
            "thinking": block.thinking,
        }

    return {"type": "unknown", "repr": repr(block)}


def _render_messages_as_text(messages: list[dict], lines: int = 50) -> str:
    """Render structured messages as plain text for backward compat."""
    output_lines = []
    for msg in messages:
        msg_type = msg.get("type", "")
        if msg_type == "user":
            output_lines.append(f"> {msg.get('text', '')}")
        elif msg_type == "assistant":
            for block in msg.get("content", []):
                if block.get("type") == "text":
                    output_lines.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    output_lines.append(f"[Tool: {block.get('name', '')}]")
                elif block.get("type") == "tool_result":
                    content = block.get("content", "")
                    if isinstance(content, str):
                        output_lines.append(f"  → {content[:200]}")
        elif msg_type == "result":
            status = "error" if msg.get("is_error") else "complete"
            output_lines.append(f"[{status}] {msg.get('result', '')}")
        elif msg_type == "child_completed":
            status = "error" if msg.get("is_error") else "complete"
            child = msg.get("child_name", "?")
            output_lines.append(f"[child:{child} {status}] {msg.get('result', '')}")
        elif msg_type == "system":
            output_lines.append(f"[system:{msg.get('subtype', '')}]")

    # Return last N lines
    all_text = "\n".join(output_lines)
    text_lines = all_text.split("\n")
    return "\n".join(text_lines[-lines:])


class SdkAgent(AgentBackend):
    """Agent backend using the Claude Agent SDK.

    Sessions are pure Python async - no tmux. Each session holds a
    ClaudeSDKClient that manages the Claude Code subprocess.
    """

    def __init__(self, config: dict):
        self.config = config
        self.sessions: dict[str, SdkSession] = {}
        max_sessions = config.get("sdk", {}).get("max_sessions", 10)
        self.max_sessions = max_sessions
        self._persist_dir = Path("~/.agentwire/sdk-sessions").expanduser()
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._restore_sessions()

    def supports_structured_events(self) -> bool:
        return True

    def create_session(self, name: str, path: Path, options: dict | None = None) -> bool:
        """Create an SDK session (synchronous entry point).

        The actual ClaudeSDKClient is connected lazily on first send_prompt.

        Options:
            session_type: SDK permission type (sdk-bypass, sdk-prompted, sdk-restricted)
            parent_session: Parent session name for hierarchy
            auto_kill_on_complete: Kill child on ResultMessage (default True)
            system_prompt_append: Extra instructions appended to system prompt
        """
        options = options or {}
        session_type = options.get("session_type", "sdk-bypass")

        if name in self.sessions:
            logger.warning(f"SDK session '{name}' already exists")
            return False

        if len(self.sessions) >= self.max_sessions:
            logger.error(f"Max SDK sessions ({self.max_sessions}) reached")
            return False

        session = SdkSession(
            name=name,
            path=Path(path),
            session_type=session_type,
            parent_session=options.get("parent_session"),
            auto_kill_on_complete=options.get("auto_kill_on_complete", True),
            system_prompt_append=options.get("system_prompt_append"),
        )
        self.sessions[name] = session
        self._persist_session(session)
        logger.info(f"Created SDK session '{name}' at {path} (type={session_type})")
        return True

    def session_exists(self, name: str) -> bool:
        return name in self.sessions

    def get_output(self, name: str, lines: int = 50) -> str:
        """Render message history as plain text (backward compat bridge)."""
        session = self.sessions.get(name)
        if not session:
            return ""
        return _render_messages_as_text(session.messages, lines)

    def send_keys(self, name: str, keys: str) -> bool:
        logger.warning(f"send_keys not supported for SDK sessions (session={name})")
        return False

    def send_input(self, name: str, text: str) -> bool:
        """Bridge to send_prompt for backward compat. Runs async in background."""
        session = self.sessions.get(name)
        if not session:
            return False
        # Schedule the async send_prompt
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.send_prompt(name, text))
            return True
        except RuntimeError:
            logger.error("No running event loop for send_input bridge")
            return False

    def kill_session(self, name: str) -> bool:
        """Kill an SDK session (synchronous). Recursively kills children."""
        session = self.sessions.pop(name, None)
        if not session:
            return False

        # Recursively kill all children first
        for child_name in list(session.children):
            self.kill_session(child_name)

        # Remove self from parent's children list
        if session.parent_session and session.parent_session in self.sessions:
            parent = self.sessions[session.parent_session]
            if name in parent.children:
                parent.children.remove(name)
                self._persist_session(parent)

        # Cancel response task if running
        if session._response_task and not session._response_task.done():
            session._response_task.cancel()

        # Disconnect client in background if possible
        if session.client:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._disconnect_client(session))
            except RuntimeError:
                pass  # No event loop, client will be GC'd

        self._remove_persisted(name)
        logger.info(f"Killed SDK session '{name}'")
        return True

    def list_sessions(self) -> list[str]:
        return list(self.sessions.keys())

    # --- Structured event methods ---

    async def send_prompt(self, name: str, prompt: str) -> bool:
        session = self.sessions.get(name)
        if not session:
            logger.error(f"SDK session '{name}' not found")
            return False

        if session.busy:
            logger.warning(f"SDK session '{name}' is busy, queuing not supported yet")
            return False

        session.busy = True

        # Add user message to history
        user_msg = {
            "type": "user",
            "timestamp": time.time(),
            "text": prompt,
        }
        session.messages.append(user_msg)
        self._persist_session(session)
        await self._fire_callbacks(session, user_msg)

        # Ensure client is connected
        if not session.client:
            connected = await self._connect_client(session)
            if not connected:
                session.busy = False
                return False

        # Send query and process response in background task
        session._response_task = asyncio.create_task(
            self._process_response(session, prompt)
        )
        return True

    async def get_messages(self, name: str) -> list[dict]:
        session = self.sessions.get(name)
        if not session:
            return []
        return list(session.messages)

    async def interrupt_session(self, name: str) -> bool:
        session = self.sessions.get(name)
        if not session or not session.client:
            return False
        try:
            await session.client.interrupt()
            return True
        except Exception as e:
            logger.error(f"Failed to interrupt SDK session '{name}': {e}")
            return False

    def register_message_callback(self, name: str, callback: Callable) -> None:
        session = self.sessions.get(name)
        if session:
            session.callbacks.add(callback)

    def unregister_message_callback(self, name: str, callback: Callable) -> None:
        session = self.sessions.get(name)
        if session:
            session.callbacks.discard(callback)

    # --- Internal helpers ---

    async def _connect_client(self, session: SdkSession) -> bool:
        """Connect the ClaudeSDKClient for a session."""
        try:
            from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

            permission_mode = PERMISSION_MODES.get(session.session_type, "default")

            options = ClaudeAgentOptions(
                permission_mode=permission_mode,
                cwd=str(session.path),
                setting_sources=["user", "project", "local"],
                include_partial_messages=True,
            )

            # Append role instructions to system prompt if provided
            if session.system_prompt_append:
                options.system_prompt = {
                    "type": "preset",
                    "preset": "claude_code",
                    "append": session.system_prompt_append,
                }

            client = ClaudeSDKClient(options=options)
            await client.connect()
            session.client = client
            logger.info(f"SDK client connected for session '{session.name}'")
            return True
        except Exception as e:
            logger.error(f"Failed to connect SDK client for '{session.name}': {e}")
            return False

    async def _process_response(self, session: SdkSession, prompt: str) -> None:
        """Send prompt and iterate response messages."""
        try:
            await session.client.query(prompt)

            async for message in session.client.receive_response():
                msg_dict = _message_to_dict(message)
                session.messages.append(msg_dict)
                await self._fire_callbacks(session, msg_dict)

                # Capture session_id from ResultMessage and persist
                from claude_agent_sdk import ResultMessage
                if isinstance(message, ResultMessage):
                    session.session_id = message.session_id
                    self._persist_session(session)

                    # Notify parent if this is a child session
                    if session.parent_session:
                        await self._handle_child_completion(
                            session.parent_session, session.name, msg_dict
                        )

        except asyncio.CancelledError:
            logger.info(f"Response processing cancelled for '{session.name}'")
        except Exception as e:
            logger.error(f"Error processing SDK response for '{session.name}': {e}")
            error_msg = {
                "type": "error",
                "timestamp": time.time(),
                "error": str(e),
            }
            session.messages.append(error_msg)
            await self._fire_callbacks(session, error_msg)
        finally:
            session.busy = False

    async def _fire_callbacks(self, session: SdkSession, message: dict) -> None:
        """Fire all registered callbacks for a session."""
        for callback in list(session.callbacks):
            try:
                await callback(message)
            except Exception as e:
                logger.error(f"Callback error for '{session.name}': {e}")

    # --- Hierarchy methods ---

    async def spawn_child(
        self,
        parent_name: str,
        child_name: str,
        path: str | None = None,
        session_type: str | None = None,
        system_prompt_append: str | None = None,
        auto_kill_on_complete: bool = True,
    ) -> bool:
        """Spawn a child SDK session linked to a parent.

        Args:
            parent_name: Name of the parent session
            child_name: Name for the new child session
            path: Working directory (defaults to parent's path)
            session_type: SDK session type (defaults to parent's type)
            system_prompt_append: Extra instructions for the child
            auto_kill_on_complete: Kill child when it completes (default True)
        """
        parent = self.sessions.get(parent_name)
        if not parent:
            logger.error(f"Parent session '{parent_name}' not found")
            return False

        child_path = Path(path) if path else parent.path
        child_type = session_type or parent.session_type

        success = self.create_session(
            name=child_name,
            path=child_path,
            options={
                "session_type": child_type,
                "parent_session": parent_name,
                "auto_kill_on_complete": auto_kill_on_complete,
                "system_prompt_append": system_prompt_append,
            },
        )
        if not success:
            return False

        # Register parent-child link
        parent.children.append(child_name)
        self._persist_session(parent)

        logger.info(f"Spawned child '{child_name}' for parent '{parent_name}'")
        return True

    async def _handle_child_completion(
        self, parent_name: str, child_name: str, result_msg: dict
    ) -> None:
        """Handle a child session completing (ResultMessage received).

        Sends a child_completed notification to the parent and optionally kills the child.
        """
        parent = self.sessions.get(parent_name)
        if not parent:
            return

        notification = {
            "type": "child_completed",
            "timestamp": time.time(),
            "child_name": child_name,
            "status": "error" if result_msg.get("is_error") else "complete",
            "result": result_msg.get("result", ""),
            "cost_usd": result_msg.get("total_cost_usd"),
            "duration_ms": result_msg.get("duration_ms"),
            "is_error": result_msg.get("is_error", False),
        }
        parent.messages.append(notification)
        self._persist_session(parent)
        await self._fire_callbacks(parent, notification)

        # Auto-kill child if configured
        child = self.sessions.get(child_name)
        if child and child.auto_kill_on_complete:
            logger.info(f"Auto-killing completed child '{child_name}'")
            self.kill_session(child_name)

    def list_children(self, parent_name: str) -> list[dict]:
        """List children of a parent session with their status."""
        parent = self.sessions.get(parent_name)
        if not parent:
            return []

        children = []
        for child_name in parent.children:
            child = self.sessions.get(child_name)
            if child:
                children.append({
                    "name": child_name,
                    "busy": child.busy,
                    "message_count": len(child.messages),
                    "path": str(child.path),
                    "session_type": child.session_type,
                })
        return children

    async def _disconnect_client(self, session: SdkSession) -> None:
        """Disconnect a client safely."""
        try:
            if session.client:
                await session.client.disconnect()
        except Exception as e:
            logger.debug(f"Error disconnecting SDK client: {e}")

    # --- Persistence ---

    def _persist_session(self, session: SdkSession) -> None:
        """Save session metadata and message history to disk."""
        try:
            data = {
                "name": session.name,
                "path": str(session.path),
                "session_type": session.session_type,
                "session_id": session.session_id,
                "created_at": session.created_at,
                "messages": session.messages,
                "parent_session": session.parent_session,
                "children": session.children,
                "auto_kill_on_complete": session.auto_kill_on_complete,
                "system_prompt_append": session.system_prompt_append,
            }
            path = self._persist_dir / f"{session.name}.json"
            path.write_text(json.dumps(data, default=str))
        except Exception as e:
            logger.error(f"Failed to persist SDK session '{session.name}': {e}")

    def _remove_persisted(self, name: str) -> None:
        """Remove persisted session data."""
        try:
            path = self._persist_dir / f"{name}.json"
            if path.exists():
                path.unlink()
        except Exception as e:
            logger.error(f"Failed to remove persisted SDK session '{name}': {e}")

    def _restore_sessions(self) -> None:
        """Restore SDK sessions from disk on startup."""
        try:
            for path in self._persist_dir.glob("*.json"):
                data = json.loads(path.read_text())
                name = data.get("name", path.stem)
                session = SdkSession(
                    name=name,
                    path=Path(data.get("path", ".")),
                    session_type=data.get("session_type", "sdk-bypass"),
                    session_id=data.get("session_id"),
                    created_at=data.get("created_at", time.time()),
                    messages=data.get("messages", []),
                    parent_session=data.get("parent_session"),
                    children=data.get("children", []),
                    auto_kill_on_complete=data.get("auto_kill_on_complete", True),
                    system_prompt_append=data.get("system_prompt_append"),
                )
                self.sessions[name] = session
                logger.info(f"Restored SDK session '{name}' ({len(session.messages)} messages)")
        except Exception as e:
            logger.error(f"Failed to restore SDK sessions: {e}")
