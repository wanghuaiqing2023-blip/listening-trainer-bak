"""
Anthropic Claude service for generalization test sentence generation
and initial level assessment.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

from backend.config import settings

_client = None
logger = logging.getLogger(__name__)
BoundaryDebugCallback = Callable[[dict[str, Any]], None]
ExplainDebugCallback = Callable[[dict[str, Any]], None]
SEGMENT_BOUNDARY_MAX_TOKENS = 8000
EXPLAIN_BATCH_SIZE = 30


def _format_log_context(log_context: dict[str, Any] | None) -> str:
    if not log_context:
        return ""

    parts: list[str] = []
    for key in sorted(log_context):
        value = log_context[key]
        if value in (None, ""):
            continue
        parts.append(f"{key}={value}")
    return f" [{' '.join(parts)}]" if parts else ""


def _get_client():
    global _client
    if _client is None:
        from anthropic import Anthropic
        kwargs = {}
        if settings.anthropic_api_key:
            kwargs["api_key"] = settings.anthropic_api_key
        if settings.anthropic_auth_token:
            kwargs["auth_token"] = settings.anthropic_auth_token
        if settings.anthropic_base_url:
            kwargs["base_url"] = settings.anthropic_base_url
        _client = Anthropic(**kwargs)
    return _client


def _get_attr(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _extract_text_blocks(response: Any) -> tuple[str, dict[str, Any]]:
    content_blocks = list(_get_attr(response, "content", []) or [])
    text_blocks: list[str] = []
    block_summaries: list[dict[str, Any]] = []

    for index, block in enumerate(content_blocks):
        block_type = _get_attr(block, "type", "unknown")
        text = _get_attr(block, "text", "")
        if isinstance(text, str) and text:
            text_blocks.append(text)

        summary = {
            "index": index,
            "type": block_type,
        }
        if isinstance(text, str):
            summary["text_chars"] = len(text)
            summary["text_preview"] = text[:200]
        block_summaries.append(summary)

    usage = _get_attr(response, "usage")
    response_meta = {
        "model": _get_attr(response, "model"),
        "id": _get_attr(response, "id"),
        "role": _get_attr(response, "role"),
        "stop_reason": _get_attr(response, "stop_reason"),
        "stop_sequence": _get_attr(response, "stop_sequence"),
        "content_block_count": len(content_blocks),
        "text_block_count": len(text_blocks),
        "text_chars": sum(len(text) for text in text_blocks),
        "usage": {
            "input_tokens": _get_attr(usage, "input_tokens"),
            "output_tokens": _get_attr(usage, "output_tokens"),
            "cache_creation_input_tokens": _get_attr(usage, "cache_creation_input_tokens"),
            "cache_read_input_tokens": _get_attr(usage, "cache_read_input_tokens"),
        } if usage is not None else None,
        "content_blocks": block_summaries,
        "text_blocks": text_blocks,
    }
    return "".join(text_blocks).strip(), response_meta


def _chat_with_metadata(
    prompt: str,
    temperature: float = 0.7,
    max_tokens: int = 500,
) -> dict[str, Any]:
    """Send a message to Claude and return concatenated text plus response metadata."""
    if not (settings.anthropic_api_key or settings.anthropic_auth_token):
        raise RuntimeError("Anthropic credentials are not configured")
    response = _get_client().messages.create(
        model=settings.anthropic_model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[{"role": "user", "content": prompt}],
    )
    text, response_meta = _extract_text_blocks(response)
    response_meta["requested_max_tokens"] = max_tokens
    response_meta["temperature"] = temperature
    return {
        "text": text,
        "response_meta": response_meta,
    }


def _chat(prompt: str, temperature: float = 0.7, max_tokens: int = 500) -> str:
    """Send a message to Claude and return the text response."""
    return _chat_with_metadata(
        prompt,
        temperature=temperature,
        max_tokens=max_tokens,
    )["text"]


def generate_generalization_sentence(
    original_text: str,
    phenomena: list[dict],
) -> str:
    """
    Generate a NEW sentence that exercises the same phonetic rules
    but uses completely different words/context.
    """
    phenomena_desc = "\n".join(
        f"- {p['label']} ({p['type']}): {p['info']}"
        for p in phenomena
    )

    prompt = f"""You are an English phonetics expert creating listening comprehension tests.

Original sentence: "{original_text}"

Phonetic phenomena detected in this sentence:
{phenomena_desc}

