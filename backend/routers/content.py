"""Content upload and YouTube submission endpoints."""
from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, BackgroundTasks
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db
from backend.models import Content, User
from backend.services import pipeline
from backend.services.youtube import download_youtube_audio_and_subs, get_youtube_title

router = APIRouter(prefix="/content", tags=["content"])


def _get_or_create_user(db: Session) -> User:
    """For now, single-user mode: always return user id=1."""
    user = db.query(User).filter_by(id=1).first()
    if not user:
        user = User(id=1, name="default")
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


@router.post("/upload")
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload an audio or video file for processing."""
    user = _get_or_create_user(db)

    # Save uploaded file
    suffix = Path(file.filename).suffix or ".mp4"
    dest = settings.uploads_dir / f"{uuid.uuid4().hex}{suffix}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    content = Content(
        user_id=user.id,
        title=file.filename,
        source_type="file",
        source_path=str(dest),
        status="processing",
    )
    db.add(content)
    db.commit()
    db.refresh(content)

    background_tasks.add_task(_run_pipeline, content.id)
    return {"id": content.id, "status": "processing", "title": file.filename}


@router.post("/youtube")
async def submit_youtube(
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    db: Session = Depends(get_db),
):
    """Submit a YouTube URL for download and processing."""
    user = _get_or_create_user(db)

    # Use URL as placeholder title immediately; pipeline will update it
    # Do NOT call get_youtube_title() here — it blocks the event loop
    placeholder_title = url.split("v=")[-1][:32] if "v=" in url else "YouTube Video"

    content = Content(
        user_id=user.id,
        title=placeholder_title,
        source_type="youtube",
        source_path=url,
        status="processing",
    )
    db.add(content)
    db.commit()
    db.refresh(content)

    background_tasks.add_task(_run_pipeline_youtube, content.id, url)
    return {"id": content.id, "status": "processing", "title": placeholder_title}


@router.get("/{content_id}/status")
def get_status(content_id: int, db: Session = Depends(get_db)):
    """Poll content processing status, including step-by-step pipeline progress."""
    content = db.get(Content, content_id)
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")
    return {
        "id": content.id,
        "status": content.status,
        "title": content.title,
        "error": content.error_msg,
        "segment_count": len(content.segments),
        "steps": content.steps_json or [],
        "progress": content.progress or 0,
    }


@router.get("/")
def list_contents(db: Session = Depends(get_db)):
    """List all uploaded content."""
    user = _get_or_create_user(db)
    contents = db.query(Content).filter_by(user_id=user.id).order_by(Content.created_at.desc()).all()
    return [
        {
            "id": c.id,
            "title": c.title,
            "source_type": c.source_type,
            "status": c.status,
            "segment_count": len(c.segments),
            "steps": c.steps_json or [],
            "progress": c.progress or 0,
            "error": c.error_msg,
            "created_at": c.created_at.isoformat(),
        }
        for c in contents
    ]


# ── Background task helpers (each needs its own DB session) ───────────────────

def _run_pipeline(content_id: int):
    from backend.database import SessionLocal
    db = SessionLocal()
    try:
        asyncio.run(pipeline.process_content(content_id, db))
    finally:
        db.close()


def _run_pipeline_youtube(content_id: int, url: str):
    from backend.database import SessionLocal
    db = SessionLocal()
    try:
        content = db.get(Content, content_id)

        # Fetch real title (blocking is fine here — we're in a background thread)
        real_title = get_youtube_title(url)
        content.title = real_title
        db.commit()

        # Initialise all steps (including youtube_download) before starting
        pipeline.init_steps(content, db)

        # Step: youtube_download (audio + subtitles)
        pipeline.update_step(content, db, "youtube_download", "running")
        audio_path, subtitle_lines = download_youtube_audio_and_subs(url)
        content.audio_path = audio_path
        db.commit()
        sub_msg = f"字幕 {len(subtitle_lines)} 行" if subtitle_lines else "无字幕，使用转录"
        pipeline.update_step(content, db, "youtube_download", "success", sub_msg)

        # Hand off to the shared pipeline (extract_audio onwards)
        asyncio.run(pipeline.process_content(content_id, db, subtitle_lines))

    except Exception as e:
        content = db.get(Content, content_id)
        if content:
            pipeline.update_step(content, db, "youtube_download", "error", str(e))
            content.status = "error"
            content.error_msg = str(e)
            db.commit()
    finally:
        db.close()
