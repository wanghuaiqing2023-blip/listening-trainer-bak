"""
Segment transcript into semantically complete training cards.

Pipeline:
1. WhisperX validation — verify token/timing material is usable
2. LLM boundary cut    — Claude returns token boundary indexes
3. Boundary validation — verify boundary indexes are structurally legal
4. Boundary fix        — cut points move to the midpoint of silence gaps
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

MAX_BOUNDARY_REVISIONS = 3
MIN_CANDIDATE_GAP_SECONDS = 0.12
SegmentationTraceCallback = Callable[[dict[str, Any]], None]


@dataclass
class SegmentData:
    text: str
    start: float
    end: float
    words: list[dict] = field(default_factory=list)
    explanation: str = ""


class SegmentationValidationError(ValueError):
    """Raised when transcript tokens or LLM boundaries are structurally invalid."""

    def __init__(self, message: str, issues: list[dict]):
        super().__init__(message)
        self.issues = issues


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


def cut_into_sentences(
    whisperx_result: dict,
    trace_callback: SegmentationTraceCallback | None = None,
) -> list[SegmentData]:
    """
    Phase 1: Claude boundary-index cut + boundary adjustment.
    Returns SegmentData list with original (uncorrected) text.
    """
    from backend.services.openai_service import (
        build_segment_boundary_candidate_payload,
        segment_transcript_boundaries,
    )

    all_words = _flatten_words(whisperx_result)
    if not all_words:
        return []

    candidate_boundaries = build_candidate_boundaries(all_words)
    if trace_callback:
        trace_callback({
            "type": "candidate_boundaries",
            "token_count": len(all_words),
            "candidate_boundaries": candidate_boundaries,
            "candidate_payload": build_segment_boundary_candidate_payload(
                all_words,
                candidate_boundaries,
            ),
        })
    problems: list[dict] | None = None
    boundaries: list[int] = []
    for attempt in range(1, MAX_BOUNDARY_REVISIONS + 1):
        boundaries = segment_transcript_boundaries(
            all_words,
            candidate_boundaries=candidate_boundaries,
            problems=problems,
            attempt=attempt,
            debug_callback=trace_callback,
        )
        problems = validate_boundaries(
            boundaries,
            token_count=len(all_words),
            candidate_boundaries=candidate_boundaries,
        )
        if trace_callback:
            trace_callback({
                "type": "validation",
                "attempt": attempt,
                "boundaries": boundaries,
                "problems": problems,
            })
        if not problems:
            break
        for problem in problems:
            problem["attempt"] = attempt

    if problems:
        raise SegmentationValidationError("LLM boundary output is invalid", problems)

    segments = _segments_from_boundaries(boundaries, all_words)
    _adjust_boundaries(segments)
    if trace_callback:
        trace_callback({
            "type": "segments_raw",
            "boundaries": boundaries,
            "segments": [
                {
                    "index": index,
                    "text": segment.text,
                    "start": segment.start,
                    "end": segment.end,
                    "word_count": len(segment.words),
                    "words": segment.words,
                }
                for index, segment in enumerate(segments)
            ],
        })
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

def validate_transcription_result(whisperx_result: dict) -> list[dict]:
    """
    Validate WhisperX token/timing material immediately after transcription.

    Returns warning issues. Raises SegmentationValidationError for hard errors.
    """
    issues: list[dict] = []
    valid_word_count = 0
    previous_start: float | None = None
    previous_end: float | None = None

    segments = whisperx_result.get("segments", [])
    if not isinstance(segments, list) or not segments:
        raise SegmentationValidationError("WhisperX result has no segments", [{
            "type": "missing_segments",
            "severity": "error",
            "message": "WhisperX result does not contain any segments.",
        }])

    for seg_idx, segment in enumerate(segments):
        words = segment.get("words", [])
        if not isinstance(words, list):
            issues.append({
                "type": "invalid_words_container",
                "severity": "error",
                "segment_index": seg_idx,
                "message": "Segment words field is not a list.",
            })
            continue

        for word_idx, word in enumerate(words):
            location = {"segment_index": seg_idx, "word_index": word_idx}
            token = word.get("word") if isinstance(word, dict) else None
            start = word.get("start") if isinstance(word, dict) else None
            end = word.get("end") if isinstance(word, dict) else None

            if not token or start is None or end is None:
                issues.append({
                    "type": "missing_token_timing",
                    "severity": "error",
                    **location,
                    "message": "Token is missing word/start/end.",
                })
                continue

            if not _is_number(start) or not _is_number(end):
                issues.append({
                    "type": "non_numeric_timing",
                    "severity": "error",
                    **location,
                    "message": "Token start/end must be numeric.",
                })
                continue

            start_f = float(start)
            end_f = float(end)
            if start_f >= end_f:
                issues.append({
                    "type": "invalid_token_timing",
                    "severity": "error",
                    **location,
                    "word": token,
                    "start": start_f,
                    "end": end_f,
                    "message": "Token must satisfy start < end.",
                })
                continue

            duration = end_f - start_f
            if duration < 0.02 or duration > 5.0:
                issues.append({
                    "type": "token_duration_anomaly",
                    "severity": "warning",
                    **location,
                    "word": token,
                    "duration": duration,
                    "message": "Token duration looks unusual.",
                })

            if previous_start is not None and start_f + 0.2 < previous_start:
                issues.append({
                    "type": "token_timing_backwards",
                    "severity": "error",
                    **location,
                    "word": token,
                    "previous_start": previous_start,
                    "start": start_f,
                    "message": "Token timing moves backwards too far.",
                })
            elif previous_end is not None and start_f + 0.05 < previous_end:
                issues.append({
                    "type": "token_timing_overlap",
                    "severity": "warning",
                    **location,
                    "word": token,
                    "previous_end": previous_end,
                    "start": start_f,
                    "message": "Token overlaps previous token slightly.",
                })

            previous_start = start_f
            previous_end = end_f
            valid_word_count += 1

    errors = [issue for issue in issues if issue["severity"] == "error"]
    if errors:
        raise SegmentationValidationError("WhisperX token/timing validation failed", errors)
    if valid_word_count == 0:
        raise SegmentationValidationError("WhisperX result has no usable timed words", [{
            "type": "no_usable_words",
            "severity": "error",
            "message": "No timed words remain after validation.",
        }])
    return [issue for issue in issues if issue["severity"] == "warning"]


def build_candidate_boundaries(
    all_words: list[dict],
    min_gap_seconds: float = MIN_CANDIDATE_GAP_SECONDS,
) -> list[int]:
    """
    Generate allowed boundary indexes.

    A non-final boundary is allowed only when the gap after that token is at
    least min_gap_seconds. The final token index is always allowed.
    """
    if not all_words:
        return []

    candidates: list[int] = []
    for idx in range(len(all_words) - 1):
        current_end = float(all_words[idx]["end"])
        next_start = float(all_words[idx + 1]["start"])
        if next_start - current_end >= min_gap_seconds:
            candidates.append(idx)

    final_boundary = len(all_words) - 1
    if final_boundary not in candidates:
        candidates.append(final_boundary)
    return candidates


def validate_boundaries(
    boundaries: list[int],
    token_count: int,
    candidate_boundaries: list[int] | None = None,
) -> list[dict]:
    """Validate LLM boundary indexes according to the first-version protocol."""
    problems: list[dict] = []

    if token_count <= 0:
        if boundaries:
            problems.append({
                "type": "empty_token_sequence_has_boundaries",
                "severity": "error",
                "message": "Boundary output must be empty when there are no tokens.",
            })
        return problems

    if not boundaries:
        return [{
            "type": "missing_boundaries",
            "severity": "error",
            "message": "Boundary output is empty.",
        }]

    previous = -1
    seen: set[int] = set()
    candidate_set = set(candidate_boundaries or [])
    for position, boundary in enumerate(boundaries):
        if not isinstance(boundary, int):
            problems.append({
                "type": "non_integer_boundary",
                "severity": "error",
                "position": position,
                "boundary": boundary,
                "message": "Boundary must be an integer token index.",
            })
            continue

        if boundary in seen:
            problems.append({
                "type": "duplicate_boundary",
                "severity": "error",
                "position": position,
                "boundary": boundary,
                "message": "Boundary indexes must not repeat.",
            })
        seen.add(boundary)

        if boundary <= previous:
            problems.append({
                "type": "non_increasing_boundary",
                "severity": "error",
                "position": position,
                "boundary": boundary,
                "previous_boundary": previous,
                "message": "Boundaries must be strictly increasing.",
            })

        if boundary < 0 or boundary >= token_count:
            problems.append({
                "type": "boundary_out_of_range",
                "severity": "error",
                "position": position,
                "boundary": boundary,
                "token_count": token_count,
                "message": "Boundary is outside the valid token index range.",
            })
        elif candidate_boundaries is not None and boundary != token_count - 1 and boundary not in candidate_set:
            problems.append({
                "type": "boundary_not_in_candidate_set",
                "severity": "error",
                "position": position,
                "boundary": boundary,
                "candidate_boundaries": candidate_boundaries,
                "min_gap_seconds": MIN_CANDIDATE_GAP_SECONDS,
                "message": "Non-final boundary must be selected from candidate boundaries.",
            })

        previous = boundary

    if boundaries[-1] != token_count - 1:
        problems.append({
            "type": "missing_final_boundary",
            "severity": "error",
            "last_boundary": boundaries[-1],
            "expected_last_boundary": token_count - 1,
            "message": "The final boundary must equal the final token index.",
        })

    return problems


def _flatten_words(whisperx_result: dict) -> list[dict]:
    validate_transcription_result(whisperx_result)
    words = []
    for seg in whisperx_result.get("segments", []):
        for w in seg.get("words", []):
            if "start" in w and "end" in w:
                words.append(w)
    return words


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _segments_from_boundaries(boundaries: list[int], all_words: list[dict]) -> list[SegmentData]:
    segments: list[SegmentData] = []
    start_idx = 0
    for boundary in boundaries:
        seg_words = all_words[start_idx: boundary + 1]
        text = _words_to_text(seg_words)
        segments.append(SegmentData(
            text=text,
            start=float(seg_words[0]["start"]),
            end=float(seg_words[-1]["end"]),
            words=seg_words,
        ))
        start_idx = boundary + 1
    return segments


def _words_to_text(words: list[dict]) -> str:
    return " ".join(str(word.get("word", "")).strip() for word in words).strip()


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
