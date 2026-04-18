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
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from backend.config import settings
from backend.models import Content, Segment, Vocabulary
from backend.services import transcriber, segmenter, phonetics, difficulty, vocabulary as vocab_service
from backend.services.openai_service import explain_segments
from backend.utils.audio import extract_audio_ffmpeg, slice_audio
from backend.utils.text import extract_words

logger = logging.getLogger(__name__)

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
    steps = list(content.steps_json or [])
    for step in steps:
        if step["name"] == step_name:
            step["status"] = status
            step["message"] = message
            break
    content.steps_json = steps
    done = sum(1 for s in steps if s["status"] in ("success", "error"))
    content.progress = int(done / len(steps) * 100) if steps else 0
    db.commit()


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

    try:
        content.status = "processing"
        db.commit()

        # ── Step: extract_audio ──────────────────────────────────────────────
        if not content.audio_path:
            update_step(content, db, "extract_audio", "running")
            try:
                audio_path = await asyncio.to_thread(
                    extract_audio_ffmpeg,
                    content.source_path,
                    settings.uploads_dir,
                )
                content.audio_path = audio_path
                db.commit()
                update_step(content, db, "extract_audio", "success")
            except Exception as e:
                update_step(content, db, "extract_audio", "error", str(e))
                raise
        else:
            # Audio already extracted (YouTube path set before calling us)
            update_step(content, db, "extract_audio", "success")

        # ── Step: transcribe ─────────────────────────────────────────────────
        update_step(content, db, "transcribe", "running")
        try:
            logger.info(f"Transcribing content {content_id}...")
            result = await asyncio.to_thread(transcriber.transcribe, content.audio_path)
            update_step(content, db, "transcribe", "success")
        except Exception as e:
            update_step(content, db, "transcribe", "error", str(e))
            raise

        # ── Step: segment (Phase 1 — Claude semantic cut) ────────────────────
        update_step(content, db, "segment", "running")
        try:
            segments_raw = await asyncio.to_thread(segmenter.cut_into_sentences, result)
            update_step(content, db, "segment", "success", f"共 {len(segments_raw)} 个句子")
            logger.info(f"Content {content_id}: {len(segments_raw)} raw sentence segments")
        except Exception as e:
            update_step(content, db, "segment", "error", str(e))
            raise

        # ── Step: asr_correct (Phase 2 — Claude ASR correction) ──────────────
        update_step(content, db, "asr_correct", "running")
        try:
            segments_data = await asyncio.to_thread(segmenter.apply_asr_correction, segments_raw)
            update_step(content, db, "asr_correct", "success", f"共 {len(segments_data)} 个片段")
            logger.info(f"Content {content_id}: {len(segments_data)} segments after correction")
        except Exception as e:
            update_step(content, db, "asr_correct", "error", str(e))
            raise

        # ── Step: explain (Claude Chinese linguistic explanation) ────────────
        update_step(content, db, "explain", "running")
        try:
            full_text = " ".join(s.text for s in segments_data)
            sentences = [s.text for s in segments_data]
            explanations = await asyncio.to_thread(explain_segments, full_text, sentences)
            for seg_data, expl in zip(segments_data, explanations):
                seg_data.explanation = expl
            update_step(content, db, "explain", "success", f"生成 {len(explanations)} 条讲解")
        except Exception as e:
            update_step(content, db, "explain", "error", str(e))
            raise

        # ── Step: detect_phonetics (+ scoring, per segment) ──────────────────
        update_step(content, db, "detect_phonetics", "running")
        saved_segments: list[Segment] = []
        try:
            total = len(segments_data)
            for idx, seg_data in enumerate(segments_data):
                seg = await _process_segment(idx, seg_data, content, db)
                if seg:
                    saved_segments.append(seg)
                # Granular intra-step progress (40% → 80% of total)
                content.progress = 40 + int((idx + 1) / total * 40) if total else 80
                db.commit()
            update_step(
                content, db, "detect_phonetics", "success",
                f"处理 {len(saved_segments)} 个有效片段",
            )
        except Exception as e:
            update_step(content, db, "detect_phonetics", "error", str(e))
            raise

        # ── Step: vocabulary ─────────────────────────────────────────────────
        update_step(content, db, "vocabulary", "running")
        try:
            vocab_count = await _extract_vocabulary(content, saved_segments, db)
            update_step(content, db, "vocabulary", "success", f"提取 {vocab_count} 个词汇")
        except Exception as e:
            update_step(content, db, "vocabulary", "error", str(e))
            raise

        content.status = "ready"
        content.progress = 100
        db.commit()
        logger.info(f"Content {content_id} processing complete.")

    except Exception as e:
        logger.exception(f"Pipeline failed for content {content_id}: {e}")
        content.status = "error"
        content.error_msg = str(e)
        db.commit()


async def _process_segment(
    idx: int,
    seg_data,
    content: Content,
    db: Session,
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
) -> int:
    """Upsert all words from all segments into the Vocabulary table. Returns word count."""
    all_words: set[str] = set()
    for seg in segments:
        all_words.update(extract_words(seg.text))

    now = datetime.utcnow()
    for word in all_words:
        existing = (
            db.query(Vocabulary)
            .filter_by(user_id=content.user_id, word=word)
            .first()
        )
        if existing:
            existing.encounters += 1
            existing.last_seen = now
        else:
            p = vocab_service.initial_mastery_prob(word)
            db.add(Vocabulary(
                user_id=content.user_id,
                word=word,
                mastery_prob=p,
                encounters=1,
                last_seen=now,
            ))
    db.commit()
    return len(all_words)
