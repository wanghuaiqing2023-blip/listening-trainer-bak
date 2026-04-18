"""WhisperX transcription with word-level and phoneme-level alignment."""
from __future__ import annotations

import whisperx
import torch

from backend.config import settings

_model = None
_align_model = None
_align_metadata = None
_device = "cuda" if torch.cuda.is_available() else "cpu"
_compute_type = "float16" if _device == "cuda" else "int8"


def _load_model():
    global _model
    if _model is None:
        _model = whisperx.load_model(
            settings.whisper_model,
            _device,
            compute_type=_compute_type,
            language="en",
        )


def _load_align_model():
    global _align_model, _align_metadata
    if _align_model is None:
        _align_model, _align_metadata = whisperx.load_align_model(
            language_code="en",
            device=_device,
        )


def transcribe(audio_path: str) -> dict:
    """
    Transcribe audio and return WhisperX result with word-level timestamps.

    Returns:
        {
            "segments": [
                {
                    "text": str,
                    "start": float,
                    "end": float,
                    "words": [
                        {"word": str, "start": float, "end": float, "score": float}
                    ]
                }
            ]
        }
    """
    _load_model()
    _load_align_model()

    audio = whisperx.load_audio(audio_path)

    # Step 1: Transcribe
    result = _model.transcribe(audio, batch_size=16, language="en")

    # Step 2: Align word timestamps
    result = whisperx.align(
        result["segments"],
        _align_model,
        _align_metadata,
        audio,
        _device,
        return_char_alignments=False,
    )

    return result