Task: Generate ONE new English sentence (8-15 words) that:
1. Contains the SAME phonetic phenomena (e.g., the same type of linking, weak form, or assimilation)
2. Uses completely DIFFERENT words and context from the original
3. Sounds natural and conversational
4. Is at a similar difficulty level

Respond with ONLY the new sentence, nothing else."""

    return _chat(prompt, temperature=0.8, max_tokens=60).strip('"')


def evaluate_dictation(
    reference: str,
    hypothesis: str,
    phenomena: list[dict],
) -> dict:
    """
    Evaluate whether the user's dictation is correct,
    accounting for acceptable phonetic variations.
    """
    prompt = f"""You are evaluating a listening comprehension dictation exercise.

Reference sentence: "{reference}"
User's transcription: "{hypothesis}"

The sentence contains these phonetic phenomena (connected speech, weak forms, etc.):
{chr(10).join(f"- {p['label']}: {p['info']}" for p in phenomena)}

Evaluate the user's transcription:
1. Is it essentially correct? (Accept minor punctuation/capitalization differences)
2. If the user wrote the phonetically-reduced form (e.g., "gonna" for "going to"), accept it.
3. Score from 0.0 to 1.0

Respond in JSON only:
{{"correct": true/false, "score": 0.0-1.0, "feedback": "brief explanation in Chinese"}}"""

    text = _chat(prompt, temperature=0.1, max_tokens=150)
    # Extract JSON from response
    start = text.find('{')
    end = text.rfind('}') + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    return {"correct": False, "score": 0.0, "feedback": "评估失败，请重试"}


def correct_transcripts(sentences: list[str]) -> list[str]:
    """
    Correct ASR transcription errors in a batch of sentences.
    Returns corrected sentences in the same order and same count.
    """
    numbered = "\n".join(f"{i+1}. {s}" for i, s in enumerate(sentences))

    prompt = f"""You are correcting ASR (automatic speech recognition) transcription errors in English sentences.

Sentences to correct:
{numbered}

Rules:
- Fix ONLY clear ASR errors: wrong homophones, garbled words, impossible word sequences
- Keep all informal/spoken forms exactly as-is: gonna, wanna, kinda, yeah, um, uh, etc.
- Do NOT rephrase, restructure, or improve grammar
- Do NOT change word order
- If a sentence looks correct, return it unchanged
- The number of output sentences must exactly match the input

Return ONLY a JSON array with the corrected sentences in the same order:
["sentence 1", "sentence 2", ...]"""

    text = _chat(prompt, temperature=0.1, max_tokens=4000)
    start = text.find("[")
    end = text.rfind("]") + 1
    if start >= 0 and end > start:
        try:
            result = json.loads(text[start:end])
            if len(result) == len(sentences):
                return result
        except json.JSONDecodeError:
            pass
    # Fallback: return originals unchanged
    return sentences


def segment_transcript_text(full_text: str) -> list[str]:
    """
    Ask Claude to split a full transcript into complete, self-contained sentences.
    Returns a list of sentence strings.
    """
    prompt = f"""You are segmenting an English transcript for listening comprehension training.

Transcript:
{full_text}

Split this transcript into complete, self-contained sentences or thoughts suitable for listening practice.

Rules:
- Every segment must be a grammatically complete sentence or a natural complete thought
- Never cut mid-sentence
- Ideal length per segment: 5–20 words
- Merge orphaned short phrases (e.g. "Yeah", "Right", "Okay") into the adjacent sentence
- Preserve the original wording exactly — do not paraphrase or fix grammar

