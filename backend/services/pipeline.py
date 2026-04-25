"""
Content processing pipeline.

Runs asynchronously after content upload:
  1. Extract audio (ffmpeg)
  2. Transcribe (WhisperX)
  3. Segment (pause-based sentence splitting)
  4. Slice audio per segment (ffmpeg)
  5. Detect phonetic phenomena (Azure Speech phoneme assessment)
  6. Score difficulty (all 5 dimensions)
  7. Extract vocabulary
  8. Save segments and vocabulary to database
"""
from __future__ import annotations

import asyncio
import copy
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from backend.config import settings
from backend.models import Content, Segment, Vocabulary
from backend.services import (
    artifacts as artifact_service,
    difficulty,
    phonetics,
    segmenter,
    transcriber,
    vocabulary as vocab_service,
)
from backend.services.openai_service import explain_segments
from backend.utils.audio import extract_audio_ffmpeg, slice_audio
from backend.utils.text import extract_words

logger = logging.getLogger(__name__)


class PipelinePaused(Exception):
    """Raised when a user pauses a content task at a safe checkpoint."""

# Step definitions per source type
_STEPS_YOUTUBE = [
    ("youtube_download", "下载 YouTube 音频 & 字幕"),
    ("extract_audio",    "提取 WAV 音频"),
    ("transcribe",       "语音转录 (WhisperX)"),
    ("segment",          "语义切割 (AI)"),
    ("asr_correct",      "文字纠错 (AI)"),
    ("explain",          "语言讲解生成 (AI)"),
    ("detect_phonetics", "语音现象检测 & 难度评分"),
    ("vocabulary",       "词汇提取"),
]
_STEPS_FILE = [
    ("extract_audio",    "提取 WAV 音频"),
    ("transcribe",       "语音转录 (WhisperX)"),
    ("segment",          "语义切割 (AI)"),
    ("asr_correct",      "文字纠错 (AI)"),
    ("explain",          "语言讲解生成 (AI)"),
    ("detect_phonetics", "语音现象检测 & 难度评分"),
    ("vocabulary",       "词汇提取"),
]


def init_steps(content: Content, db: Session) -> None:
    """Initialise steps_json for the content based on its source_type."""
    step_defs = _STEPS_YOUTUBE if content.source_type == "youtube" else _STEPS_FILE
    content.steps_json = [
        {"name": name, "label": label, "status": "pending", "message": ""}
        for name, label in step_defs
    ]
    content.progress = 0
    db.commit()


def update_step(
    content: Content,
    db: Session,
    step_name: str,
    status: str,
    message: str = "",
) -> None:
    """Update a single step's status and recalculate overall progress."""
    # Deep-copy nested step dicts before mutation so SQLAlchemy can detect the
    # JSON field change and persist updated step states to SQLite.
    steps = copy.deepcopy(content.steps_json or [])
    for step in steps:
        if step["name"] == step_name:
            step["status"] = status
            step["message"] = message
            break
    content.steps_json = steps
    done = sum(1 for s in steps if s["status"] in ("success", "error"))
    content.progress = int(done / len(steps) * 100) if steps else 0
    db.commit()


def update_running_step_progress(
    content: Content,
    db: Session,
    step_name: str,
    step_percent: float,
    message: str = "",
) -> None:
    """
    Persist intra-step progress so long-running stages do not look frozen.
    """
    steps = copy.deepcopy(content.steps_json or [])
    step_fraction = max(0.0, min(1.0, float(step_percent) / 100.0))
    for step in steps:
        if step["name"] == step_name:
            step["status"] = "running"
            step["message"] = message
            break
    content.steps_json = steps
    done = sum(1 for s in steps if s["status"] in ("success", "error"))
    total = len(steps)
    content.progress = int(((done + step_fraction) / total) * 100) if total else 0
    db.commit()


def pause_if_requested(content_id: int, db: Session) -> None:
    current = db.get(Content, content_id)
    if not current:
        raise RuntimeError("Content not found")
    if current.status != "pause_requested":
        return

    current.status = "paused"
    current.error_msg = ""
    db.commit()
    raise PipelinePaused(f"Content {content_id} paused by user request")


