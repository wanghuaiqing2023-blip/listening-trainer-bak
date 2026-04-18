"""
Segment transcript into semantically complete training cards.

Pipeline:
1. Text source   — YouTube subtitles (deduplicated) if available,
                   else WhisperX segments
2. LLM cutting   — Claude splits full text into complete sentences
3. Timestamp map — Match each sentence against WhisperX word list
4. Boundary fix  — Cut points move to the midpoint of silence gaps
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class SegmentData:
    text: str
    start: float
    end: float
    words: list[dict] = field(default_factory=list)
    explanation: str = ""


def segment_transcript(
    whisperx_result: dict,
    subtitle_lines: list[dict] | None = None,
) -> list[SegmentData]:
    """
    Main entry point (test-script compatible).
    Calls both phases sequentially.
    """
    segments = cut_into_sentences(whisperx_result)
    return apply_asr_correction(segments)


def cut_into_sentences(whisperx_result: dict) -> list[SegmentData]:
    """
    Phase 1: Claude semantic cut + WhisperX timestamp match + boundary adjustment.
    Returns SegmentData list with original (uncorrected) text.
    """
    from backend.services.openai_service import segment_transcript_text

    all_words = _flatten_words(whisperx_result)
    if not all_words:
        return []

    # Build full text from WhisperX (guarantees word-level match later)
    full_text = " ".join(
        s.get("text", "").strip()
        for s in whisperx_result.get("segments", [])
    ).strip()

    if not full_text:
        return []

    sentences = segment_transcript_text(full_text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return []

    segments = _match_sentences_to_words(sentences, all_words)
    _adjust_boundaries(segments)
    return segments


def apply_asr_correction(segments: list[SegmentData]) -> list[SegmentData]:
    """
    Phase 2: Correct ASR transcription errors via Claude.
    Text only — timestamps are not modified.
    Returns filtered list (empty-text segments removed).
    """
    from backend.services.openai_service import correct_transcripts

    if not segments:
        return []

    originals = [s.text for s in segments]
    corrected = correct_transcripts(originals)
    for seg, new_text in zip(segments, corrected):
        seg.text = new_text

    return [s for s in segments if s.text]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _flatten_words(whisperx_result: dict) -> list[dict]:
    words = []
    for seg in whisperx_result.get("segments", []):
        for w in seg.get("words", []):
            if "start" in w and "end" in w:
                words.append(w)
    return words


def _normalize(text: str) -> list[str]:
    """Lowercase, strip punctuation, split into words for comparison."""
    return re.sub(r"[^\w\s]", "", text.lower()).split()


def _match_sentences_to_words(
    sentences: list[str],
    all_words: list[dict],
) -> list[SegmentData]:
    """
    For each Claude sentence, find the corresponding span in the WhisperX
    word list using a greedy left-to-right word match.

    WhisperX words may differ slightly from Claude's output (punctuation,
    capitalisation), so we compare normalised forms.
    """
    norm_words = [_normalize(w["word"]) for w in all_words]
    # Flatten: each whisperx token is one word, normalise to single string
    flat_norm = [n[0] if n else "" for n in norm_words]

    segments: list[SegmentData] = []
    word_cursor = 0  # next unassigned word index

    for sentence in sentences:
        sent_norm = _normalize(sentence)
        if not sent_norm:
            continue

        # Find the first word of this sentence starting from word_cursor
        match_start = _find_word_sequence(flat_norm, sent_norm, word_cursor)

        if match_start is None:
            # Cannot find — append to previous segment if possible, else skip
            if segments:
                segments[-1].text += " " + sentence
            continue

        match_end = match_start + len(sent_norm) - 1
        match_end = min(match_end, len(all_words) - 1)

        seg_words = all_words[match_start: match_end + 1]
        segments.append(SegmentData(
            text=sentence,
            start=all_words[match_start]["start"],
            end=all_words[match_end]["end"],
            words=seg_words,
        ))
        word_cursor = match_end + 1

    return segments


def _find_word_sequence(
    flat_norm: list[str],
    sent_norm: list[str],
    start_from: int,
) -> int | None:
    """
    Find the index in flat_norm where sent_norm begins, starting at start_from.
    Matches by the first 3 words of the sentence (tolerates minor differences).
    Falls back to matching only the first word if needed.
    """
    probe = sent_norm[:3]  # use up to first 3 words as anchor
    for i in range(start_from, len(flat_norm) - len(probe) + 1):
        if flat_norm[i: i + len(probe)] == probe:
            return i

    # Fallback: match only the first word
    if sent_norm:
        for i in range(start_from, len(flat_norm)):
            if flat_norm[i] == sent_norm[0]:
                return i

    return None


def _adjust_boundaries(segments: list[SegmentData]) -> None:
    """
    Move each cut point to the midpoint of the silence gap between adjacent
    segments so audio is never sliced mid-phoneme. Mutates in-place.
    """
    for i in range(len(segments) - 1):
        gap_start = segments[i].end
        gap_end   = segments[i + 1].start
        if gap_end > gap_start:
            mid = (gap_start + gap_end) / 2
            segments[i].end       = mid
            segments[i + 1].start = mid
