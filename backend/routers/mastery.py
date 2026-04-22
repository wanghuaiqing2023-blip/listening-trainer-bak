"""
Mastery verification four gates:
  Gate 1: Shadowing Match    (Azure Speech pronunciation assessment)
  Gate 2: Generalization Test (GPT new sentence + Azure TTS + dictation)
  Gate 3: Stress Test        (noise or speed)
  Gate 4: SRS time check     (handled by SRS scheduler, not an endpoint)
"""
from __future__ import annotations

import random
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db
from backend.models import DictationAttempt, Segment, UserCard, User, Vocabulary
from backend.services.azure_speech import assess_pronunciation, synthesize_speech
from backend.services.openai_service import generate_generalization_sentence, evaluate_dictation
from backend.services.srs import review_card
from backend.services.vocabulary import update_mastery_prob
from backend.utils.audio import change_speed, add_noise
from backend.utils.text import analyze_dictation
from datetime import datetime

router = APIRouter(prefix="/mastery", tags=["mastery"])

SHADOW_PASS_THRESHOLD = 90.0   # Azure pronunciation score >= 90
SHADOW_STREAK_REQUIRED = 3


def _get_user(db: Session) -> User:
    user = db.query(User).filter_by(id=1).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _get_or_create_card(user_id: int, segment_id: int, db: Session) -> UserCard:
    card = db.query(UserCard).filter_by(user_id=user_id, segment_id=segment_id).first()
    if not card:
        card = UserCard(user_id=user_id, segment_id=segment_id)
        db.add(card)
        db.commit()
        db.refresh(card)
    return card


# ---------------------------------------------------------------------------
# Dictation discovery
# ---------------------------------------------------------------------------

@router.post("/dictation/check")
async def check_dictation(
    segment_id: int = Form(...),
    user_text: str = Form(...),
    db: Session = Depends(get_db),
):
    """
    Analyze a user's free dictation against the segment reference text.
    Returns detailed word-level errors and persists the attempt for profiling.
    """
    user = _get_user(db)
    seg = db.get(Segment, segment_id)
    if not seg:
        raise HTTPException(status_code=404, detail="Segment not found")

    analysis = analyze_dictation(seg.text or "", user_text or "")

    attempt = DictationAttempt(
        user_id=user.id,
        segment_id=segment_id,
        reference_text=seg.text or "",
        user_text=user_text or "",
        accuracy=analysis["accuracy"],
        correct_word_count=analysis["correct_word_count"],
        reference_word_count=analysis["reference_word_count"],
        error_count=analysis["error_count"],
        analysis_json=analysis,
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)

    return {
        **analysis,
        "attempt_id": attempt.id,
    }


# ---------------------------------------------------------------------------
# Gate 1: Shadowing
# ---------------------------------------------------------------------------