async def process_content(
    content_id: int,
    db: Session,
    subtitle_lines: list[dict] | None = None,
) -> None:
    """Main pipeline entry point. Updates content status and step progress in DB."""
    content = db.get(Content, content_id)
    if not content:
        return

    # Initialize steps if not already done (file uploads; youtube inits them earlier)
    if not content.steps_json:
        init_steps(content, db)

    previous_step_success = {
        step.get("name"): step.get("status") == "success"
        for step in copy.deepcopy(content.steps_json or [])
    }
    previous_run_dir = artifact_service.find_latest_run_dir(content.id)
    artifact_run = artifact_service.ContentArtifactRun.create(
        content_id=content.id,
        title=content.title,
        source_type=content.source_type,
        source_path=content.source_path,
        audio_path=content.audio_path,
    )
    source_copy = artifact_run.copy_file(content.source_path, "cached-source")
    artifact_run.update_summary(
        resumed_from_run_dir=str(previous_run_dir) if previous_run_dir else None,
        cached_source_path=str(source_copy) if source_copy else None,
    )
    step_states: dict[str, dict] = {}

    def step_previously_succeeded(step_name: str) -> bool:
        return previous_step_success.get(step_name, False)

    def load_previous_json(filename: str):
        return artifact_service.read_json(previous_run_dir, filename)

    youtube_subtitles_payload = subtitle_lines
    if youtube_subtitles_payload is None:
        previous_subtitles = load_previous_json("youtube-subtitles.json")
        if isinstance(previous_subtitles, list):
            youtube_subtitles_payload = previous_subtitles

    if content.source_type == "youtube":
        if isinstance(youtube_subtitles_payload, list):
            artifact_run.write_json("youtube-subtitles.json", youtube_subtitles_payload)
        artifact_run.write_json(
            "youtube-download.json",
            {
                "content_id": content.id,
                "source_type": content.source_type,
                "source_path": content.source_path,
                "audio_path": content.audio_path,
                "cached_source_path": str(source_copy) if source_copy else None,
                "subtitle_line_count": len(youtube_subtitles_payload) if isinstance(youtube_subtitles_payload, list) else None,
                "reused": step_previously_succeeded("youtube_download"),
            },
        )

    def restore_segment_data(filename: str) -> list[segmenter.SegmentData] | None:
        payload = load_previous_json(filename)
        if not isinstance(payload, dict):
            return None
        items = payload.get("segments")
        if not isinstance(items, list):
            return None

        restored: list[segmenter.SegmentData] = []
        for item in items:
            if not isinstance(item, dict):
                return None
            restored.append(segmenter.SegmentData(
                text=str(item.get("text", "")),
                start=float(item.get("start", 0.0) or 0.0),
                end=float(item.get("end", 0.0) or 0.0),
                words=list(item.get("words") or []),
                explanation=str(item.get("explanation", "")),
            ))
        return restored

    def record_step_state(step_name: str, status: str, message: str = "") -> None:
        step_states[step_name] = {
            "status": status,
            "message": message,
            "updated_at": datetime.now().isoformat(),
        }
        artifact_run.update_summary(
            status=content.status,
            current_step=step_name,
            current_step_status=status,
            current_step_message=message,
            content_progress=content.progress,
            audio_path=content.audio_path,
            step_states=copy.deepcopy(step_states),
        )

    def record_segmentation_trace(event: dict) -> None:
        event_type = event.get("type")
        attempt = event.get("attempt")
        if event_type == "candidate_boundaries":
            artifact_run.write_json("segment-candidates.json", {
                "token_count": event.get("token_count"),
                "candidate_count": len(event.get("candidate_boundaries", [])),
                "candidate_boundaries": event.get("candidate_boundaries", []),
                "candidate_payload": event.get("candidate_payload", []),
            })
            return

        if event_type == "segments_raw":
            artifact_run.write_json(
                "segments-raw.json",
                {
                    "boundary_count": len(event.get("boundaries", [])),
                    "boundaries": event.get("boundaries", []),
                    "segment_count": len(event.get("segments", [])),
                    "segments": event.get("segments", []),
                },
            )
            return

        if attempt is None:
            return

        if event_type == "prompt":
            artifact_run.write_text(
                f"segment-prompt-attempt-{attempt:02d}.txt",
                event.get("prompt", ""),
            )
        elif event_type == "llm_raw_response":
            artifact_run.write_json(
                f"segment-llm-response-meta-attempt-{attempt:02d}.json",
                {
                    "attempt": attempt,
                    **(event.get("response_meta") or {}),
                },
            )
            artifact_run.write_text(
                f"segment-raw-response-attempt-{attempt:02d}.txt",
                event.get("raw_response", ""),
            )
        elif event_type == "parsed_boundaries":
            boundaries = event.get("boundaries", [])
            artifact_run.write_json(
                f"segment-parsed-boundaries-attempt-{attempt:02d}.json",
                {
                    "attempt": attempt,
                    "boundary_count": len(boundaries),
                    "boundaries": boundaries,
                },
            )
        elif event_type == "validation":
            problems = event.get("problems", [])
            artifact_run.write_json(
                f"segment-validation-attempt-{attempt:02d}.json",
                {
                    "attempt": attempt,
                    "boundaries": event.get("boundaries", []),
                    "problem_count": len(problems),
                    "problem_type_counts": artifact_service.summarize_issue_types(problems),
                    "problems": problems,
                },
            )
    result: dict | None = None
    segments_raw: list[segmenter.SegmentData] = []
    segments_data: list[segmenter.SegmentData] = []

    try:
        content.status = "processing"
        db.commit()
        artifact_run.update_summary(
            status="processing",
            current_step="",
            current_step_status="running",
            current_step_message="",
            content_progress=content.progress,
            audio_path=content.audio_path,
            step_states=copy.deepcopy(step_states),
        )
        pause_if_requested(content_id, db)

        # ── Step: extract_audio ──────────────────────────────────────────────
        if not content.audio_path:
            update_step(content, db, "extract_audio", "running")
            record_step_state("extract_audio", "running")
            try:
                audio_path = await asyncio.to_thread(
                    extract_audio_ffmpeg,
                    content.source_path,
                    settings.uploads_dir,
                )
                content.audio_path = audio_path
                db.commit()
                update_step(content, db, "extract_audio", "success")
                record_step_state("extract_audio", "success")
            except Exception as e:
                update_step(content, db, "extract_audio", "error", str(e))
                record_step_state("extract_audio", "error", str(e))
                raise
        else:
            # Audio already extracted (YouTube path set before calling us)
            update_step(content, db, "extract_audio", "success")
            record_step_state("extract_audio", "success", "复用已有音频")
        audio_copy = artifact_run.copy_file(content.audio_path, "cached-audio")
        artifact_run.write_json(
            "extract-audio.json",
            {
                "content_id": content.id,
                "source_type": content.source_type,
                "source_path": content.source_path,
                "audio_path": content.audio_path,
                "cached_source_path": str(source_copy) if source_copy else None,
                "cached_audio_path": str(audio_copy) if audio_copy else None,
            },
        )
        artifact_run.update_summary(
            audio_path=content.audio_path,
            cached_audio_path=str(audio_copy) if audio_copy else None,
        )
        pause_if_requested(content_id, db)

        # ── Step: transcribe ─────────────────────────────────────────────────
        reused_transcription = False
        if step_previously_succeeded("transcribe"):
            cached_result = load_previous_json("transcribe-whisperx_result.json")
            if isinstance(cached_result, dict):
                result = cached_result
                artifact_run.write_json("transcribe-whisperx_result.json", result)
                try:
                    transcription_warnings = segmenter.validate_transcription_result(result)
                    artifact_run.write_json("transcribe-validation.json", {
                        "ok": True,
                        "warning_count": len(transcription_warnings),
                        "warning_type_counts": artifact_service.summarize_issue_types(transcription_warnings),
                        "warnings": transcription_warnings,
                    })
                except segmenter.SegmentationValidationError as exc:
                    artifact_run.write_json("transcribe-validation.json", {
                        "ok": False,
                        "error_count": len(exc.issues),
                        "error_type_counts": artifact_service.summarize_issue_types(exc.issues),
                        "errors": exc.issues,
                    })
                else:
                    message = "澶嶇敤涓婁竴娆℃垚鍔熺殑杞綍缁撴灉"
                    if transcription_warnings:
                        message += f"锛?{len(transcription_warnings)} 涓?timing warning)"
                    update_step(content, db, "transcribe", "success", message)
                    record_step_state("transcribe", "success", message)
                    reused_transcription = True

        if not reused_transcription:
            update_step(content, db, "transcribe", "running")
            record_step_state("transcribe", "running")
            try:
                logger.info(f"Transcribing content {content_id}...")
                last_transcribe_percent = -1

                def on_transcribe_progress(percent: float, message: str) -> None:
                    nonlocal last_transcribe_percent
                    update_running_step_progress(content, db, "transcribe", percent, message)
                    rounded = int(percent)
                    if rounded == last_transcribe_percent and rounded not in {0, 100}:
                        return
                    last_transcribe_percent = rounded
                    artifact_run.update_summary(
                        status=content.status,
                        current_step="transcribe",
                        current_step_status="running",
                        current_step_message=message,
                        current_step_progress=rounded,
                        content_progress=content.progress,
                        audio_path=content.audio_path,
                        step_states=copy.deepcopy(step_states),
                    )

                result = await asyncio.to_thread(
                    transcriber.transcribe,
                    content.audio_path,
                    on_transcribe_progress,
                )
                artifact_run.write_json("transcribe-whisperx_result.json", result)
                try:
                    transcription_warnings = segmenter.validate_transcription_result(result)
                    artifact_run.write_json("transcribe-validation.json", {
                        "ok": True,
                        "warning_count": len(transcription_warnings),
                        "warning_type_counts": artifact_service.summarize_issue_types(transcription_warnings),
                        "warnings": transcription_warnings,
                    })
                except segmenter.SegmentationValidationError as exc:
                    artifact_run.write_json("transcribe-validation.json", {
                        "ok": False,
                        "error_count": len(exc.issues),
                        "error_type_counts": artifact_service.summarize_issue_types(exc.issues),
                        "errors": exc.issues,
                    })
                    raise
                message = ""
                if transcription_warnings:
                    pass
                message = f"{len(transcription_warnings)} 个 timing warning"
                logger.warning(
                    "Content %s transcription validation warnings: %s",
                    content_id,
                    transcription_warnings,
                )
                update_step(content, db, "transcribe", "success", message)
                record_step_state("transcribe", "success", message)
            except Exception as e:
                update_step(content, db, "transcribe", "error", str(e))
                record_step_state("transcribe", "error", str(e))
                raise
        pause_if_requested(content_id, db)

        # ── Step: segment (Phase 1 — Claude semantic cut) ────────────────────
        update_step(content, db, "segment", "running")
        record_step_state("segment", "running")
        try:
            restored_segments_raw = (
                restore_segment_data("segments-raw.json")
                if step_previously_succeeded("segment")
                else None
            )
            if restored_segments_raw is None and step_previously_succeeded("asr_correct"):
                restored_segments_raw = restore_segment_data("segments-corrected.json")
            if restored_segments_raw is None and step_previously_succeeded("explain"):
                restored_segments_raw = restore_segment_data("segments-explained.json")
            if restored_segments_raw is not None:
                segments_raw = restored_segments_raw
                artifact_run.write_json(
                    "segments-raw.json",
                    {
                        "segment_count": len(segments_raw),
                        "segments": artifact_service.serialize_segment_data_list(segments_raw),
                    },
                )
                segment_message = f"复用 {len(segments_raw)} 个句子片段"
            else:
                segments_raw = await asyncio.to_thread(
                    segmenter.cut_into_sentences,
                    result,
                    record_segmentation_trace,
                )
                segment_message = f"共 {len(segments_raw)} 个句子"
            update_step(content, db, "segment", "success", segment_message)
            record_step_state("segment", "success", f"segment_count={len(segments_raw)}")
            logger.info(f"Content {content_id}: {len(segments_raw)} raw sentence segments")
        except Exception as e:
            update_step(content, db, "segment", "error", str(e))
            record_step_state("segment", "error", str(e))
            raise
        pause_if_requested(content_id, db)

        # ── Step: asr_correct (Phase 2 — Claude ASR correction) ──────────────
        update_step(content, db, "asr_correct", "running")
        record_step_state("asr_correct", "running")
        try:
            restored_segments_data = (
                restore_segment_data("segments-corrected.json")
                if step_previously_succeeded("asr_correct")
                else None
            )
            if restored_segments_data is None and step_previously_succeeded("explain"):
                restored_segments_data = restore_segment_data("segments-explained.json")
            if restored_segments_data is not None:
                segments_data = restored_segments_data
                asr_message = f"复用 {len(segments_data)} 个片段"
            else:
                segments_data = await asyncio.to_thread(segmenter.apply_asr_correction, segments_raw)
                asr_message = f"共 {len(segments_data)} 个片段"
            artifact_run.write_json(
                "segments-corrected.json",
                {
                    "segment_count": len(segments_data),
                    "segments": artifact_service.serialize_segment_data_list(segments_data),
                },
            )
            update_step(content, db, "asr_correct", "success", asr_message)
            record_step_state("asr_correct", "success", f"segment_count={len(segments_data)}")
            logger.info(f"Content {content_id}: {len(segments_data)} segments after correction")
        except Exception as e:
            update_step(content, db, "asr_correct", "error", str(e))
            record_step_state("asr_correct", "error", str(e))
            raise
        pause_if_requested(content_id, db)

        # ── Step: explain (Claude Chinese linguistic explanation) ────────────
        update_step(content, db, "explain", "running")
        record_step_state("explain", "running")
        try:
            restored_explained_segments = (
                restore_segment_data("segments-explained.json")
                if step_previously_succeeded("explain")
                else None
            )
            if restored_explained_segments is not None:
                segments_data = restored_explained_segments
                explanations = [segment.explanation for segment in segments_data]
                explain_message = f"复用 {len(explanations)} 条讲解"
            else:
                full_text = " ".join(s.text for s in segments_data)
                sentences = [s.text for s in segments_data]
                last_explain_percent = -1
                logger.info(
                    "Content %s explain step started total_sentences=%s full_text_chars=%s",
                    content_id,
                    len(sentences),
                    len(full_text),
                )

                def on_explain_progress(
                    completed: int,
                    total: int,
                    batch_index: int,
                    total_batches: int,
                ) -> None:
                    nonlocal last_explain_percent
                    percent = int((completed / total) * 100) if total else 100
                    if percent == last_explain_percent and completed not in {0, total}:
                        return
                    last_explain_percent = percent
                    message = (
                        f"正在生成讲解：已完成 {completed}/{total} 句"
                        f"（第 {batch_index}/{total_batches} 批）"
                    )
                    update_running_step_progress(content, db, "explain", percent, message)
                    artifact_run.update_summary(
                        status=content.status,
                        current_step="explain",
                        current_step_status="running",
                        current_step_message=message,
                        current_step_progress=percent,
                        content_progress=content.progress,
                        audio_path=content.audio_path,
                        step_states=copy.deepcopy(step_states),
                    )
                    logger.info(
                        "Content %s explain progress completed=%s/%s batch=%s/%s percent=%s",
                        content_id,
                        completed,
                        total,
                        batch_index,
                        total_batches,
                        percent,
                    )

                explanations = await asyncio.to_thread(
                    explain_segments,
                    full_text,
                    sentences,
                    on_explain_progress,
                    log_context={"content_id": content_id, "source_type": content.source_type},
                )
                for seg_data, expl in zip(segments_data, explanations):
                    seg_data.explanation = expl
                explain_message = f"生成 {len(explanations)} 条讲解"
            artifact_run.write_json(
                "segments-explained.json",
                {
                    "segment_count": len(segments_data),
                    "segments": artifact_service.serialize_segment_data_list(segments_data),
                },
            )
            update_step(content, db, "explain", "success", explain_message)
            record_step_state("explain", "success", explain_message)
            logger.info(
                "Content %s explain step completed explanation_count=%s",
                content_id,
                len(segments_data),
            )
        except Exception as e:
            update_step(content, db, "explain", "error", str(e))
            record_step_state("explain", "error", str(e))
            raise
        pause_if_requested(content_id, db)

        # ── Step: detect_phonetics (+ scoring, per segment) ──────────────────
        update_step(content, db, "detect_phonetics", "running")
        record_step_state("detect_phonetics", "running")
        saved_segments: list[Segment] = []
        try:
            reused_saved_segments = False
            phonetics_available = True
            phonetics_skip_reason = ""
            if step_previously_succeeded("detect_phonetics"):
                saved_segments = (
                    db.query(Segment)
                    .filter_by(content_id=content.id)
                    .order_by(Segment.index.asc())
                    .all()
                )
                reused_saved_segments = bool(saved_segments)

            if not reused_saved_segments:
                probe_segment = next((seg for seg in segments_data if seg.text.strip()), None)
                probe_audio_path: str | None = None
                if probe_segment is not None:
                    try:
                        probe_audio_path = await asyncio.to_thread(
                            slice_audio,
                            content.audio_path,
                            probe_segment.start,
                            probe_segment.end,
                            settings.segments_dir,
                        )
                        try:
                            phonetics_available, phonetics_skip_reason = await asyncio.wait_for(
                                asyncio.to_thread(
                                    phonetics.check_service_availability,
                                    probe_audio_path,
                                    probe_segment.text,
                                ),
                                timeout=8.0,
                            )
                        except asyncio.TimeoutError:
                            phonetics_available = False
                            phonetics_skip_reason = "Azure Speech probe timed out after 8 seconds"
                    except Exception as exc:
                        phonetics_available = False
                        phonetics_skip_reason = f"Azure Speech probe failed: {exc}"
                    finally:
                        if probe_audio_path:
                            try:
                                Path(probe_audio_path).unlink(missing_ok=True)
                            except OSError:
                                pass

                if not phonetics_available:
                    logger.warning(
                        "Content %s skipping phonetics detection for the whole step: %s",
                        content_id,
                        phonetics_skip_reason,
                    )
                    skip_message = f"Azure Speech 不可用，已跳过语音现象检测：{phonetics_skip_reason}"
                    update_step(content, db, "detect_phonetics", "running", skip_message)
                    record_step_state("detect_phonetics", "running", skip_message)
                else:
                    update_running_step_progress(
                        content,
                        db,
                        "detect_phonetics",
                        0,
                        f"正在处理音频片段：0/{len(segments_data)}（含语音现象检测）",
                    )

                total = len(segments_data)
                for idx, seg_data in enumerate(segments_data):
                    pause_if_requested(content_id, db)
                    seg = await _process_segment(
                        idx,
                        seg_data,
                        content,
                        db,
                        skip_phonetics=not phonetics_available,
                    )
                    if seg:
                        saved_segments.append(seg)
                # Granular intra-step progress (40% → 80% of total)
                    completed = idx + 1
                    percent = int((completed / total) * 100) if total else 100
                    detail = (
                        "已跳过语音现象检测，正在切片与评估难度"
                        if not phonetics_available
                        else "正在检测语音现象与评估难度"
                    )
                    update_running_step_progress(
                        content,
                        db,
                        "detect_phonetics",
                        percent,
                        f"正在处理音频片段：{completed}/{total}（{detail}）",
                    )
            artifact_run.write_json(
                "segments-detected.json",
                {
                    "content_id": content.id,
                    "segment_count": len(saved_segments),
                    "reused": reused_saved_segments,
                    "phonetics_available": phonetics_available,
                    "phonetics_skip_reason": phonetics_skip_reason,
                    "segments": artifact_service.serialize_saved_segments(saved_segments),
                },
            )
            success_message = f"处理 {len(saved_segments)} 个有效片段"
            if not phonetics_available and not reused_saved_segments:
                success_message += "（已快速跳过语音现象检测）"
            update_step(
                content, db, "detect_phonetics", "success",
                success_message,
            )
            record_step_state(
                "detect_phonetics",
                "success",
                f"segment_count={len(saved_segments)}; phonetics_available={phonetics_available}",
            )
        except Exception as e:
            update_step(content, db, "detect_phonetics", "error", str(e))
            record_step_state("detect_phonetics", "error", str(e))
            raise
        pause_if_requested(content_id, db)

        # ── Step: vocabulary ─────────────────────────────────────────────────
        update_step(content, db, "vocabulary", "running")
        record_step_state("vocabulary", "running")
        try:
            def on_vocabulary_progress(completed: int, total: int) -> None:
                percent = int((completed / total) * 100) if total else 100
                update_running_step_progress(
                    content,
                    db,
                    "vocabulary",
                    percent,
                    f"正在提取词汇：{completed}/{total}",
                )

            vocabulary_result = await _extract_vocabulary(
                content,
                saved_segments,
                db,
                progress_callback=on_vocabulary_progress,
            )
            artifact_run.write_json("vocabulary-result.json", vocabulary_result)
            vocab_count = vocabulary_result.get("word_count", 0)
            update_step(content, db, "vocabulary", "success", f"提取 {vocab_count} 个词汇")
            record_step_state("vocabulary", "success", f"word_count={vocab_count}")
        except Exception as e:
            update_step(content, db, "vocabulary", "error", str(e))
            record_step_state("vocabulary", "error", str(e))
            raise

        content.status = "ready"
        content.progress = 100
        db.commit()
        artifact_run.update_summary(
            status="ready",
            current_step="vocabulary",
            current_step_status="success",
            current_step_message="",
            current_step_progress=100,
            content_progress=100,
            audio_path=content.audio_path,
            step_states=copy.deepcopy(step_states),
            error="",
            finished_at=datetime.now().isoformat(),
        )
        logger.info(f"Content {content_id} processing complete.")

    except PipelinePaused as e:
        logger.info(str(e))
        artifact_run.update_summary(
            status="paused",
            content_progress=content.progress,
            audio_path=content.audio_path,
            step_states=copy.deepcopy(step_states),
            error="",
            finished_at=None,
        )
    except Exception as e:
        logger.exception(f"Pipeline failed for content {content_id}: {e}")
        content.status = "error"
        content.error_msg = str(e)
        db.commit()
        artifact_run.update_summary(
            status="error",
            content_progress=content.progress,
            audio_path=content.audio_path,
            step_states=copy.deepcopy(step_states),
            error=str(e),
            finished_at=datetime.now().isoformat(),
        )


