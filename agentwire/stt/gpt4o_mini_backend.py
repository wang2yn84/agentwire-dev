#!/usr/bin/env python3
"""gpt-4o-mini-transcribe proxy — context-aware, $0.003/min.

Run via: agentwire stt gpt4o-start  (or directly: python gpt4o_mini_backend.py)
"""

import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

app = FastAPI(title="AgentWire STT gpt-4o-mini-transcribe Proxy")


@app.get("/health")
async def health():
    has_key = bool(os.environ.get("OPENAI_API_KEY"))
    return {"status": "ok" if has_key else "no_api_key", "backend": "gpt4o-mini", "model": "gpt-4o-mini-transcribe"}


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
                model="gpt-4o-mini-transcribe",
                file=(file.filename or f"audio{suffix}", f),
                response_format="text",
            )
        return JSONResponse({"text": result.strip(), "backend": "gpt4o-mini"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        Path(tmp_path).unlink(missing_ok=True)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("STT_PORT", "8203"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
