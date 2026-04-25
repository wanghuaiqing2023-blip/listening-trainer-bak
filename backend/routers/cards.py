"""Card listing, retrieval, and deletion with i+1 filtering."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import Content, Segment, UserCard, User, Vocabulary
from backend.services.difficulty import score_vocabulary_for_user
from backend.services.srs import get_due_cards_filter

router = APIRouter(prefix="/cards", tags=["cards"])


def _get_user(db: Session) -> User:
    user = db.query(User).filter_by(id=1).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _user_vocab_map(db: Session, user_id: int) -> dict[str, float]:
    """Return {word: mastery_prob} for a user."""
    rows = db.query(Vocabulary).filter_by(user_id=user_id).all()
    return {r.word: r.mastery_prob for r in rows}


def _dedupe_segments_for_library(
    segments: list[Segment],
    user_card_map: dict[int, UserCard],
) -> list[Segment]:
    """
    Library should not surface duplicate segments left behind by historical reruns.
    Keep at most one segment for each (content_id, index), preferring:
    1. a segment that already has a UserCard for this user
    2. otherwise the newest segment id
    """
    selected: dict[tuple[int, int], Segment] = {}
    for seg in segments:
        key = (seg.content_id, seg.index)
        current = selected.get(key)
        if current is None:
            selected[key] = seg
            continue

        current_has_card = current.id in user_card_map
        seg_has_card = seg.id in user_card_map
        if seg_has_card and not current_has_card:
            selected[key] = seg
            continue
        if seg_has_card == current_has_card and seg.id > current.id:
            selected[key] = seg

    return sorted(selected.values(), key=lambda seg: (seg.content_id, seg.index))


@router.get("/")
def list_cards(
    mode: str = Query("training", enum=["training", "review", "all"]),
    db: Session = Depends(get_db),
):
    """
    Return cards filtered by i+1 rule and optionally by SRS due date.

    mode=training  → new cards within [i, i+1] difficulty window
    mode=review    → cards due for SRS review
    mode=all       → all cards for the user
    """
    user = _get_user(db)
    user_vocab = _user_vocab_map(db, user.id)

    # Get all segments for this user's content
    from backend.models import Content
    content_ids = [
        c.id for c in db.query(Content).filter_by(user_id=user.id, status="ready").all()
    ]
    if not content_ids:
        return []

    segments = (
        db.query(Segment)
        .filter(Segment.content_id.in_(content_ids))
        .order_by(Segment.content_id, Segment.index, Segment.id.desc())
        .all()
    )

    all_user_cards = db.query(UserCard).filter_by(user_id=user.id).all()
    user_card_map = {card.segment_id: card for card in all_user_cards}
    segments = _dedupe_segments_for_library(segments, user_card_map)

    # Build a title lookup to avoid N+1 queries
    content_title_map = {
        c.id: c.title
        for c in db.query(Content).filter(Content.id.in_(content_ids)).all()
    }

    result = []
    for seg in segments:
        # Recalculate vocabulary difficulty for this specific user
        vocab_diff = score_vocabulary_for_user(seg.text, user_vocab)

        # Recalculate total difficulty with user-specific vocab score
        dims = [
            seg.diff_speech_rate,
            seg.diff_phonetics,
            vocab_diff,
            seg.diff_complexity,
            seg.diff_audio_quality,
        ]
        user_total = max(dims)

        if mode == "training":
            # i+1 filter
            if not (user.level_score <= user_total <= user.level_score + 1.0):
                continue
        elif mode == "review":
            # Only cards due for SRS review
            card = user_card_map.get(seg.id)
            if not card:
                continue
            from datetime import datetime
            if card.state == "mastered":
                continue
            if card.next_review and card.next_review > datetime.utcnow():
                continue

        # Get or create UserCard
        card = user_card_map.get(seg.id)
        card_state = card.state if card else "new"
        next_review = card.next_review.isoformat() if (card and card.next_review) else None

        result.append({
            "id": seg.id,
            "content_id": seg.content_id,
            "content_title": content_title_map.get(seg.content_id, ""),
            "index": seg.index,          # 0-based in DB
            "card_number": seg.index + 1, # 1-based for display
            "text": seg.text,
            "start_time": seg.start_time,
            "end_time": seg.end_time,
            "audio_url": f"/audio/{seg.id}",
            "difficulty": {
                "speech_rate": seg.diff_speech_rate,
                "phonetics": seg.diff_phonetics,
                "vocabulary": round(vocab_diff, 2),
                "complexity": seg.diff_complexity,
                "audio_quality": seg.diff_audio_quality,
                "total": round(user_total, 2),
            },
            "phonetic_annotations": seg.phonetic_annotations,
            "explanation": seg.explanation or "",
            "card_state": card_state,
            "next_review": next_review,
        })

    return result


@router.delete("/all")
def delete_all_cards(db: Session = Depends(get_db)):
    """Delete all segments and their SRS progress for the current user."""
    user = _get_user(db)
    content_ids = [
        c.id for c in db.query(Content).filter_by(user_id=user.id).all()
    ]
    if not content_ids:
        return {"deleted": 0}

    segments = db.query(Segment).filter(Segment.content_id.in_(content_ids)).all()
    deleted = 0
    for seg in segments:
        # Delete associated UserCards
        db.query(UserCard).filter_by(segment_id=seg.id).delete()
        # Delete audio file if it exists
        if seg.audio_path and os.path.exists(seg.audio_path):
            try:
                os.remove(seg.audio_path)
            except OSError:
                pass
        db.delete(seg)
        deleted += 1

    db.commit()
    return {"deleted": deleted}


@router.delete("/{segment_id}")
def delete_card(segment_id: int, db: Session = Depends(get_db)):
    """Delete a specific segment and its SRS progress."""
    seg = db.get(Segment, segment_id)
    if not seg:
        raise HTTPException(status_code=404, detail="Segment not found")

    db.query(UserCard).filter_by(segment_id=seg.id).delete()
    if seg.audio_path and os.path.exists(seg.audio_path):
        try:
            os.remove(seg.audio_path)
        except OSError:
            pass
    db.delete(seg)
    db.commit()
    return {"deleted": segment_id}


@router.get("/{segment_id}")
def get_card(segment_id: int, db: Session = Depends(get_db)):
    """Get full details of a single card."""
    user = _get_user(db)
    seg = db.get(Segment, segment_id)
    if not seg:
        raise HTTPException(status_code=404, detail="Segment not found")

    user_vocab = _user_vocab_map(db, user.id)
    vocab_diff = score_vocabulary_for_user(seg.text, user_vocab)

    dims = [
        seg.diff_speech_rate,
        seg.diff_phonetics,
        vocab_diff,
        seg.diff_complexity,
        seg.diff_audio_quality,
    ]
    user_total = max(dims)

    card = db.query(UserCard).filter_by(user_id=user.id, segment_id=seg.id).first()

    return {
        "id": seg.id,
        "content_id": seg.content_id,
        "index": seg.index,
        "text": seg.text,
        "start_time": seg.start_time,
        "end_time": seg.end_time,
        "audio_url": f"/audio/{seg.id}",
        "word_timestamps": seg.word_timestamps,
        "difficulty": {
            "speech_rate": seg.diff_speech_rate,
            "phonetics": seg.diff_phonetics,
            "vocabulary": round(vocab_diff, 2),
            "complexity": seg.diff_complexity,
            "audio_quality": seg.diff_audio_quality,
            "total": round(user_total, 2),
        },
        "phonetic_annotations": seg.phonetic_annotations,
        "explanation": seg.explanation or "",
        "card": {
            "state": card.state if card else "new",
            "shadow_streak": card.shadow_streak if card else 0,
            "gen_passed": card.gen_passed if card else False,
            "stress_passed": card.stress_passed if card else False,
            "interval_days": card.interval_days if card else 1,
            "next_review": card.next_review.isoformat() if (card and card.next_review) else None,
        },
    }
