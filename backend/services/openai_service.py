"""
Anthropic Claude service for generalization test sentence generation
and initial level assessment.
"""
from __future__ import annotations

import json

from backend.config import settings

_client = None


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


def _chat(prompt: str, temperature: float = 0.7, max_tokens: int = 500) -> str:
    """Send a message to Claude and return the text response."""
    if not (settings.anthropic_api_key or settings.anthropic_auth_token):
        raise RuntimeError("Anthropic credentials are not configured")
    response = _get_client().messages.create(
        model=settings.anthropic_model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


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


def extract_sentence_chunks(sentence: str) -> list[str]:
    """
    Ask Claude to split one sentence into listening-friendly chunks.
    Returns a list of chunk strings in original order.
    """
    prompt = f"""You are splitting one English sentence into listening-friendly chunks for an English listening trainer.

Sentence:
"{sentence}"

Rules:
- Keep the original wording exactly
- Return contiguous chunks in the original order
- Chunks should help a learner hear the sentence in meaningful groups
- Prefer natural phrase boundaries over grammar jargon
- Keep common expressions together when possible
- Avoid chunks that are only a single function word unless absolutely necessary
- Usually produce 2 to 6 chunks for one sentence

Respond with JSON only:
{{"chunks": ["chunk 1", "chunk 2", "chunk 3"]}}"""

    text = _chat(prompt, temperature=0.1, max_tokens=300)
    start = text.find('{')
    end = text.rfind('}') + 1
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start:end])
            chunks = data.get("chunks", [])
            if isinstance(chunks, list):
                cleaned = [c.strip() for c in chunks if isinstance(c, str) and c.strip()]
                if cleaned:
                    return cleaned
        except json.JSONDecodeError:
            pass
    return [sentence]


def explain_segments(full_text: str, sentences: list[str]) -> list[str]:
    """
    Generate a deep Chinese linguistic explanation for each sentence,
    using the full transcript as context. Returns one explanation per sentence.
    Processes in batches of 30 to stay within output token limits.
    """
    results: list[str] = []
    batch_size = 30

    for batch_start in range(0, len(sentences), batch_size):
        batch = sentences[batch_start: batch_start + batch_size]
        numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(batch))

        prompt = f"""你是一位英语语言专家，正在为中文学习者逐句讲解一段英语音频。

完整转录原文（仅供上下文参考）：
{full_text}

请对下列每个句子，结合上下文，用中文进行深入讲解。每条讲解应涵盖以下一项或多项（视句子内容而定）：
- 语法结构（如特殊句式、从句、省略等）
- 常用表达、短语或习语的含义与用法
- 词汇搭配与语感
- 口语特点或语用含义

要求：讲解简明深刻，不超过120字，不重复句子原文，直接给出分析。

句子列表：
{numbered}

请返回 JSON 数组，长度与句子数量完全相同，每个元素是对应句子的中文讲解：
["讲解1", "讲解2", ...]"""

        text = _chat(prompt, temperature=0.3, max_tokens=8000)
        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            try:
                batch_result = json.loads(text[start:end])
                if len(batch_result) == len(batch):
                    results.extend(batch_result)
                    continue
            except json.JSONDecodeError:
                pass
        # Fallback for this batch
        results.extend([""] * len(batch))

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