Respond with a JSON array of strings only:
["complete sentence 1", "complete sentence 2", ...]"""

    text = _chat(prompt, temperature=0.1, max_tokens=4000)
    start = text.find("[")
    end = text.rfind("]") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass
    # Fallback: return as single segment
    return [full_text]


def segment_transcript_boundaries(
    tokens: list[dict],
    candidate_boundaries: list[int],
    problems: list[dict] | None = None,
    attempt: int | None = None,
    debug_callback: BoundaryDebugCallback | None = None,
) -> list[int]:
    """
    Ask Claude to split a transcript by returning boundary token indexes.

    A boundary is the last token index of a segment. The final boundary must
    equal the final token index; validation is performed by segmenter.py.
    """
    prompt = build_segment_boundary_prompt(tokens, candidate_boundaries, problems)
    if debug_callback:
        debug_callback({
            "type": "prompt",
            "attempt": attempt,
            "prompt": prompt,
        })
    response_payload = _chat_with_metadata(
        prompt,
        temperature=0.1,
        max_tokens=SEGMENT_BOUNDARY_MAX_TOKENS,
    )
    text = response_payload["text"]
    if debug_callback:
        debug_callback({
            "type": "llm_raw_response",
            "attempt": attempt,
            "raw_response": text,
            "response_meta": response_payload["response_meta"],
        })
    boundaries = parse_segment_boundary_response(text)
    if debug_callback:
        debug_callback({
            "type": "parsed_boundaries",
            "attempt": attempt,
            "boundaries": boundaries,
        })
    return boundaries


def build_segment_boundary_token_lines(tokens: list[dict]) -> list[str]:
    token_lines: list[str] = []
    for idx, token in enumerate(tokens):
        word = str(token.get("word", "")).strip()
        start = float(token.get("start", 0.0))
        end = float(token.get("end", 0.0))
        gap_after = ""
        if idx + 1 < len(tokens):
            next_start = float(tokens[idx + 1].get("start", end))
            gap_after = f", gap_after={max(0.0, next_start - end):.3f}s"
        token_lines.append(f"{idx}: {word} [{start:.3f}-{end:.3f}s{gap_after}]")
    return token_lines


def build_segment_boundary_candidate_payload(
    tokens: list[dict],
    candidate_boundaries: list[int],
) -> list[dict]:
    candidate_payload: list[dict] = []
    for boundary in candidate_boundaries:
        if boundary == len(tokens) - 1:
            left_token = str(tokens[boundary].get("word", "")).strip()
            candidate_payload.append({
                "boundary_index": boundary,
                "type": "final_required",
                "meaning": f"end the final segment at token {boundary}",
                "left_token": left_token,
                "right_token": None,
                "gap_after_ms": None,
                "required": True,
            })
            continue
        current_end = float(tokens[boundary].get("end", 0.0))
        next_start = float(tokens[boundary + 1].get("start", current_end))
        gap_ms = int(round(max(0.0, next_start - current_end) * 1000))
        left_token = str(tokens[boundary].get("word", "")).strip()
        right_token = str(tokens[boundary + 1].get("word", "")).strip()
        candidate_payload.append({
            "boundary_index": boundary,
            "type": "cut_candidate",
            "meaning": f"cut after token {boundary}; the next segment starts at token {boundary + 1}",
            "left_token": left_token,
            "right_token": right_token,
            "gap_after_ms": gap_ms,
            "required": False,
        })
    return candidate_payload


def build_segment_boundary_prompt(
    tokens: list[dict],
    candidate_boundaries: list[int],
    problems: list[dict] | None = None,
) -> str:
    token_lines = build_segment_boundary_token_lines(tokens)
    candidate_payload = build_segment_boundary_candidate_payload(tokens, candidate_boundaries)

    feedback = ""
    if problems:
        feedback = f"""

Previous boundary output had these structural problems:
{json.dumps(problems, ensure_ascii=False, indent=2)}

Revise the boundaries to fix these problems while preserving semantic completeness."""

    return f"""You are segmenting an English transcript for listening comprehension training.

You must use the provided WhisperX token indexes as the only coordinate system.
Do NOT output sentence text. Output boundary indexes only.

Boundary definition:
- A boundary is the last token index of one segment.
- The final boundary must equal the final token index: {len(tokens) - 1}.
- You must choose boundary indexes ONLY from candidate_boundaries.
- A candidate with type="cut_candidate" means: if you choose boundary_index=N, the current segment ends at token N and the next segment starts at token N+1.
- The actual audio cut time for a cut_candidate will be the midpoint between token N end and token N+1 start.
- A candidate with type="final_required" marks the end of the final segment and MUST be included as the last boundary.
- candidate_boundaries has already been generated by the system using timing gaps.
- Do not create or output any boundary index that is not listed in candidate_boundaries.

Segmentation goals:
- Each segment should be a complete sentence or natural complete thought.
- Avoid cutting in the middle of a fixed expression, clause, or obvious discourse unit.
- Ideal segment length is usually 5-20 tokens, but natural completeness is more important.
- Merge orphaned short phrases into adjacent segments when appropriate.
- Use timing gaps as hints, but do not rely only on pauses.

Tokens:
{chr(10).join(token_lines)}

