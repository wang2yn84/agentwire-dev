"""Speech-to-text backend for AgentWire."""

import logging
from typing import Any

from .base import STTBackend
from .server_backend import STTServerBackend
from .whisperkit import WhisperKitSTT

__all__ = [
    "STTBackend",
    "STTServerBackend",
    "WhisperKitSTT",
    "get_stt_backend",
]

logger = logging.getLogger(__name__)


def get_stt_backend(config: Any) -> STTBackend:
    """Get STT backend based on configuration.

    Prefers STT server if URL is configured and server is available.
    Falls back to WhisperKit for local transcription.

    Args:
        config: Configuration object with optional stt.url and stt.model_path.

    Returns:
        STTBackend instance (STTServerBackend or WhisperKitSTT).
    """
    # Extract config values
    stt_url = None
    model_path = None
    timeout = 60

    if hasattr(config, "stt"):
        stt_config = config.stt
        stt_url = getattr(stt_config, "url", None)
        model_path = getattr(stt_config, "model_path", None)
        timeout = getattr(stt_config, "timeout", 60)
    elif isinstance(config, dict):
        stt_config = config.get("stt", {})
        stt_url = stt_config.get("url")
        model_path = stt_config.get("model_path")
        timeout = stt_config.get("timeout", 60)

    # Try STT server first if URL is configured
    if stt_url:
        if STTServerBackend.is_available(stt_url):
            logger.info(f"Using STT server at {stt_url}")
            return STTServerBackend(url=stt_url, timeout=timeout)
        else:
            logger.warning(f"STT server at {stt_url} not available, falling back to WhisperKit")

    # Fall back to WhisperKit
    logger.info("Using local WhisperKitSTT")
    return WhisperKitSTT(model_path=model_path, timeout=timeout)
