"""Content upload and YouTube submission endpoints."""
from __future__ import annotations

import asyncio
import copy
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, BackgroundTasks
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import SessionLocal, get_db
from backend.models import Content, DictationAttempt, Segment, User, UserCard
from backend.services import artifacts as artifact_service, pipeline
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


def _serialize_content(content: Content) -> dict:
    return {
        "id": content.id,
        "status": content.status,
        "title": content.title,
        "error": content.error_msg,
        "segment_count": len(content.segments),
        "steps": content.steps_json or [],
        "progress": content.progress or 0,
        "source_type": content.source_type,
        "created_at": content.created_at.isoformat(),
    }


def _safe_unlink(path_str: str | None) -> None:
    if not path_str:
        return
    try:
        path = Path(path_str)
        if path.exists() and path.is_file():
            path.unlink()
    except OSError:
        pass


def _clear_segment_outputs(content: Content, db: Session) -> None:
    segment_ids = [segment.id for segment in content.segments if segment.id is not None]
    for segment in list(content.segments):
        _safe_unlink(segment.audio_path)

    if segment_ids:
        db.query(DictationAttempt).filter(DictationAttempt.segment_id.in_(segment_ids)).delete(
            synchronize_session=False
        )
        db.query(UserCard).filter(UserCard.segment_id.in_(segment_ids)).delete(
            synchronize_session=False
        )

    for segment in list(content.segments):
        db.delete(segment)


def _clear_audio_output(content: Content) -> None:
    _safe_unlink(content.audio_path)
    content.audio_path = ""


def _delete_generated_outputs(content: Content, db: Session, remove_source: bool = False) -> None:
    _clear_segment_outputs(content, db)
    _clear_audio_output(content)

    if remove_source:
        _safe_unlink(content.source_path)


def _get_step_order(content: Content) -> list[str]:
    if content.steps_json:
        return [step.get("name") for step in content.steps_json if step.get("name")]

    step_defs = pipeline._STEPS_YOUTUBE if content.source_type == "youtube" else pipeline._STEPS_FILE
    return [name for name, _label in step_defs]


def _artifact_root(content: Content) -> Path:
    return artifact_service.get_content_artifact_root(content.id)


def _remove_artifact_patterns(content: Content, patterns: list[str]) -> None:
    root = _artifact_root(content)
    if not root.exists():
        return

    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file():
                _safe_unlink(str(path))


def _clear_outputs_from_resume_step(content: Content, db: Session, resume_step: str | None) -> None:
    step_order = _get_step_order(content)
    step_index = {name: index for index, name in enumerate(step_order)}
    resume_index = step_index.get(resume_step or "", len(step_order))

    def should_clear(step_name: str) -> bool:
        target_index = step_index.get(step_name)
        if target_index is None:
            return False
        return resume_index <= target_index

    if should_clear("detect_phonetics"):
        _clear_segment_outputs(content, db)

    if should_clear("extract_audio"):
        _clear_audio_output(content)

    artifact_patterns_by_step: dict[str, list[str]] = {
        "youtube_download": [
            "youtube-download.json",
            "youtube-subtitles.json",
        ],
        "extract_audio": [
            "extract-audio.json",
            "cached-audio.*",
        ],
        "transcribe": [
            "transcribe-whisperx_result.json",
            "transcribe-validation.json",
        ],
        "segment": [
            "segment-candidates.json",
            "segment-prompt-attempt-*.txt",
            "segment-llm-response-meta-attempt-*.json",
            "segment-raw-response-attempt-*.txt",
            "segment-parsed-boundaries-attempt-*.json",
            "segment-validation-attempt-*.json",
            "segments-raw.json",
        ],
        "asr_correct": [
            "segments-corrected.json",
        ],
        "explain": [
            "segments-explained.json",
        ],
        "detect_phonetics": [
            "segments-detected.json",
        ],
        "vocabulary": [
            "vocabulary-result.json",
        ],
    }

    for step_name, patterns in artifact_patterns_by_step.items():
        if should_clear(step_name):
            _remove_artifact_patterns(content, patterns)


def _find_resume_step_name(content: Content) -> str | None:
    run_dir = artifact_service.find_latest_run_dir(content.id)

    def has_artifact(filename: str) -> bool:
        return run_dir is not None and (run_dir / filename).exists()

    if has_artifact("segments-detected.json"):
        return "vocabulary"
    if has_artifact("segments-explained.json"):
        return "detect_phonetics"
    if has_artifact("segments-corrected.json"):
        return "explain"
    if has_artifact("segments-raw.json"):
        return "asr_correct"
    if has_artifact("transcribe-whisperx_result.json"):
        return "segment"
    if content.audio_path or has_artifact("extract-audio.json"):
        return "transcribe"

    steps = content.steps_json or []
    for step in steps:
        if step.get("status") != "success":
            return step.get("name")
    if steps:
        return steps[-1].get("name")
    return "youtube_download" if content.source_type == "youtube" else "extract_audio"


def _prepare_content_for_resume(content: Content, db: Session) -> str:
    resume_step = _find_resume_step_name(content)
    steps = copy.deepcopy(content.steps_json or [])
    step_order = _get_step_order(content)
    _clear_outputs_from_resume_step(content, db, resume_step)

    if steps:
        if resume_step in step_order:
            resetting = False
            for step in steps:
                if not resetting and step.get("name") == resume_step:
                    resetting = True
                if resetting:
                    step["status"] = "pending"
                    step["message"] = ""
                else:
                    step["status"] = "success"
        else:
            resetting = False
            for step in steps:
                if not resetting and step.get("status") != "success":
                    resetting = True
                if resetting:
                    step["status"] = "pending"
                    step["message"] = ""

        if not any(step.get("status") == "pending" for step in steps):
            steps[-1]["status"] = "pending"
            steps[-1]["message"] = ""
    content.steps_json = steps

    success_count = sum(1 for step in content.steps_json or [] if step.get("status") == "success")
    total_steps = len(content.steps_json or [])
    content.status = "processing"
    content.error_msg = ""
    content.progress = int(success_count / total_steps * 100) if total_steps else 0
    db.commit()
    return resume_step or ""