candidate_boundaries:
{json.dumps(candidate_payload, ensure_ascii=False, indent=2)}
{feedback}

Respond with JSON only:
{{"boundaries": [12, 27, 41]}}"""


def parse_segment_boundary_response(text: str) -> list[int]:
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start:end])
            boundaries = data.get("boundaries", [])
            if isinstance(boundaries, list):
                return [int(item) for item in boundaries]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    start = text.find("[")
    end = text.rfind("]") + 1
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start:end])
            if isinstance(data, list):
                return [int(item) for item in data]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    return []


def build_explain_prompt(full_text: str, sentences: list[str]) -> str:
    numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sentences))
    return f"""You are an English linguistics tutor helping Chinese learners understand spoken English sentence by sentence.

Full transcript for context only:
{full_text}

For each sentence below, write a concise Chinese explanation using the surrounding context when helpful.
Each explanation may cover one or more of these aspects, depending on the sentence:
- grammar or sentence structure
- meaning and usage of common expressions, phrases, or idioms
- collocations and word choice
- spoken-language or pragmatic nuance

Requirements:
- write the explanation in Chinese
- be concise but insightful
- keep each explanation under 120 Chinese characters
- do not repeat the original sentence text
- return direct analysis only

Sentence list:
{numbered}

Return a JSON array only. The array length must exactly match the number of sentences.
Each element should be the Chinese explanation for the corresponding sentence:
["explanation 1", "explanation 2", ...]"""


def parse_explain_response(text: str, expected_count: int) -> list[str] | None:
    start = text.find("[")
    end = text.rfind("]") + 1
    if start < 0 or end <= start:
        return None

    try:
        data = json.loads(text[start:end])
    except json.JSONDecodeError:
        return None

    if not isinstance(data, list) or len(data) != expected_count:
        return None

    return [str(item) for item in data]


def explain_segments(
    full_text: str,
    sentences: list[str],
    on_progress: Callable[[int, int, int, int], None] | None = None,
    debug_callback: ExplainDebugCallback | None = None,
    log_context: dict[str, Any] | None = None,
) -> list[str]:
    """
    Generate a deep Chinese linguistic explanation for each sentence,
    using the full transcript as context. Returns one explanation per sentence.
    Processes in batches of 30 to stay within output token limits.
    """
    results: list[str] = []
    batch_size = EXPLAIN_BATCH_SIZE
    total_sentences = len(sentences)
    total_batches = max(1, (total_sentences + batch_size - 1) // batch_size)
    context_suffix = _format_log_context(log_context)
    total_started_at = time.perf_counter()
    total_input_tokens = 0
    total_output_tokens = 0
    fallback_batches = 0
    batch_elapsed_values: list[int] = []

    logger.info(
        "Explain stage started%s total_sentences=%s batch_size=%s total_batches=%s full_text_chars=%s",
        context_suffix,
        total_sentences,
        batch_size,
        total_batches,
        len(full_text),
    )

    for batch_index, batch_start in enumerate(range(0, total_sentences, batch_size), start=1):
        batch = sentences[batch_start: batch_start + batch_size]
        batch_end = batch_start + len(batch) - 1
        prompt = build_explain_prompt(full_text, batch)
        batch_sentence_chars = sum(len(sentence) for sentence in batch)

        logger.info(
            (
                "Explain batch started%s batch=%s/%s sentence_range=%s-%s "
                "batch_size=%s batch_sentence_chars=%s prompt_chars=%s full_text_chars=%s"
            ),
            context_suffix,
            batch_index,
            total_batches,
            batch_start + 1,
            batch_end + 1,
            len(batch),
            batch_sentence_chars,
            len(prompt),
            len(full_text),
        )

        if on_progress:
            on_progress(len(results), total_sentences, batch_index, total_batches)

        if debug_callback:
            debug_callback({
                "type": "prompt",
                "batch_index": batch_index,
                "total_batches": total_batches,
                "batch_start": batch_start,
                "batch_end": batch_end,
                "batch_size": len(batch),
                "completed_before": len(results),
                "total_sentences": total_sentences,
                "full_text_chars": len(full_text),
                "prompt_chars": len(prompt),
                "prompt": prompt,
            })

        started_at = time.perf_counter()
        response_payload = _chat_with_metadata(prompt, temperature=0.3, max_tokens=8000)
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        batch_elapsed_values.append(elapsed_ms)
        text = response_payload["text"]
        response_meta = response_payload["response_meta"] or {}
        usage = response_meta.get("usage") or {}
        input_tokens = usage.get("input_tokens") or 0
        output_tokens = usage.get("output_tokens") or 0
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens

        logger.info(
            (
                "Explain batch finished%s batch=%s/%s elapsed_ms=%s stop_reason=%s "
                "input_tokens=%s output_tokens=%s response_chars=%s"
            ),
            context_suffix,
            batch_index,
            total_batches,
            elapsed_ms,
            response_meta.get("stop_reason"),
            input_tokens,
            output_tokens,
            len(text),
        )

        if debug_callback:
            debug_callback({
                "type": "response",
                "batch_index": batch_index,
                "total_batches": total_batches,
                "batch_start": batch_start,
                "batch_end": batch_end,
                "batch_size": len(batch),
                "elapsed_ms": elapsed_ms,
                "raw_response": text,
                "response_meta": response_payload["response_meta"],
            })

        batch_result = parse_explain_response(text, len(batch))
        used_fallback = batch_result is None
        if batch_result is None:
            fallback_batches += 1
            response_preview = " ".join(text.split())[:200]
            logger.warning(
                (
                    "Explain batch fallback%s batch=%s/%s expected_count=%s raw_response_chars=%s "
                    "raw_response_preview=%r"
                ),
                context_suffix,
                batch_index,
                total_batches,
                len(batch),
                len(text),
                response_preview,
            )
            batch_result = [""] * len(batch)

        results.extend(batch_result)

        logger.info(
            "Explain batch parsed%s batch=%s/%s parsed_count=%s completed_after=%s used_fallback=%s",
            context_suffix,
            batch_index,
            total_batches,
            0 if used_fallback else len(batch_result),
            len(results),
            used_fallback,
        )

        if debug_callback:
            debug_callback({
                "type": "result",
                "batch_index": batch_index,
                "total_batches": total_batches,
                "batch_start": batch_start,
                "batch_end": batch_end,
                "batch_size": len(batch),
                "elapsed_ms": elapsed_ms,
                "used_fallback": used_fallback,
                "parsed_count": 0 if used_fallback else len(batch_result),
                "completed_after": len(results),
                "result_preview": batch_result[:3],
            })

        if on_progress:
            on_progress(len(results), total_sentences, batch_index, total_batches)

    total_elapsed_ms = int((time.perf_counter() - total_started_at) * 1000)
    average_elapsed_ms = int(sum(batch_elapsed_values) / len(batch_elapsed_values)) if batch_elapsed_values else 0
    logger.info(
        (
            "Explain stage finished%s result_count=%s total_sentences=%s total_batches=%s "
            "fallback_batches=%s input_tokens_total=%s output_tokens_total=%s "
            "elapsed_ms_total=%s elapsed_ms_avg=%s"
        ),
        context_suffix,
        len(results),
        total_sentences,
        total_batches,
        fallback_batches,
        total_input_tokens,
        total_output_tokens,
        total_elapsed_ms,
        average_elapsed_ms,
    )
    return results


def generate_level_test_sentences() -> list[dict]:
    """
    Generate 5 test sentences of increasing difficulty (levels 2,4,6,8,10)
    for the initial onboarding assessment.
    """
    prompt = """Generate 5 English sentences for a listening level test, at difficulty levels 2, 4, 6, 8, 10 (1=easiest, 10=hardest).

Criteria:
- Level 2: Simple, slow-speech friendly, common words, no phonetic phenomena
- Level 4: Moderate speed, 1-2 weak forms
- Level 6: Natural speed, several connected speech phenomena
- Level 8: Fast, dense phonetic phenomena, some complex vocabulary
- Level 10: Very fast, heavy reduction, complex syntax, uncommon vocabulary

Respond in JSON only:
{"sentences": [
  {"level": 2, "text": "...", "answer": "..."},
  {"level": 4, "text": "...", "answer": "..."},
  {"level": 6, "text": "...", "answer": "..."},
  {"level": 8, "text": "...", "answer": "..."},
  {"level": 10, "text": "...", "answer": "..."}
]}"""

    text = _chat(prompt, temperature=0.7, max_tokens=600)
    start = text.find('{')
    end = text.rfind('}') + 1
    if start >= 0 and end > start:
        data = json.loads(text[start:end])
        return data.get("sentences", [])
    return []
