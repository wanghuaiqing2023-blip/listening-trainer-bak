"""
Multi-dimensional difficulty scoring for audio segments.

Each dimension returns a score in [1, 10].
Total difficulty = max(all dimensions).

Dimensions:
  1. speech_rate     — words per minute
  2. phonetics       — phonetic phenomena density
  3. vocabulary      — per-user vocabulary difficulty (Bayesian)
  4. complexity      — syntactic complexity (spaCy)
  5. audio_quality   — signal-to-noise ratio
"""
from __future__ import annotations

import math

import spacy
from wordfreq import zipf_frequency

from backend.utils.audio import compute_snr
from backend.utils.text import extract_words

# Load spaCy model once
_nlp = None


def _get_nlp():
    global _nlp
    if _nlp is None:
        _nlp = spacy.load("en_core_web_sm")
    return _nlp


# ---------------------------------------------------------------------------
# Individual dimension scorers
# ---------------------------------------------------------------------------

def score_speech_rate(words: list[dict]) -> float:
    """
    words: WhisperX word list with 'start' and 'end' keys.
    WPM → 1-10 linear scale.
    """
    if len(words) < 2:
        return 5.0
    duration = words[-1]["end"] - words[0]["start"]
    if duration <= 0:
        return 5.0
    wpm = len(words) / duration * 60
    # <100 wpm → 1, 200 wpm → 10
    score = (wpm - 100) / 10.0
    return float(max(1.0, min(10.0, score)))


def score_phonetics(phenomena_count: int, word_count: int) -> float:
    """
    Ratio of detected phonetic phenomena to total words → 1-10.
    """
    if word_count == 0:
        return 1.0
    density = phenomena_count / word_count
    score = density * 20.0   # ~0.5 phenomena/word → 10
    return float(max(1.0, min(10.0, score)))


def score_vocabulary_objective(text: str) -> float:
    """
    Objective vocabulary difficulty using wordfreq Zipf scores (no user data).
    Used for initial content ingestion before we know the user.
    """
    words = extract_words(text)
    if not words:
        return 5.0
    zipf_scores = [zipf_frequency(w, "en") for w in words]
    avg_zipf = sum(zipf_scores) / len(zipf_scores)
    # Zipf 6 = very common, 2 = rare
    score = (6.0 - avg_zipf) * 2.0
    return float(max(1.0, min(10.0, score)))


def score_vocabulary_for_user(text: str, user_vocab: dict[str, float]) -> float:
    """
    Per-user vocabulary difficulty.

    user_vocab: {word: mastery_prob}
    For words not in user_vocab, fall back to wordfreq prior.
    """
    words = extract_words(text)
    if not words:
        return 5.0

    difficulties = []
    for word in words:
        if word in user_vocab:
            p = user_vocab[word]
        else:
            zipf = zipf_frequency(word, "en")
            p = min(1.0, zipf / 8.0)  # prior from word frequency
        difficulties.append(1.0 - p)  # low mastery = high difficulty

    avg_diff = sum(difficulties) / len(difficulties)
    return float(max(1.0, min(10.0, avg_diff * 10.0)))


def score_complexity(text: str) -> float:
    """
    Syntactic complexity: dependency tree depth + subordinate clause count.
    """
    nlp = _get_nlp()
    doc = nlp(text)
    if not doc:
        return 1.0

    depths = [len(list(token.ancestors)) for token in doc]
    max_depth = max(depths) if depths else 0

    # Count subordinate clauses (advcl, relcl, ccomp)
    sub_clauses = sum(
        1 for token in doc
        if token.dep_ in {"advcl", "relcl", "ccomp", "acl"}
    )

    score = max_depth * 1.2 + sub_clauses * 0.8
    return float(max(1.0, min(10.0, score)))


def score_audio_quality(audio_path: str) -> float:
    """
    Audio quality: higher SNR → lower difficulty.
    SNR >30dB → 1, SNR <5dB → 10.
    """
    snr = compute_snr(audio_path)
    score = 10.0 - snr / 3.0
    return float(max(1.0, min(10.0, score)))


# ---------------------------------------------------------------------------
# Combined scorer
# ---------------------------------------------------------------------------

def compute_difficulty(
    words: list[dict],
    text: str,
    audio_path: str,
    phenomena_count: int = 0,
    phonetics_available: bool = True,
    user_vocab: dict[str, float] | None = None,
) -> dict:
    """
    Compute all difficulty dimensions and return a dict.

    Returns:
        {
            "speech_rate": float,
            "phonetics": float,
            "vocabulary": float,
            "complexity": float,
            "audio_quality": float,
            "total": float,   # max of all dimensions
        }
    """
    word_count = len(words)

    speech_rate = score_speech_rate(words)
    # When Azure Speech is unavailable, use a neutral phonetics score instead of
    # incorrectly treating "no detected phenomena" as genuinely easy speech.
    phonetics = score_phonetics(phenomena_count, word_count) if phonetics_available else 5.0

    if user_vocab is not None:
        vocabulary = score_vocabulary_for_user(text, user_vocab)
    else:
        vocabulary = score_vocabulary_objective(text)

    complexity = score_complexity(text)
    audio_quality = score_audio_quality(audio_path)

    total = max(speech_rate, phonetics, vocabulary, complexity, audio_quality)

    return {
        "speech_rate": round(speech_rate, 2),
        "phonetics": round(phonetics, 2),
        "vocabulary": round(vocabulary, 2),
        "complexity": round(complexity, 2),
        "audio_quality": round(audio_quality, 2),
        "total": round(total, 2),
    }
