"""Vocabulary tracking and statistics."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db
from backend.models import User, Vocabulary
from backend.services.vocabulary import mastery_color

router = APIRouter(prefix="/vocabulary", tags=["vocabulary"])


def _get_user(db: Session) -> User:
    user = db.query(User).filter_by(id=1).first()
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/")
def list_vocabulary(
    state: str | None = None,  # blue | yellow | white
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """Return vocabulary list with mastery probabilities."""
    user = _get_user(db)
    query = db.query(Vocabulary).filter_by(user_id=user.id)

    if state:
        threshold_map = {
            "blue": (0.0, settings.vocab_unknown_threshold),
            "yellow": (settings.vocab_unknown_threshold, settings.vocab_mastered_threshold),
            "white": (settings.vocab_mastered_threshold, 1.01),
        }
        low, high = threshold_map.get(state, (0.0, 1.01))
        query = query.filter(
            Vocabulary.mastery_prob >= low,
            Vocabulary.mastery_prob < high,
        )

    total = query.count()
    words = query.order_by(Vocabulary.mastery_prob.asc()).offset(offset).limit(limit).all()

    return {
        "total": total,
        "words": [
            {
                "word": v.word,
                "mastery_prob": v.mastery_prob,
                "color": mastery_color(v.mastery_prob),
                "encounters": v.encounters,
                "correct_count": v.correct_count,
                "last_seen": v.last_seen.isoformat() if v.last_seen else None,
            }
            for v in words
        ],
    }


@router.get("/stats")
def vocabulary_stats(db: Session = Depends(get_db)):
    """Return vocabulary statistics: counts per color category."""
    user = _get_user(db)
    all_vocab = db.query(Vocabulary).filter_by(user_id=user.id).all()

    unknown = sum(1 for v in all_vocab if v.mastery_prob < settings.vocab_unknown_threshold)
    learning = sum(1 for v in all_vocab if settings.vocab_unknown_threshold <= v.mastery_prob < settings.vocab_mastered_threshold)
    mastered = sum(1 for v in all_vocab if v.mastery_prob >= settings.vocab_mastered_threshold)
    total = len(all_vocab)

    return {
        "total": total,
        "unknown": unknown,       # blue
        "learning": learning,     # yellow
        "mastered": mastered,     # white
        "unknown_pct": round(unknown / total * 100, 1) if total else 0,
        "learning_pct": round(learning / total * 100, 1) if total else 0,
        "mastered_pct": round(mastered / total * 100, 1) if total else 0,
    }


@router.put("/{word}")
def update_word(
    word: str,
    mastery_prob: float,
    db: Session = Depends(get_db),
):
    """Manually override a word's mastery probability."""
    user = _get_user(db)
    vocab = db.query(Vocabulary).filter_by(user_id=user.id, word=word.lower()).first()
    if not vocab:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Word not found")
    vocab.mastery_prob = max(0.0, min(1.0, mastery_prob))
    db.commit()
    return {
        "word": vocab.word,
        "mastery_prob": vocab.mastery_prob,
        "color": mastery_color(vocab.mastery_prob),
    }
