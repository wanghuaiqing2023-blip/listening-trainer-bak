"""WhisperX transcription with word-level and phoneme-level alignment."""
from __future__ import annotations

from pathlib import Path
import shutil
from typing import Callable

import torch
import torchaudio
import whisperx
import whisperx.alignment
from faster_whisper.utils import download_model as download_whisper_model

from backend.config import settings

_model = None
_align_model = None
_align_metadata = None
_device = "cuda" if torch.cuda.is_available() else "cpu"
_compute_type = "float16" if _device == "cuda" else "int8"

_WHISPER_REQUIRED_FILES = (
    "config.json",
    "model.bin",
    "tokenizer.json",
)
TranscribeProgressCallback = Callable[[float, str], None]


def get_local_whisper_model_dir() -> Path:
    configured_value = str(settings.whisper_model).strip()
    configured_path = Path(configured_value)
    if configured_path.is_dir():
        return configured_path
    return settings.whisper_assets_dir / configured_value


def get_align_model_dir() -> Path:
    return settings.align_assets_dir


def get_align_bundle_name() -> str:
    bundle_name = whisperx.alignment.DEFAULT_ALIGN_MODELS_TORCH.get("en")
    if not bundle_name:
        raise RuntimeError("WhisperX 没有提供英文对齐模型配置。")
    return bundle_name


def get_align_bundle_file() -> Path:
    bundle_name = get_align_bundle_name()
    if bundle_name not in torchaudio.pipelines.__all__:
        raise RuntimeError(f"不支持的 torchaudio 对齐模型: {bundle_name}")
    bundle = torchaudio.pipelines.__dict__[bundle_name]
    return get_align_model_dir() / bundle._path


def get_global_align_cache_file() -> Path:
    bundle_name = get_align_bundle_name()
    if bundle_name not in torchaudio.pipelines.__all__:
        raise RuntimeError(f"不支持的 torchaudio 对齐模型: {bundle_name}")
    bundle = torchaudio.pipelines.__dict__[bundle_name]
    return Path(torch.hub.get_dir()) / "checkpoints" / bundle._path


def whisper_model_installed() -> bool:
    model_dir = get_local_whisper_model_dir()
    return model_dir.is_dir() and all((model_dir / name).exists() for name in _WHISPER_REQUIRED_FILES)


def align_model_installed() -> bool:
    return get_align_bundle_file().exists()


def ensure_local_model_assets() -> None:
    if not whisper_model_installed():
        raise RuntimeError(
            "Whisper 模型未安装到本地目录。"
            f" 期望目录: {get_local_whisper_model_dir()}"
            "。请先运行 `.\\.venv\\Scripts\\python.exe scripts\\install_models.py`。"
        )

    if not align_model_installed():
        raise RuntimeError(
            "英文对齐模型未安装到本地目录。"
            f" 期望文件: {get_align_bundle_file()}"
            "。请先运行 `.\\.venv\\Scripts\\python.exe scripts\\install_models.py`。"
        )


def install_models() -> dict:
    """
    Download required WhisperX assets into project-local model directories.
    This is an explicit setup action and should not run inside the business flow.
    """
    whisper_dir = get_local_whisper_model_dir()
    align_dir = get_align_model_dir()
    whisper_dir.mkdir(parents=True, exist_ok=True)
    align_dir.mkdir(parents=True, exist_ok=True)

    if not whisper_model_installed():
        download_whisper_model(
            settings.whisper_model,
            output_dir=str(whisper_dir),
            cache_dir=str(settings.model_assets_dir / "hf-cache"),
        )

    align_file = get_align_bundle_file()
    global_align_cache = get_global_align_cache_file()
    if not align_file.exists() and global_align_cache.exists():
        shutil.copy2(global_align_cache, align_file)

    if not align_file.exists():
        whisperx.load_align_model(
            language_code="en",
            device="cpu",
            model_dir=str(align_dir),
        )

    return {
        "whisper_model": settings.whisper_model,
        "whisper_dir": str(whisper_dir),
        "align_bundle": get_align_bundle_name(),
        "align_file": str(align_file),
        "global_align_cache": str(global_align_cache),
    }


def _load_model():
    global _model
    if _model is None:
        ensure_local_model_assets()
        _model = whisperx.load_model(
            str(get_local_whisper_model_dir()),
            _device,
            compute_type=_compute_type,
            language="en",
            local_files_only=True,
        )


def _load_align_model():
    global _align_model, _align_metadata
    if _align_model is None:
        ensure_local_model_assets()
        _align_model, _align_metadata = whisperx.load_align_model(
            language_code="en",
            device=_device,
            model_dir=str(get_align_model_dir()),
            model_cache_only=True,
        )


def prewarm() -> dict:
    """
    Load already-installed local models into memory so the first real task does
    not pay initialization cost.
    """
    _load_model()
    _load_align_model()
    return {
        "whisper_model": settings.whisper_model,
        "whisper_dir": str(get_local_whisper_model_dir()),
        "align_file": str(get_align_bundle_file()),
        "device": _device,
        "compute_type": _compute_type,
        "align_language": "en",
    }


def _emit_progress(
    callback: TranscribeProgressCallback | None,
    percent: float,
    message: str,
) -> None:
    if callback is None:
        return
    bounded = max(0.0, min(100.0, float(percent)))
    callback(bounded, message)


def transcribe(
    audio_path: str,
    progress_callback: TranscribeProgressCallback | None = None,
) -> dict:
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

    _emit_progress(progress_callback, 5, "正在加载音频...")
    audio = whisperx.load_audio(audio_path)

    # Step 1: Transcribe
    _emit_progress(progress_callback, 10, "正在检测语音片段...")
    result = _model.transcribe(
        audio,
        batch_size=16,
        language="en",
        progress_callback=lambda p: _emit_progress(
            progress_callback,
            10 + p * 0.55,
            f"正在转录语音 {round(p)}%",
        ),
    )

    # Step 2: Align word timestamps
    _emit_progress(progress_callback, 70, "正在对齐单词时间戳...")
    result = whisperx.align(
        result["segments"],
        _align_model,
        _align_metadata,
        audio,
        _device,
        return_char_alignments=False,
        progress_callback=lambda p: _emit_progress(
            progress_callback,
            70 + p * 0.30,
            f"正在对齐时间戳 {round(p)}%",
        ),
    )
    _emit_progress(progress_callback, 100, "转录完成")

    return result
