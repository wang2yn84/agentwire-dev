"""Kokoro TTS Engine (kokoro-onnx) - CPU-only, ultra-lightweight."""

import asyncio
from pathlib import Path
from typing import Iterator

import numpy as np

from ..base import TTSCapabilities, TTSEngine, TTSRequest, TTSResult

# Preset voices bundled with Kokoro v1.0
# Full list: https://huggingface.co/hexgrad/Kokoro-82M-ONNX
PRESET_VOICES = [
    # American English (female)
    "af_heart",
    "af_bella",
    "af_nicole",
    "af_sky",
    "af_sarah",
    "af_alloy",
    "af_aoede",
    "af_jessica",
    "af_kore",
    "af_nova",
    "af_river",
    # American English (male)
    "am_adam",
    "am_michael",
    "am_echo",
    "am_eric",
    "am_liam",
    "am_onyx",
    "am_puck",
    # British English (female)
    "bf_emma",
    "bf_isabella",
    # British English (male)
    "bm_george",
    "bm_lewis",
    # Spanish
    "ef_dora",
    # French
    "ff_siwis",
    # Hindi
    "hf_alpha",
    "hf_beta",
    # Italian
    "im_nicola",
    # Japanese
    "jf_alpha",
    "jf_gongitsune",
    # Portuguese
    "pf_dora",
    # Chinese
    "zf_xiaobei",
    "zf_xiaoni",
]

SUPPORTED_LANGUAGES = [
    "English",
    "Spanish",
    "French",
    "Hindi",
    "Italian",
    "Japanese",
    "Portuguese",
    "Chinese",
]

_LANG_MAP = {
    "English": "en-us",
    "Spanish": "es",
    "French": "fr-fr",
    "Hindi": "hi",
    "Italian": "it",
    "Japanese": "ja",
    "Portuguese": "pt-br",
    "Chinese": "zh",
}

DEFAULT_VOICE = "af_heart"


class KokoroEngine(TTSEngine):
    """Kokoro TTS engine via kokoro-onnx.

    Ultra-lightweight CPU-only TTS:
    - ~82M parameters, ~170 MB ONNX model (fp16)
    - No GPU required — pure ONNX CPU inference
    - Near real-time on Apple Silicon / modern Intel CPU
    - 30+ preset voices across 8 languages
    - Streaming support
    - Model auto-downloaded from GitHub releases on first use (~170 MB, cached in ~/.cache/kokoro_onnx/)

    Install:
        pip install kokoro-onnx
        pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
    """

    # GitHub release URL for model files
    _MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
    _VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

    def __init__(self, voices_dir: Path | None = None):
        from kokoro_onnx import Kokoro

        model_path = self._ensure_file("kokoro-v1.0.onnx", self._MODEL_URL)
        voices_path = self._ensure_file("voices-v1.0.bin", self._VOICES_URL)

        print("Loading Kokoro ONNX model...")
        self._model = Kokoro(str(model_path), str(voices_path))
        self._voices_dir = voices_dir
        self._sample_rate = 24000
        print("Kokoro loaded!")

    @staticmethod
    def _ensure_file(filename: str, url: str) -> Path:
        """Download file to ~/.cache/kokoro_onnx/ if not already present."""
        import urllib.request

        cache_dir = Path.home() / ".cache" / "kokoro_onnx"
        cache_dir.mkdir(parents=True, exist_ok=True)
        dest = cache_dir / filename

        if not dest.exists():
            size_mb = 170 if "onnx" in filename else 10
            print(f"Downloading {filename} (~{size_mb} MB)...")
            urllib.request.urlretrieve(url, dest)
            print(f"Saved to {dest}")

        return dest

    @property
    def name(self) -> str:
        return "Kokoro"

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def capabilities(self) -> TTSCapabilities:
        return TTSCapabilities(
            voice_cloning=False,
            voice_design=False,
            preset_voices=PRESET_VOICES,
            emotion_control=False,
            paralinguistic_tags=False,
            streaming=True,
            languages=SUPPORTED_LANGUAGES,
        )

    def _resolve_voice(self, request: TTSRequest) -> str:
        """Return a valid Kokoro voice name, falling back to default.

        If request.voice isn't a known preset (e.g. user has voice: dotdev from
        a different backend config), we silently fall back to af_heart.
        """
        if request.voice and request.voice in PRESET_VOICES:
            return request.voice
        return DEFAULT_VOICE

    def generate(self, request: TTSRequest) -> TTSResult:
        import torch

        voice = self._resolve_voice(request)
        lang = _LANG_MAP.get(request.language, "en-us")

        samples, sample_rate = self._model.create(
            text=request.text,
            voice=voice,
            speed=1.0,
            lang=lang,
        )

        # Convert numpy (N,) → torch (1, N) to match TTSResult interface
        tensor = torch.from_numpy(np.asarray(samples, dtype=np.float32)).unsqueeze(0)
        return TTSResult(audio=tensor, sample_rate=sample_rate)

    def generate_stream(self, request: TTSRequest) -> Iterator[bytes]:
        """Yield WAV chunks from Kokoro's async streaming generator.

        kokoro-onnx create_stream() is an async generator; we drive it
        chunk-by-chunk from a dedicated event loop so the sync TTSEngine
        interface is preserved.

        Uses stdlib wave for WAV serialization (avoids torchaudio's BytesIO
        incompatibility with the torchcodec backend in CPU-only builds).
        """
        import io
        import wave

        voice = self._resolve_voice(request)
        lang = _LANG_MAP.get(request.language, "en-us")

        loop = asyncio.new_event_loop()
        async_gen = self._model.create_stream(
            text=request.text,
            voice=voice,
            speed=1.0,
            lang=lang,
        )

        try:
            while True:
                try:
                    samples, sample_rate = loop.run_until_complete(async_gen.__anext__())
                except StopAsyncIteration:
                    break

                pcm = np.asarray(samples, dtype=np.float32)
                pcm_int16 = (pcm * 32767).clip(-32768, 32767).astype(np.int16)
                buf = io.BytesIO()
                with wave.open(buf, "w") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(sample_rate)
                    wf.writeframes(pcm_int16.tobytes())
                buf.seek(0)
                yield buf.read()
        finally:
            loop.close()

    def unload(self) -> None:
        if hasattr(self, "_model"):
            del self._model
            self._model = None
