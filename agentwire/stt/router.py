#!/usr/bin/env python3
"""STT Router — fixed port 8199, forwards /transcribe to whichever backend is active.

Backends:
  faster-whisper  http://localhost:8200   (GPU, local)
  whispercpp      http://localhost:8201   (CPU/GPU, local gguf)
  openai          http://localhost:8202   (cloud proxy)

Switch active backend via POST /switch/{backend} or:
  agentwire stt switch faster-whisper
"""

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

BACKEND_URLS: dict[str, str] = {
    "faster-whisper": "http://localhost:8200",
    "whispercpp": "http://localhost:8201",
    "openai": "http://localhost:8202",
    "gpt4o-mini": "http://localhost:8203",
}
DEFAULT_BACKEND = "faster-whisper"
ACTIVE_BACKEND_FILE = Path.home() / ".agentwire" / "stt-active-backend"

active_backend: str = DEFAULT_BACKEND
_client: httpx.AsyncClient | None = None


def _load_active_backend() -> str:
    try:
        name = ACTIVE_BACKEND_FILE.read_text().strip()
        if name in BACKEND_URLS:
            return name
    except FileNotFoundError:
        pass
    return DEFAULT_BACKEND


def _save_active_backend(name: str) -> None:
    ACTIVE_BACKEND_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_BACKEND_FILE.write_text(name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global active_backend, _client
    active_backend = _load_active_backend()
    _client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=3.0))
    logger.info(f"STT router started — active backend: {active_backend}")
    yield
    await _client.aclose()


app = FastAPI(title="AgentWire STT Router", lifespan=lifespan)


async def _check_health(url: str) -> bool:
    try:
        r = await _client.get(f"{url}/health", timeout=1.0)
        return r.status_code == 200
    except Exception:
        return False


@app.get("/health")
async def health():
    return {"status": "ok", "active": active_backend, "url": BACKEND_URLS[active_backend]}


@app.get("/status")
async def status():
    import asyncio
    healths = await asyncio.gather(*[_check_health(url) for url in BACKEND_URLS.values()])
    backends = {
        name: {"url": url, "healthy": healthy, "active": name == active_backend}
        for (name, url), healthy in zip(BACKEND_URLS.items(), healths)
    }
    return {"active": active_backend, "backends": backends}


@app.post("/switch/{backend}")
async def switch(backend: str):
    if backend not in BACKEND_URLS:
        return JSONResponse({"error": f"Unknown backend '{backend}'. Valid: {list(BACKEND_URLS)}"}, status_code=400)
    global active_backend
    active_backend = backend
    _save_active_backend(backend)
    logger.info(f"Switched STT backend to: {backend}")
    return {"active": backend, "url": BACKEND_URLS[backend]}


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    target = BACKEND_URLS[active_backend]
    content = await file.read()
    try:
        resp = await _client.post(
            f"{target}/transcribe",
            files={"file": (file.filename or "audio.wav", content, file.content_type or "audio/wav")},
            timeout=30.0,
        )
        if resp.status_code != 200:
            return JSONResponse({"error": f"Backend {active_backend} returned {resp.status_code}"}, status_code=503)
        return JSONResponse(resp.json())
    except httpx.ConnectError:
        return JSONResponse({"error": f"Backend {active_backend} unreachable at {target}"}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8199, log_level="info")
