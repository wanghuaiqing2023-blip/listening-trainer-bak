"""
Bayesian vocabulary mastery tracking.

Each word has a mastery_prob in [0, 1]:
  < 0.30  → blue  (unknown)
  < 0.85  → yellow (learning)
  >= 0.85 → white  (mastered)

Update model:
  - Correct answer: prob increases (Beta distribution update)
  - Wrong answer:   prob decreases
  - Time decay:     prob drifts toward prior based on elapsed time (Ebbinghaus)

Prior:
  - New word: prior = wordfreq Zipf / 8.0 (high-frequency words start with higher prior)
"""
from __future__ import annotations

import math
from datetime import datetime

from wordfreq import zipf_frequency

UNKNOWN_THRESHOLD = 0.30
MASTERED_THRESHOLD = 0.85

# Forgetting decay constant (days): prob decays with half-life of ~30 days
HALF_LIFE_DAYS = 30.0

# Beta distribution pseudo-counts
ALPHA_INIT = 1.0
BETA_INIT = 1.0


def initial_mastery_prob(word: str) -> float:
    """Compute prior mastery probability from word frequency."""
    zipf = zipf_frequency(word.lower(), "en")
    # Zipf 0-8; 8 = extremely common. Map to [0.05, 0.6]
    p = min(0.6, max(0.05, zipf / 8.0 * 0.6))
    return round(p, 4)


def update_mastery_prob(
    current_prob: float,
    correct: bool,
    difficulty_weight: float = 1.0,
    last_seen: datetime | None = None,
    now: datetime | None = None,
) -> float:
    """
    Bayesian update of mastery probability.

    Args:
        current_prob:      Current P(mastery) in [0, 1]
        correct:           Whether the user answered correctly
        difficulty_weight: Higher difficulty → larger update step (0.5-2.0)
        last_seen:         When the word was last reviewed (for decay)
        now:               Current time (default: utcnow)

    Returns:
        Updated probability in [0, 1]
    """
    if now is None:
        now = datetime.utcnow()

    # Step 1: Apply time decay (Ebbinghaus forgetting curve)
    if last_seen is not None:
        days_elapsed = (now - last_seen).total_seconds() / 86400.0
        decay = math.exp(-days_elapsed / HALF_LIFE_DAYS * math.log(2))
        # Decay toward prior (0.1) not toward 0
        prior = 0.1
        current_prob = prior + (current_prob - prior) * decay

    # Step 2: Bayesian-style update using Beta distribution
    # Interpret current_prob as alpha / (alpha + beta)
    alpha = current_prob * (ALPHA_INIT + BETA_INIT)
    beta = (1.0 - current_prob) * (ALPHA_INIT + BETA_INIT)

    step = difficulty_weight
    if correct:
        alpha += step
    else:
        beta += step

    new_prob = alpha / (alpha + beta)
    return round(float(max(0.0, min(1.0, new_prob))), 4)


def mastery_color(prob: float) -> str:
    """Return display color category for a mastery probability."""
    if prob >= MASTERED_THRESHOLD:
        return "white"
    elif prob >= UNKNOWN_THRESHOLD:
        return "yellow"
    else:
        return "blue"
