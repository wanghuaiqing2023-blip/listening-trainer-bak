"""
SM-2 Spaced Repetition Algorithm implementation.

Card states:
  new       → first time seen
  learning  → currently being learned (interval < 1 day)
  review    → graduated, being reviewed at intervals
  mastered  → passed all 4 gates AND survived longest SRS interval (90d)

Gate requirements before a card can graduate to 'mastered':
  - shadow_streak >= 3  (Gate 1)
  - gen_passed == True  (Gate 2)
  - stress_passed == True (Gate 3)
  - SRS interval reached 90 days and user still recalled correctly (Gate 4)
"""
from __future__ import annotations

from datetime import datetime, timedelta
from dataclasses import dataclass

MASTERED_INTERVAL_DAYS = 90
INITIAL_EASE = 2.5
MIN_EASE = 1.3


@dataclass
class ReviewResult:
    new_state: str
    new_interval: int
    new_ease: float
    next_review: datetime


def review_card(
    state: str,
    interval_days: int,
    ease_factor: float,
    shadow_streak: int,
    gen_passed: bool,
    stress_passed: bool,
    quality: int,           # 0-5: user performance score
    now: datetime | None = None,
) -> ReviewResult:
    """
    Apply SM-2 update given user performance quality (0-5).

    quality scale:
      5 = perfect recall
      4 = correct with slight hesitation
      3 = correct with difficulty
      2 = incorrect but easy after seeing answer
      1 = incorrect, hard
      0 = total blackout
    """
    if now is None:
        now = datetime.utcnow()

    if quality < 3:
        # Failed: reset to learning, keep ease
        new_interval = 1
        new_ease = max(MIN_EASE, ease_factor - 0.2)
        new_state = "learning"
    else:
        # Passed: compute next interval
        if state == "new" or state == "learning":
            new_interval = 1
        elif interval_days == 1:
            new_interval = 6
        else:
            new_interval = round(interval_days * ease_factor)

        new_ease = ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
        new_ease = max(MIN_EASE, new_ease)
        new_state = "review"

        # Check if all gates passed and interval reached mastery threshold
        all_gates = shadow_streak >= 3 and gen_passed and stress_passed
        if all_gates and new_interval >= MASTERED_INTERVAL_DAYS:
            new_state = "mastered"

    next_review = now + timedelta(days=new_interval)

    return ReviewResult(
        new_state=new_state,
        new_interval=new_interval,
        new_ease=round(new_ease, 4),
        next_review=next_review,
    )


def get_due_cards_filter(now: datetime | None = None):
    """Return SQLAlchemy filter condition for cards due for review."""
    from backend.models import UserCard
    from sqlalchemy import or_
    if now is None:
        now = datetime.utcnow()
    return or_(
        UserCard.state == "new",
        UserCard.next_review <= now,
    )
