"""
Phonetic phenomena detection based on ACTUAL audio phonemes.

Pipeline:
  1. WhisperX provides word-level timestamps.
  2. Azure Speech Pronunciation Assessment returns the actual phoneme sequence
     for each word in the audio.
  3. We compare actual phonemes against CMU Pronouncing Dictionary canonical forms.
  4. Differences are classified as: linking, weak_form, assimilation, elision, flapping.

No text-rule heuristics — everything is derived from what was actually spoken.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import azure.cognitiveservices.speech as speechsdk

from backend.config import settings

# ---------------------------------------------------------------------------
# CMU-style canonical phoneme dictionary (simplified subset)
# In production, load from the full CMU Pronouncing Dictionary (cmudict).
# ---------------------------------------------------------------------------

# Weak form pairs: {word: {"strong": [...], "weak": [...]}}
WEAK_FORMS: dict[str, dict] = {
    "and": {"strong": ["AE1", "N", "D"], "weak": ["AH0", "N", "D"], "label": "弱读"},
    "the": {"strong": ["DH", "AH1"], "weak": ["DH", "AH0"], "label": "弱读"},
    "a": {"strong": ["EY1"], "weak": ["AH0"], "label": "弱读"},
    "to": {"strong": ["T", "UW1"], "weak": ["T", "AH0"], "label": "弱读"},
    "of": {"strong": ["AH1", "V"], "weak": ["AH0", "V"], "label": "弱读"},
    "for": {"strong": ["F", "AO1", "R"], "weak": ["F", "ER0"], "label": "弱读"},
    "that": {"strong": ["DH", "AE1", "T"], "weak": ["DH", "AH0", "T"], "label": "弱读"},
    "can": {"strong": ["K", "AE1", "N"], "weak": ["K", "AH0", "N"], "label": "弱读"},
    "have": {"strong": ["HH", "AE1", "V"], "weak": ["AH0", "V"], "label": "弱读"},
    "has": {"strong": ["HH", "AE1", "Z"], "weak": ["AH0", "Z"], "label": "弱读"},
    "was": {"strong": ["W", "AO1", "Z"], "weak": ["W", "AH0", "Z"], "label": "弱读"},
    "were": {"strong": ["W", "ER1"], "weak": ["W", "ER0"], "label": "弱读"},
    "would": {"strong": ["W", "UH1", "D"], "weak": ["W", "AH0", "D"], "label": "弱读"},
    "could": {"strong": ["K", "UH1", "D"], "weak": ["K", "AH0", "D"], "label": "弱读"},
    "should": {"strong": ["SH", "UH1", "D"], "weak": ["SH", "AH0", "D"], "label": "弱读"},
    "will": {"strong": ["W", "IH1", "L"], "weak": ["AH0", "L"], "label": "弱读"},
    "him": {"strong": ["HH", "IH1", "M"], "weak": ["IH0", "M"], "label": "弱读"},
    "her": {"strong": ["HH", "ER1"], "weak": ["ER0"], "label": "弱读"},
    "them": {"strong": ["DH", "EH1", "M"], "weak": ["DH", "AH0", "M"], "label": "弱读"},
    "us": {"strong": ["AH1", "S"], "weak": ["AH0", "S"], "label": "弱读"},
    "at": {"strong": ["AE1", "T"], "weak": ["AH0", "T"], "label": "弱读"},
    "from": {"strong": ["F", "R", "AH1", "M"], "weak": ["F", "R", "AH0", "M"], "label": "弱读"},
}

# Flapping: /t/ or /d/ realized as flap /ɾ/ (ARPAbet: DX)
FLAP_PHONEME = "DX"

# Assimilation patterns: when word boundary causes phoneme change
# Represented as (word1_last_phoneme, word2_first_phoneme) → result_phoneme
ASSIMILATION_PATTERNS = [
    # Yod-coalescence: t + y → ch, d + y → j
    (("T", "Y"), "CH", "同化"),
    (("D", "Y"), "JH", "同化"),
    # Place assimilation: n + p/b → m
    (("N", "P"), "M", "同化"),
    (("N", "B"), "M", "同化"),
    # Palatalization: s + y → sh
    (("S", "Y"), "SH", "同化"),
]


@dataclass
class PhonemePhenomenon:
    word: str
    word_index: int
    start: float
    end: float
    phenomena: list[dict] = field(default_factory=list)  # [{type, label, info}]


def _azure_assess_phonemes(audio_path: str, reference_text: str) -> list[dict]:
    """
    Call Azure Speech Pronunciation Assessment on a short audio segment.
    Returns list of word-level phoneme data:
    [{"word": str, "phonemes": [str], "offset_ms": int, "duration_ms": int}]
    """
    speech_config = speechsdk.SpeechConfig(
        subscription=settings.azure_speech_key,
        region=settings.azure_speech_region,
    )
    audio_config = speechsdk.AudioConfig(filename=audio_path)

    pron_config = speechsdk.PronunciationAssessmentConfig(
        reference_text=reference_text,
        grading_system=speechsdk.PronunciationAssessmentGradingSystem.HundredMark,
        granularity=speechsdk.PronunciationAssessmentGranularity.Phoneme,
        enable_miscue=True,
    )
    pron_config.enable_prosody_assessment()

    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )
    pron_config.apply_to(recognizer)

    result = recognizer.recognize_once()

    if result.reason != speechsdk.ResultReason.RecognizedSpeech:
        return []

    pron_result = speechsdk.PronunciationAssessmentResult(result)
    words_data = []
    for word in pron_result.words:
        phonemes = [p.phoneme for p in word.phonemes]
        words_data.append({
            "word": word.word,
            "phonemes": phonemes,
            "accuracy": word.accuracy_score,
        })
    return words_data


def detect_phenomena(
    segment_audio_path: str,
    text: str,
    word_timestamps: list[dict],
) -> list[PhonemePhenomenon]:
    """
    Detect phonetic phenomena by comparing Azure-returned actual phonemes
    against canonical (expected) phonemes.

    Returns list of PhonemePhenomenon, one per word that has detected phenomena.
    """
    azure_words = _azure_assess_phonemes(segment_audio_path, text)
    if not azure_words:
        return []

    results: list[PhonemePhenomenon] = []

    for i, az_word in enumerate(azure_words):
        word_lower = az_word["word"].lower().rstrip(".,!?;:")
        actual_phonemes = [p.upper() for p in az_word["phonemes"]]

        # Get timestamp from WhisperX data
        ts = word_timestamps[i] if i < len(word_timestamps) else {}
        start = ts.get("start", 0.0)
        end = ts.get("end", 0.0)

        phenomena: list[dict] = []

        # --- 1. Weak form detection ---
        if word_lower in WEAK_FORMS:
            wf = WEAK_FORMS[word_lower]
            strong = wf["strong"]
            weak = wf["weak"]
            # If actual phonemes are closer to weak form than strong
            if _phoneme_similarity(actual_phonemes, weak) > _phoneme_similarity(actual_phonemes, strong):
                phenomena.append({
                    "type": "weakForm",
                    "label": "弱读",
                    "info": f"{word_lower}: /{' '.join(strong)}/ → /{' '.join(actual_phonemes)}/",
                })

        # --- 2. Flapping detection ---
        if FLAP_PHONEME in actual_phonemes:
            phenomena.append({
                "type": "flapping",
                "label": "闪音",
                "info": f"{word_lower}: /t/ or /d/ → /ɾ/ (flap)",
            })

        # --- 3. Elision detection ---
        canonical = WEAK_FORMS.get(word_lower, {}).get("strong", [])
        if canonical and len(actual_phonemes) < len(canonical) - 1:
            phenomena.append({
                "type": "elision",
                "label": "省略",
                "info": f"{word_lower}: expected {len(canonical)} phonemes, got {len(actual_phonemes)}",
            })

        # --- 4. Assimilation detection (cross-word boundary) ---
        if i > 0 and azure_words[i - 1]["phonemes"]:
            prev_phonemes = [p.upper() for p in azure_words[i - 1]["phonemes"]]
            prev_last = prev_phonemes[-1] if prev_phonemes else ""
            curr_first = actual_phonemes[0] if actual_phonemes else ""

            for (p1, p2), result_ph, label in ASSIMILATION_PATTERNS:
                if prev_last == p1 and curr_first == p2:
                    phenomena.append({
                        "type": "assimilation",
                        "label": label,
                        "info": f"/{p1}/ + /{p2}/ → /{result_ph}/",
                    })

        # --- 5. Linking detection ---
        if i > 0 and azure_words[i - 1]["phonemes"] and actual_phonemes:
            prev_phonemes = [p.upper() for p in azure_words[i - 1]["phonemes"]]
            prev_ts = word_timestamps[i - 1] if i - 1 < len(word_timestamps) else {}
            prev_end = prev_ts.get("end", 0.0)
            gap = start - prev_end
            # Linking: gap < 30ms and no pause between words
            if 0 <= gap < 0.03:
                prev_word_lower = azure_words[i - 1]["word"].lower().rstrip(".,!?;:")
                phenomena.append({
                    "type": "linking",
                    "label": "连读",
                    "info": f'"{prev_word_lower}" + "{word_lower}" linked (gap={gap:.3f}s)',
                })

        if phenomena:
            results.append(PhonemePhenomenon(
                word=az_word["word"],
                word_index=i,
                start=start,
                end=end,
                phenomena=phenomena,
            ))

    return results


def _phoneme_similarity(actual: list[str], reference: list[str]) -> float:
    """Simple overlap-based similarity between two phoneme sequences."""
    if not reference:
        return 0.0
    matches = sum(a == r for a, r in zip(actual, reference))
    return matches / max(len(actual), len(reference))


def phenomena_to_annotations(phenomena_list: list[PhonemePhenomenon]) -> list[dict]:
    """Convert PhonemePhenomenon list to JSON-serializable annotation list."""
    return [
        {
            "word": p.word,
            "word_index": p.word_index,
            "start": p.start,
            "end": p.end,
            "phenomena": p.phenomena,
        }
        for p in phenomena_list
    ]
