"""Audio utility functions: extraction, slicing, speed change, noise injection."""
import subprocess
import uuid
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf


def extract_audio_ffmpeg(source_path: str, output_dir: Path) -> str:
    """Convert any video/audio file to 16kHz mono WAV using ffmpeg."""
    out_path = output_dir / f"{uuid.uuid4().hex}.wav"
    cmd = [
        "ffmpeg", "-y", "-i", source_path,
        "-ac", "1",          # mono
        "-ar", "16000",      # 16 kHz (Whisper requirement)
        "-vn",               # no video
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")
    return str(out_path)


def slice_audio(audio_path: str, start: float, end: float, output_dir: Path) -> str:
    """Cut a segment [start, end] seconds from audio_path, save as WAV."""
    out_path = output_dir / f"{uuid.uuid4().hex}.wav"
    cmd = [
        "ffmpeg", "-y",
        "-i", audio_path,
        "-ss", str(start),
        "-to", str(end),
        "-ac", "1", "-ar", "16000",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg slice failed: {result.stderr}")
    return str(out_path)


def change_speed(audio_path: str, speed: float, output_dir: Path) -> str:
    """Return a new audio file at the given speed (e.g. 0.75 or 1.5)."""
    out_path = output_dir / f"{uuid.uuid4().hex}_speed{speed}.wav"
    # atempo supports 0.5-2.0; chain filters for extreme values
    if speed < 0.5:
        atempo = f"atempo=0.5,atempo={speed/0.5:.4f}"
    elif speed > 2.0:
        atempo = f"atempo=2.0,atempo={speed/2.0:.4f}"
    else:
        atempo = f"atempo={speed:.4f}"
    cmd = [
        "ffmpeg", "-y", "-i", audio_path,
        "-filter:a", atempo,
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg speed change failed: {result.stderr}")
    return str(out_path)


def add_noise(audio_path: str, snr_db: float, output_dir: Path) -> str:
    """Add white Gaussian noise at a given SNR (dB) to the audio."""
    y, sr = librosa.load(audio_path, sr=None, mono=True)
    signal_power = np.mean(y ** 2)
    noise_power = signal_power / (10 ** (snr_db / 10))
    noise = np.random.normal(0, np.sqrt(noise_power), len(y))
    noisy = (y + noise).astype(np.float32)
    out_path = output_dir / f"{uuid.uuid4().hex}_noisy.wav"
    sf.write(str(out_path), noisy, sr)
    return str(out_path)


def compute_snr(audio_path: str) -> float:
    """Estimate SNR in dB using the 10th percentile as noise floor."""
    y, _ = librosa.load(audio_path, sr=None, mono=True)
    signal_rms = float(np.sqrt(np.mean(y ** 2)))
    noise_floor = float(np.percentile(np.abs(y), 10)) + 1e-8
    snr = 20 * np.log10(signal_rms / noise_floor)
    return float(snr)
