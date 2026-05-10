#!/usr/bin/env python3
"""OpenAI Whisper API proxy — wraps /v1/audio/transcriptions at POST /transcribe.

Reads OPENAI_API_KEY from environment.
Run via: agentwire stt openai-start  (or directly: python openai_backend.py)
"""

import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

app = FastAPI(title="AgentWire STT OpenAI Proxy")


@app.get("/health")
async def health():
    has_key = bool(os.environ.get("OPENAI_API_KEY"))
    return {"status": "ok" if has_key else "no_api_key", "backend": "openai", "model": "whisper-1"}


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not set")

    try:
        from openai import AsyncOpenAI
    except ImportError:
        raise HTTPException(status_code=500, detail="openai package not installed: pip install openai")

    content = await file.read()
    suffix = Path(file.filename or "audio.wav").suffix or ".wav"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        client = AsyncOpenAI(api_key=api_key)
        with open(tmp_path, "rb") as f:
            result = await client.audio.transcriptions.create(
                model="whisper-1",
                file=(file.filename or f"audio{suffix}", f),
                response_format="text",
            )
        return JSONResponse({"text": result.strip(), "backend": "openai"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        Path(tmp_path).unlink(missing_ok=True)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("STT_PORT", "8202"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
