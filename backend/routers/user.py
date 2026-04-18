"""User level management and onboarding test."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db
from backend.models import User
from backend.services.azure_speech import synthesize_speech
from backend.services.openai_service import generate_level_test_sentences
from backend.utils.text import dictation_accuracy

router = APIRouter(prefix="/user", tags=["user"])


def _get_or_create_user(db: Session) -> User:
    user = db.query(User).filter_by(id=1).first()
    if not user:
        user = User(id=1, name="default", level_score=5.0)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


@router.get("/level")
def get_level(db: Session = Depends(get_db)):
    user = _get_or_create_user(db)
    return {
        "level_score": user.level_score,
        "level_label": _level_label(user.level_score),
    }


@router.put("/level")
def update_level(level_score: float, db: Session = Depends(get_db)):
    """Manually set user level (admin/debug use)."""
    if not 1.0 <= level_score <= 10.0:
        raise HTTPException(status_code=400, detail="Level must be between 1 and 10")
    user = _get_or_create_user(db)
    user.level_score = level_score
    db.commit()
    return {
        "level_score": user.level_score,
        "level_label": _level_label(user.level_score),
    }


@router.get("/test/sentences")
async def get_test_sentences(db: Session = Depends(get_db)):
    """
    Generate 5 test sentences (levels 2, 4, 6, 8, 10) and synthesize audio.
    Used for the onboarding listening test.
    """
    _get_or_create_user(db)
    sentences = generate_level_test_sentences()
    result = []
    for item in sentences:
        text = item.get("text", "")
        tts_path = synthesize_speech(text, settings.segments_dir)
        filename = Path(tts_path).name
        result.append(
            {
                "level": item.get("level"),
                "audio_url": f"/audio/tts/{filename}",
                "_answer": item.get("answer", text),
            }
        )
    return result


@router.post("/test/submit")
def submit_test(
    responses: list[dict],
    db: Session = Depends(get_db),
):
    """
    Evaluate onboarding test responses and set initial user level.

    Each response: {level, user_text, answer}
    Level is determined as the highest level with accuracy >= 0.70.
    """
    if not responses:
        raise HTTPException(status_code=400, detail="No responses provided")

    level_results = []
    for response in responses:
        accuracy = dictation_accuracy(response.get("answer", ""), response.get("user_text", ""))
        level_results.append(
            {
                "level": response.get("level", 5),
                "accuracy": accuracy,
                "passed": accuracy >= 0.70,
            }
        )

    passed_levels = [item["level"] for item in level_results if item["passed"]]
    initial_level = float(max(passed_levels)) if passed_levels else 2.0

    user = _get_or_create_user(db)
    user.level_score = initial_level
    db.commit()

    return {
        "initial_level": initial_level,
        "level_label": _level_label(initial_level),
        "results": level_results,
    }


def _level_label(score: float) -> str:
    if score <= 2:
        return "A1 初级"
    if score <= 4:
        return "A2 基础"
    if score <= 6:
        return "B1 中级"
    if score <= 8:
        return "B2 中高级"
    return "C1/C2 高级"