async def _process_segment(
    idx: int,
    seg_data,
    content: Content,
    db: Session,
    *,
    skip_phonetics: bool = False,
) -> Segment | None:
    """Slice audio, detect phenomena, score difficulty. Returns saved Segment."""
    text = seg_data.text.strip()
    if not text:
        return None

    # Slice audio
    audio_path = await asyncio.to_thread(
        slice_audio,
        content.audio_path,
        seg_data.start,
        seg_data.end,
        settings.segments_dir,
    )

    # Detect phonetic phenomena (Azure Speech)
    if skip_phonetics:
        annotations = []
        phenomena_count = 0
    else:
        try:
            phenomena_list = await asyncio.to_thread(
                phonetics.detect_phenomena,
                audio_path,
                text,
                seg_data.words,
            )
            annotations = phonetics.phenomena_to_annotations(phenomena_list)
            phenomena_count = len(phenomena_list)
        except Exception as e:
            logger.warning(f"Phonetics detection failed for segment {idx}: {e}")
            annotations = []
            phenomena_count = 0

    # Score difficulty
    diff_scores = await asyncio.to_thread(
        difficulty.compute_difficulty,
        seg_data.words,
        text,
        audio_path,
        phenomena_count,
        not skip_phonetics,
        None,  # no user vocab at ingestion time
    )

    # Save segment
    segment = Segment(
        content_id=content.id,
        index=idx,
        text=text,
        start_time=seg_data.start,
        end_time=seg_data.end,
        audio_path=audio_path,
        diff_speech_rate=diff_scores["speech_rate"],
        diff_phonetics=diff_scores["phonetics"],
        diff_vocabulary=diff_scores["vocabulary"],
        diff_complexity=diff_scores["complexity"],
        diff_audio_quality=diff_scores["audio_quality"],
        diff_total=diff_scores["total"],
        phonetic_annotations=annotations,
        word_timestamps=seg_data.words,
        explanation=getattr(seg_data, "explanation", ""),
    )
    db.add(segment)
    db.flush()  # get segment.id
    return segment


