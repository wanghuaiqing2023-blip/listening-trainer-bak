"""FastAPI application entry point."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.database import Base, engine, run_migrations
from backend.routers import content, cards, mastery, user, vocabulary, dictionary

logging.basicConfig(level=logging.INFO)

# Create all database tables, then patch any missing columns
Base.metadata.create_all(bind=engine)
run_migrations()

app = FastAPI(
    title="Listening Trainer API",
    description="Adaptive listening training system based on i+1 principle",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(content.router)
app.include_router(cards.router)
app.include_router(mastery.router)
app.include_router(user.router)
app.include_router(vocabulary.router)
app.include_router(dictionary.router)


# ---------------------------------------------------------------------------
# Audio file serving
# ---------------------------------------------------------------------------

@app.get("/audio/{segment_id}")
def serve_segment_audio(segment_id: int):
    """Serve the audio file for a segment by ID."""
    from backend.database import SessionLocal
    from backend.models import Segment
    db = SessionLocal()
    try:
        seg = db.get(Segment, segment_id)
        if not seg or not seg.audio_path:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Audio not found")
        return FileResponse(seg.audio_path, media_type="audio/wav")
    finally:
        db.close()


@app.get("/audio/tts/{filename}")
def serve_tts_audio(filename: str):
    """Serve a TTS-generated audio file."""
    path = settings.segments_dir / filename
    if not path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="TTS audio not found")
    return FileResponse(str(path), media_type="audio/wav")


@app.get("/audio/stress/{filename}")
def serve_stress_audio(filename: str):
    """Serve a stress-test audio file."""
    path = settings.segments_dir / filename
    if not path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Stress audio not found")
    return FileResponse(str(path), media_type="audio/wav")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/llm")
def health_llm():
    """
    Probe the configured LLM with a minimal request.
    Always returns HTTP 200; check the 'status' field for the result.
    """
    from backend.services.openai_service import _get_client
    try:
        client = _get_client()
        client.messages.create(
            model=settings.anthropic_model,
            max_tokens=5,
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
        )
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}
