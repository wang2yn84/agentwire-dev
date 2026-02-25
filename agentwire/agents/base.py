"""Abstract base class for agent backends."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path


class AgentBackend(ABC):
    """Abstract base for managing AI coding agent sessions."""

    @abstractmethod
    def create_session(self, name: str, path: Path, options: dict | None = None) -> bool:
        """Create a new agent session.

        Args:
            name: Session name (e.g., "api", "auth")
            path: Working directory for the session
            options: Optional backend-specific options

        Returns:
            True if session was created successfully
        """
        pass

    @abstractmethod
    def session_exists(self, name: str) -> bool:
        """Check if a session exists.

        Args:
            name: Session name

        Returns:
            True if session exists
        """
        pass

    @abstractmethod
    def get_output(self, name: str, lines: int = 50) -> str:
        """Get recent output from a session.

        Args:
            name: Session name
            lines: Number of lines to capture

        Returns:
            Recent terminal output as string
        """
        pass

    @abstractmethod
    def send_keys(self, name: str, keys: str) -> bool:
        """Send keys to a session WITHOUT Enter.

        Use for keypresses like selecting menu options.

        Args:
            name: Session name
            keys: Keys to send

        Returns:
            True if keys were sent successfully
        """
        pass

    @abstractmethod
    def send_input(self, name: str, text: str) -> bool:
        """Send input to a session (text + Enter).

        Args:
            name: Session name
            text: Text to send (Enter key is appended)

        Returns:
            True if input was sent successfully
        """
        pass

    @abstractmethod
    def kill_session(self, name: str) -> bool:
        """Terminate a session.

        Args:
            name: Session name

        Returns:
            True if session was terminated successfully
        """
        pass

    @abstractmethod
    def list_sessions(self) -> list[str]:
        """List all active sessions.

        Returns:
            List of session names
        """
        pass

    # --- Structured event methods (SDK backends) ---
    # Non-abstract with default NotImplementedError so TmuxAgent is unaffected.

    def supports_structured_events(self) -> bool:
        """Whether this backend provides structured JSON message events.

        Returns:
            True for SDK-based backends, False for terminal-scraping backends.
        """
        return False

    async def send_prompt(self, name: str, prompt: str) -> bool:
        """Send a prompt to a session and begin processing.

        Args:
            name: Session name
            prompt: The user prompt text

        Returns:
            True if prompt was sent successfully
        """
        raise NotImplementedError("send_prompt requires an SDK backend")

    async def get_messages(self, name: str) -> list[dict]:
        """Get full structured message history for a session.

        Args:
            name: Session name

        Returns:
            List of message dicts with type, timestamp, content fields
        """
        raise NotImplementedError("get_messages requires an SDK backend")

    async def interrupt_session(self, name: str) -> bool:
        """Interrupt a running session.

        Args:
            name: Session name

        Returns:
            True if interrupt was sent successfully
        """
        raise NotImplementedError("interrupt_session requires an SDK backend")

    def register_message_callback(self, name: str, callback: Callable) -> None:
        """Register a callback for real-time message events.

        Args:
            name: Session name
            callback: Async callable receiving a message dict
        """
        raise NotImplementedError("register_message_callback requires an SDK backend")

    def unregister_message_callback(self, name: str, callback: Callable) -> None:
        """Unregister a previously registered message callback.

        Args:
            name: Session name
            callback: The callback to remove
        """
        raise NotImplementedError("unregister_message_callback requires an SDK backend")