async def _extract_vocabulary(
    content: Content,
    segments: list[Segment],
    db: Session,
    progress_callback=None,
) -> dict:
    """Upsert all words from all segments into the Vocabulary table and return an artifact payload."""
    all_words: set[str] = set()
    for seg in segments:
        all_words.update(extract_words(seg.text))

    sorted_words = sorted(all_words)
    now = datetime.utcnow()
    touched_entries: list[Vocabulary] = []
    total_words = len(sorted_words)
    if progress_callback:
        progress_callback(0, total_words)
    for word in sorted_words:
        existing = (
            db.query(Vocabulary)
            .filter_by(user_id=content.user_id, word=word)
            .first()
        )
        if existing:
            existing.encounters += 1
            existing.last_seen = now
            touched_entries.append(existing)
        else:
            p = vocab_service.initial_mastery_prob(word)
            entry = Vocabulary(
                user_id=content.user_id,
                word=word,
                mastery_prob=p,
                encounters=1,
                last_seen=now,
            )
            db.add(entry)
            touched_entries.append(entry)
        if progress_callback:
            progress_callback(len(touched_entries), total_words)
    db.commit()
    return {
        "content_id": content.id,
        "user_id": content.user_id,
        "word_count": len(sorted_words),
        "words": sorted_words,
        "entries": artifact_service.serialize_vocabulary_entries(touched_entries),
    }
