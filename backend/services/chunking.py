"""
Chunk extraction and lightweight post-processing for listening training.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from backend.services.openai_service import extract_sentence_chunks


WORD_RE = re.compile(r"[A-Za-z0-9']+")
LEADING_BAD_WORDS = {
    "of", "to", "and", "but", "the", "a", "an",
}
TRAILING_BAD_WORDS = {
    "of", "to", "for", "and", "but", "or", "a", "an", "the",
}


@dataclass
class WordToken:
    text: str
    normalized: str
    start: int
    end: int


def _tokenize_words(text: str) -> list[WordToken]:
    tokens: list[WordToken] = []
    for match in WORD_RE.finditer(text):
        token = match.group(0)
        tokens.append(
            WordToken(
                text=token,
                normalized=token.lower(),
                start=match.start(),
                end=match.end(),
            )
        )
    return tokens


def _chunk_word_sequences(candidate_chunks: list[str]) -> list[list[str]]:
    sequences: list[list[str]] = []
    for chunk in candidate_chunks:
        words = [w.lower() for w in WORD_RE.findall(chunk)]
        if words:
            sequences.append(words)
    return sequences


def _fallback_ranges(word_count: int, desired_chunks: int) -> list[tuple[int, int]]:
    if word_count == 0:
        return []
    desired = max(1, min(desired_chunks, word_count))
    base = word_count // desired
    extra = word_count % desired
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for index in range(desired):
        size = base + (1 if index < extra else 0)
        start = cursor
        end = cursor + size - 1
        ranges.append((start, end))
        cursor = end + 1
    return ranges


def _heuristic_chunks(sentence: str) -> list[str]:
    parts = [sentence.strip()]
    split_patterns = [
        r",\s+",
        r"\s+(?=if\s)",
        r"\s+(?=because\s)",
        r"\s+(?=when\s)",
        r"\s+(?=while\s)",
        r"\s+(?=what\s)",
        r"\s+(?=why\s)",
        r"\s+(?=how\s)",
        r"\s+(?=that\s)",
        r"\s+(?=and\s)",
        r"\s+(?=but\s)",
    ]

    for pattern in split_patterns:
        next_parts: list[str] = []
        changed = False
        for part in parts:
            if len(WORD_RE.findall(part)) <= 4:
                next_parts.append(part)
                continue
            pieces = [p.strip(" ,.") for p in re.split(pattern, part) if p.strip(" ,.")]
            if len(pieces) > 1:
                next_parts.extend(pieces)
                changed = True
            else:
                next_parts.append(part)
        parts = next_parts
        if changed and len(parts) >= 2:
            break

    return [p for p in parts if p]


def _align_sequences_to_ranges(
    sentence: str,
    candidate_chunks: list[str],
) -> list[tuple[int, int]]:
    words = _tokenize_words(sentence)
    sequences = _chunk_word_sequences(candidate_chunks)
    if not words:
        return []
    if not sequences:
        return [(0, len(words) - 1)]

    ranges: list[tuple[int, int]] = []
    cursor = 0
    remaining_words = len(words)

    for index, sequence in enumerate(sequences):
        if cursor >= len(words):
            break

        remaining_chunks = len(sequences) - index
        latest_start = remaining_words - remaining_chunks
        start_index = cursor
        matched = False

        for probe in range(cursor, max(cursor, latest_start) + 1):
            probe_words = [w.normalized for w in words[probe: probe + len(sequence)]]
            if probe_words == sequence:
                start_index = probe
                matched = True
                break

        if not matched and sequence:
            anchor = sequence[0]
            for probe in range(cursor, max(cursor, latest_start) + 1):
                if words[probe].normalized == anchor:
                    start_index = probe
                    break

        end_index = max(start_index, start_index + len(sequence) - 1)
        max_end = remaining_words - remaining_chunks
        end_index = min(end_index, max_end)
        ranges.append((start_index, end_index))
        cursor = end_index + 1

    if not ranges:
        return [(0, len(words) - 1)]

    repaired: list[tuple[int, int]] = []
    prev_end = -1
    for index, (start, end) in enumerate(ranges):
        start = max(start, prev_end + 1)
        min_end = start
        remaining_chunks = len(ranges) - index - 1
        max_end = len(words) - remaining_chunks - 1
        end = max(min_end, min(end, max_end))
        repaired.append((start, end))
        prev_end = end

    if repaired[-1][1] < len(words) - 1:
        repaired[-1] = (repaired[-1][0], len(words) - 1)

    if repaired[0][0] > 0:
        repaired[0] = (0, repaired[0][1])

    return repaired


def _surface_from_range(text: str, words: list[WordToken], start: int, end: int) -> str:
    return text[words[start].start: words[end].end].strip()


def _merge_adjacent(ranges: list[tuple[int, int]], index: int) -> list[tuple[int, int]]:
    if len(ranges) <= 1:
        return ranges
    if index <= 0:
        new_range = (ranges[0][0], ranges[1][1])
        return [new_range, *ranges[2:]]
    new_range = (ranges[index - 1][0], ranges[index][1])
    return [*ranges[: index - 1], new_range, *ranges[index + 1:]]


def _move_first_word_to_previous(
    ranges: list[tuple[int, int]],
    index: int,
) -> list[tuple[int, int]]:
    if index <= 0:
        return ranges
    prev_start, prev_end = ranges[index - 1]
    start, end = ranges[index]
    updated = ranges[:]
    updated[index - 1] = (prev_start, prev_end + 1)
    if start + 1 <= end:
        updated[index] = (start + 1, end)
    else:
        updated.pop(index)
    return updated


def _move_last_word_to_next(
    ranges: list[tuple[int, int]],
    index: int,
) -> list[tuple[int, int]]:
    if index >= len(ranges) - 1:
        return ranges
    start, end = ranges[index]
    next_start, next_end = ranges[index + 1]
    updated = ranges[:]
    updated[index + 1] = (next_start - 1, next_end)
    if start <= end - 1:
        updated[index] = (start, end - 1)
    else:
        updated.pop(index)
    return updated


def _choose_split_point(words: list[WordToken], start: int, end: int) -> int | None:
    midpoint = start + (end - start + 1) // 2 - 1
    candidates = []
    for offset in range(0, end - start):
        left = midpoint - offset
        right = midpoint + offset
        if start <= left < end:
            candidates.append(left)
        if start <= right < end and right != left:
            candidates.append(right)

    for split_at in candidates:
        left_last = words[split_at].normalized
        right_first = words[split_at + 1].normalized
        if left_last not in TRAILING_BAD_WORDS and right_first not in LEADING_BAD_WORDS:
            return split_at

    if start < end:
        return midpoint
    return None


def _repair_ranges(text: str, ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    words = _tokenize_words(text)
    if not words:
        return []

    changed = True
    iterations = 0
    while changed and len(ranges) > 1 and iterations < 50:
        iterations += 1
        changed = False
        for index, (start, end) in enumerate(ranges):
            chunk_words = words[start: end + 1]
            if not chunk_words:
                ranges = _merge_adjacent(ranges, index)
                changed = True
                break

            first = chunk_words[0].normalized
            last = chunk_words[-1].normalized

            too_short = len(chunk_words) == 1 and first in LEADING_BAD_WORDS.union(TRAILING_BAD_WORDS)
            bad_leading = first in LEADING_BAD_WORDS and index > 0
            bad_trailing = last in TRAILING_BAD_WORDS and index < len(ranges) - 1
            too_long = len(chunk_words) > 8

            if too_short:
                ranges = _merge_adjacent(ranges, index)
                changed = True
                break

            if bad_leading:
                ranges = _move_first_word_to_previous(ranges, index)
                changed = True
                break

            if bad_trailing:
                ranges = _move_last_word_to_next(ranges, index)
                changed = True
                break

            if too_long:
                split_at = _choose_split_point(words, start, end)
                if split_at is not None and start <= split_at < end:
                    left = (start, split_at)
                    right = (split_at + 1, end)
                    ranges = [*ranges[:index], left, right, *ranges[index + 1:]]
                    changed = True
                    break

    return ranges


def extract_chunks(sentence: str, use_llm: bool = False) -> dict:
    """
    Extract listening-friendly chunks for a sentence and apply lightweight repairs.
    """
    words = _tokenize_words(sentence)
    if not words:
        return {"chunks": []}

    if use_llm:
        try:
            raw_chunks = extract_sentence_chunks(sentence)
        except Exception:
            raw_chunks = _heuristic_chunks(sentence)
    else:
        raw_chunks = _heuristic_chunks(sentence)
    ranges = _align_sequences_to_ranges(sentence, raw_chunks)
    if not ranges:
        ranges = _fallback_ranges(len(words), len(raw_chunks) or 1)
    ranges = _repair_ranges(sentence, ranges)

    chunks = []
    for start, end in ranges:
        chunks.append(
            {
                "text": _surface_from_range(sentence, words, start, end),
                "start_word": start,
                "end_word": end,
                "word_count": end - start + 1,
            }
        )

    return {
        "sentence": sentence,
        "raw_chunks": raw_chunks,
        "chunks": chunks,
    }
