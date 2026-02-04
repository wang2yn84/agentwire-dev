"""STT backend that uses the STT server via HTTP."""

import json
import logging
import urllib.error
import urllib.request
from pathlib import Path

from .base import STTBackend

logger = logging.getLogger(__name__)


class STTServerBackend(STTBackend):
    """STT backend that transcribes via HTTP server."""

    @property
    def name(self) -> str:
        """Return the backend name."""
        return "STTServer"

    def __init__(self, url: str, timeout: int = 30):
        """Initialize with server URL.

        Args:
            url: STT server URL (e.g., http://localhost:8101)
            timeout: Request timeout in seconds
        """
        self.url = url.rstrip("/")
        self.timeout = timeout

    async def transcribe(self, audio_path: Path) -> str:
        """Transcribe audio file via STT server.

        Args:
            audio_path: Path to audio file (wav format)

        Returns:
            Transcribed text
        """
        # Read audio file
        with open(audio_path, "rb") as f:
            audio_data = f.read()

        # Build multipart form data
        boundary = "----AgentWireBoundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
            f"Content-Type: audio/wav\r\n\r\n"
        ).encode() + audio_data + f"\r\n--{boundary}--\r\n".encode()

        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }

        req = urllib.request.Request(
            f"{self.url}/transcribe",
            data=body,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                result = json.loads(resp.read().decode())
                return result.get("text", "")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            logger.error(f"STT server request failed: {e}")
            raise RuntimeError(f"STT server error: {e}") from e

    @classmethod
    def is_available(cls, url: str) -> bool:
        """Check if STT server is available.

        Args:
            url: Server URL to check

        Returns:
            True if server is healthy
        """
        try:
            health_req = urllib.request.Request(f"{url.rstrip('/')}/health")
            with urllib.request.urlopen(health_req, timeout=2) as resp:
                health = json.loads(resp.read().decode())
                return health.get("status") == "ok"
        except (urllib.error.URLError, TimeoutError, OSError):
            return False
