"""
Azure Speech services:
  1. Pronunciation Assessment (shadowing evaluation)
  2. Neural TTS (generalization test audio generation)
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import azure.cognitiveservices.speech as speechsdk

from backend.config import settings


# ---------------------------------------------------------------------------
# Pronunciation Assessment (Shadowing Gate)
# ---------------------------------------------------------------------------

def assess_pronunciation(audio_path: str, reference_text: str) -> dict:
    """
    Evaluate how closely user's recording matches the reference text.

    Returns:
        {
            "accuracy_score": float,       # 0-100
            "fluency_score": float,
            "completeness_score": float,
            "pronunciation_score": float,  # overall
            "words": [{"word": str, "accuracy": float, "error_type": str}]
        }
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

    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )
    pron_config.apply_to(recognizer)

    result = recognizer.recognize_once()

    if result.reason == speechsdk.ResultReason.RecognizedSpeech:
        pron_result = speechsdk.PronunciationAssessmentResult(result)
        words = []
        for w in pron_result.words:
            words.append({
                "word": w.word,
                "accuracy": w.accuracy_score,
                "error_type": w.error_type,
            })
        return {
            "accuracy_score": pron_result.accuracy_score,
            "fluency_score": pron_result.fluency_score,
            "completeness_score": pron_result.completeness_score,
            "pronunciation_score": pron_result.pronunciation_score,
            "words": words,
            "recognized_text": result.text,
        }
    else:
        return {
            "accuracy_score": 0.0,
            "fluency_score": 0.0,
            "completeness_score": 0.0,
            "pronunciation_score": 0.0,
            "words": [],
            "recognized_text": "",
            "error": str(result.reason),
        }


# ---------------------------------------------------------------------------
# Neural TTS
# ---------------------------------------------------------------------------

def synthesize_speech(text: str, output_dir: Path) -> str:
    """
    Synthesize text to speech using Azure Neural TTS.
    Returns path to the generated WAV file.
    """
    speech_config = speechsdk.SpeechConfig(
        subscription=settings.azure_speech_key,
        region=settings.azure_speech_region,
    )
    speech_config.speech_synthesis_voice_name = settings.azure_tts_voice

    out_path = output_dir / f"tts_{uuid.uuid4().hex}.wav"
    audio_config = speechsdk.AudioOutputConfig(filename=str(out_path))

    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )

    result = synthesizer.speak_text_async(text).get()

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        return str(out_path)
    else:
        cancellation = result.cancellation_details
        raise RuntimeError(f"TTS failed: {cancellation.reason} — {cancellation.error_details}")