def mark_interrupted_contents() -> None:
    """
    Any in-flight content tasks are lost on backend restart because they run in
    process-local background workers. Mark them as retryable errors on boot.
    """
    db = SessionLocal()
    try:
        interrupted = (
            db.query(Content)
            .filter(Content.status.in_(["processing", "pause_requested"]))
            .all()
        )
        changed = False
        for content in interrupted:
            content.status = "error"
            if not content.error_msg:
                content.error_msg = "任务因服务重启而中断，请手动重试。"
            steps = copy.deepcopy(content.steps_json or [])
            for step in steps:
                if step.get("status") == "running":
                    step["status"] = "error"
                    step["message"] = "任务因服务重启而中断，请手动重试。"
            content.steps_json = steps
            changed = True
        if changed:
            db.commit()
    finally:
        db.close()


def _mark_stale_pending_processing_contents(db: Session) -> None:
    cutoff = datetime.utcnow() - timedelta(minutes=30)
    contents = (
        db.query(Content)
        .filter(Content.status == "processing", Content.progress == 0, Content.created_at < cutoff)
        .all()
    )
    changed = False
    for content in contents:
        steps = copy.deepcopy(content.steps_json or [])
        if not steps or any(step.get("status") != "pending" for step in steps):
            continue
        content.status = "error"
        content.error_msg = "任务长时间未启动，已标记为失败，请手动重试。"
        changed = True
    if changed:
        db.commit()


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

    background_tasks.add_task(_run_pipeline_youtube_resume_aware, content.id, url)
    return {"id": content.id, "status": "processing", "title": placeholder_title}


@router.get("/{content_id}/status")
def get_status(content_id: int, db: Session = Depends(get_db)):
    """Poll content processing status, including step-by-step pipeline progress."""
    _mark_stale_pending_processing_contents(db)
    content = db.get(Content, content_id)
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")
    return _serialize_content(content)


@router.get("/")
def list_contents(db: Session = Depends(get_db)):
    """List all uploaded content for the current single-user workspace."""
    user = _get_or_create_user(db)
    _mark_stale_pending_processing_contents(db)
    contents = db.query(Content).filter_by(user_id=user.id).order_by(Content.created_at.desc()).all()
    return [_serialize_content(c) for c in contents]


@router.post("/{content_id}/pause")
def pause_content(content_id: int, db: Session = Depends(get_db)):
    content = db.get(Content, content_id)
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")
    if content.status != "processing":
        raise HTTPException(status_code=400, detail="Only processing tasks can be paused")

    content.status = "pause_requested"
    db.commit()
    db.refresh(content)
    return _serialize_content(content)


@router.post("/{content_id}/restart")
def restart_content(
    content_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    content = db.get(Content, content_id)
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")
    if content.status not in {"error", "paused"}:
        raise HTTPException(status_code=400, detail="Only paused or failed tasks can be restarted")

    _prepare_content_for_resume(content, db)
    if content.source_type == "youtube":
        background_tasks.add_task(_run_pipeline_youtube_resume_aware, content.id, content.source_path)
    else:
        background_tasks.add_task(_run_pipeline, content.id)

    db.refresh(content)
    return _serialize_content(content)


@router.delete("/{content_id}")
def delete_content(content_id: int, db: Session = Depends(get_db)):
    content = db.get(Content, content_id)
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")
    if content.status in {"processing", "pause_requested"}:
        raise HTTPException(status_code=400, detail="Pause the task before deleting it")

    _delete_generated_outputs(content, db, remove_source=(content.source_type == "file"))
    db.delete(content)
    db.commit()
    return {"ok": True, "id": content_id}


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

    except Exception as e:
        content = db.get(Content, content_id)
        if content:
            pipeline.update_step(content, db, "youtube_download", "error", str(e))
            content.status = "error"
            content.error_msg = str(e)
            db.commit()
        return

    try:
        # Hand off to the shared pipeline (extract_audio onwards).
        # Downstream failures are handled inside process_content and
        # should keep their original step attribution.
        asyncio.run(pipeline.process_content(content_id, db, subtitle_lines))
    finally:
        db.close()


def _run_pipeline_youtube_resume_aware(content_id: int, url: str):
    from backend.database import SessionLocal
    db = SessionLocal()
    try:
        content = db.get(Content, content_id)
        if not content:
            return

        steps = content.steps_json or []
        youtube_download_done = any(
            step.get("name") == "youtube_download" and step.get("status") == "success"
            for step in steps
        )

        if not steps:
            real_title = get_youtube_title(url)
            content.title = real_title
            db.commit()
            pipeline.init_steps(content, db)

        subtitle_lines = None
        if not youtube_download_done:
            pipeline.update_step(content, db, "youtube_download", "running")
            audio_path, subtitle_lines = download_youtube_audio_and_subs(url)
            content.audio_path = audio_path
            db.commit()
            sub_msg = f"字幕 {len(subtitle_lines)} 行" if subtitle_lines else "无字幕，使用转录"
            pipeline.update_step(content, db, "youtube_download", "success", sub_msg)

    except Exception as e:
        content = db.get(Content, content_id)
        if content:
            pipeline.update_step(content, db, "youtube_download", "error", str(e))
            content.status = "error"
            content.error_msg = str(e)
            db.commit()
        return

    try:
        asyncio.run(pipeline.process_content(content_id, db, subtitle_lines))
    finally:
        db.close()