@router.post("/shadow")
async def submit_shadow(
    segment_id: int = Form(...),
    audio: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Evaluate user's shadowing recording against the reference segment text.
    Returns Azure pronunciation scores and pass/fail.
    """
    user = _get_user(db)
    seg = db.get(Segment, segment_id)
    if not seg:
        raise HTTPException(status_code=404, detail="Segment not found")

    # Save uploaded audio
    tmp_path = settings.uploads_dir / f"shadow_{uuid.uuid4().hex}.wav"
    with tmp_path.open("wb") as f:
        shutil.copyfileobj(audio.file, f)

    try:
        assessment = assess_pronunciation(str(tmp_path), seg.text)
    finally:
        tmp_path.unlink(missing_ok=True)

    passed = assessment["pronunciation_score"] >= SHADOW_PASS_THRESHOLD

    # Update card
    card = _get_or_create_card(user.id, segment_id, db)
    card.total_attempts += 1
    if passed:
        card.shadow_streak += 1
        card.correct_attempts += 1
    else:
        card.shadow_streak = 0

    gate1_complete = card.shadow_streak >= SHADOW_STREAK_REQUIRED

    db.commit()

    return {
        "passed": passed,
        "streak": card.shadow_streak,
        "required_streak": SHADOW_STREAK_REQUIRED,
        "gate1_complete": gate1_complete,
        "assessment": assessment,
    }


# ---------------------------------------------------------------------------
# Gate 2: Generalization Test
# ---------------------------------------------------------------------------

@router.post("/generalize/generate")
async def generate_generalization(
    segment_id: int = Form(...),
    db: Session = Depends(get_db),
):
    """
    Generate a new sentence based on the card's phonetic phenomena
    and synthesize it with Azure TTS. Returns audio URL and stores
    the generated sentence server-side for later validation.
    """
    user = _get_user(db)
    seg = db.get(Segment, segment_id)
    if not seg:
        raise HTTPException(status_code=404, detail="Segment not found")

    card = _get_or_create_card(user.id, segment_id, db)
    if card.shadow_streak < SHADOW_STREAK_REQUIRED:
        raise HTTPException(status_code=400, detail="Complete Gate 1 (shadowing) first")

    # Gather all phenomena from annotations
    all_phenomena = []
    for ann in (seg.phonetic_annotations or []):
        all_phenomena.extend(ann.get("phenomena", []))

    if not all_phenomena:
        # No phenomena detected — skip generalization, auto-pass
        card.gen_passed = True
        db.commit()
        return {"skipped": True, "reason": "No phonetic phenomena detected"}

    new_sentence = generate_generalization_sentence(seg.text, all_phenomena)

    # Synthesize with Azure TTS
    tts_path = synthesize_speech(new_sentence, settings.segments_dir)

    # Store the generated sentence temporarily in card (use a JSON field trick via notes)
    # We use a simple file-based cache keyed by segment_id
    cache_file = settings.segments_dir / f"gen_cache_{segment_id}.txt"
    cache_file.write_text(new_sentence, encoding="utf-8")

    tts_filename = Path(tts_path).name
    return {
        "audio_url": f"/audio/tts/{tts_filename}",
        "phenomena": all_phenomena,
    }


@router.post("/generalize/submit")
async def submit_generalization(
    segment_id: int = Form(...),
    user_text: str = Form(...),
    db: Session = Depends(get_db),
):
    """Submit user's dictation of the generated sentence."""
    user = _get_user(db)
    seg = db.get(Segment, segment_id)
    if not seg:
        raise HTTPException(status_code=404, detail="Segment not found")

    cache_file = settings.segments_dir / f"gen_cache_{segment_id}.txt"
    if not cache_file.exists():
        raise HTTPException(status_code=400, detail="No pending generalization test")

    reference = cache_file.read_text(encoding="utf-8").strip()

    all_phenomena = []
    for ann in (seg.phonetic_annotations or []):
        all_phenomena.extend(ann.get("phenomena", []))

    eval_result = evaluate_dictation(reference, user_text, all_phenomena)

    card = _get_or_create_card(user.id, segment_id, db)
    if eval_result.get("correct"):
        card.gen_passed = True
        cache_file.unlink(missing_ok=True)

        # Update vocabulary mastery for words in this sentence
        from backend.utils.text import extract_words
        for word in extract_words(reference):
            vocab = db.query(Vocabulary).filter_by(user_id=user.id, word=word).first()
            if vocab:
                vocab.mastery_prob = update_mastery_prob(
                    vocab.mastery_prob, correct=True,
                    last_seen=vocab.last_seen,
                )
                vocab.correct_count += 1
                vocab.last_seen = datetime.utcnow()

    db.commit()

    return {
        "correct": eval_result.get("correct", False),
        "score": eval_result.get("score", 0.0),
        "feedback": eval_result.get("feedback", ""),
        "reference": reference,
        "gate2_complete": card.gen_passed,
    }


# ---------------------------------------------------------------------------
# Gate 3: Stress Test
# ---------------------------------------------------------------------------

@router.post("/stress/generate")
async def generate_stress_test(
    segment_id: int = Form(...),
    db: Session = Depends(get_db),
):
    """Generate a stress-test variant of the segment audio (noise or speed)."""
    user = _get_user(db)
    seg = db.get(Segment, segment_id)
    if not seg:
        raise HTTPException(status_code=404, detail="Segment not found")

    card = _get_or_create_card(user.id, segment_id, db)
    if not card.gen_passed:
        raise HTTPException(status_code=400, detail="Complete Gate 2 first")

    stress_type = random.choice(["noise", "speed"])

    if stress_type == "noise":
        out_path = add_noise(seg.audio_path, snr_db=10.0, output_dir=settings.segments_dir)
        description = "Added background noise (SNR 10dB)"
    else:
        out_path = change_speed(seg.audio_path, speed=1.5, output_dir=settings.segments_dir)
        description = "Increased playback speed to 1.5x"

    filename = Path(out_path).name
    return {
        "audio_url": f"/audio/stress/{filename}",
        "stress_type": stress_type,
        "description": description,
    }


@router.post("/stress/submit")
async def submit_stress_test(
    segment_id: int = Form(...),
    user_text: str = Form(...),
    db: Session = Depends(get_db),
):
    """Evaluate user's response to the stress test."""
    user = _get_user(db)
    seg = db.get(Segment, segment_id)
    if not seg:
        raise HTTPException(status_code=404, detail="Segment not found")

    analysis = analyze_dictation(seg.text or "", user_text or "")
    accuracy = analysis["accuracy"]
    passed = accuracy >= 0.85

    card = _get_or_create_card(user.id, segment_id, db)
    if passed:
        card.stress_passed = True

    # Apply SRS review
    quality = 5 if accuracy >= 0.95 else 4 if accuracy >= 0.85 else 2
    srs_result = review_card(
        state=card.state,
        interval_days=card.interval_days,
        ease_factor=card.ease_factor,
        shadow_streak=card.shadow_streak,
        gen_passed=card.gen_passed,
        stress_passed=card.stress_passed,
        quality=quality,
    )
    card.state = srs_result.new_state
    card.interval_days = srs_result.new_interval
    card.ease_factor = srs_result.new_ease
    card.next_review = srs_result.next_review
    card.last_reviewed = datetime.utcnow()

    db.commit()

    return {
        "passed": passed,
        "accuracy": round(accuracy, 3),
        "analysis": analysis,
        "gate3_complete": card.stress_passed,
        "card_state": card.state,
        "next_review": card.next_review.isoformat() if card.next_review else None,
    }


# ---------------------------------------------------------------------------
# Training review (SRS)
# ---------------------------------------------------------------------------

@router.post("/review")
async def submit_review(
    segment_id: int = Form(...),
    quality: int = Form(...),  # 0-5
    db: Session = Depends(get_db),
):
    """Submit a review result for a card in the SRS review stage."""
    user = _get_user(db)
    card = db.query(UserCard).filter_by(user_id=user.id, segment_id=segment_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    srs_result = review_card(
        state=card.state,
        interval_days=card.interval_days,
        ease_factor=card.ease_factor,
        shadow_streak=card.shadow_streak,
        gen_passed=card.gen_passed,
        stress_passed=card.stress_passed,
        quality=quality,
    )
    card.state = srs_result.new_state
    card.interval_days = srs_result.new_interval
    card.ease_factor = srs_result.new_ease
    card.next_review = srs_result.next_review
    card.last_reviewed = datetime.utcnow()
    card.total_attempts += 1
    if quality >= 3:
        card.correct_attempts += 1

    db.commit()

    return {
        "card_state": card.state,
        "interval_days": card.interval_days,
        "next_review": card.next_review.isoformat(),
    }
