"""Download audio (and subtitles) from YouTube using yt-dlp."""
import re
import subprocess
import sys
import uuid
from pathlib import Path

from backend.config import settings


def _yt_dlp_base_command() -> list[str]:
    """
    Run yt-dlp through the current Python interpreter so we do not depend on a
    globally available `yt-dlp` executable in PATH.
    """
    return [sys.executable, "-m", "yt_dlp"]


def download_youtube_audio_and_subs(url: str) -> tuple[str, list[dict] | None]:
    """
    Download audio + subtitles from a YouTube URL.
    Returns (audio_path, subtitle_lines).
    subtitle_lines is a list of {start, end, text} dicts, or None if unavailable.
    """
    out_dir = settings.uploads_dir
    stem = uuid.uuid4().hex
    out_template = str(out_dir / f"{stem}.%(ext)s")
    expected_wav = out_dir / f"{stem}.wav"

    cmd = [
        *_yt_dlp_base_command(),
        "--no-playlist",
        "-x",
        "--audio-format", "wav",
        "--postprocessor-args", "ffmpeg:-ac 1 -ar 16000",
        "--write-subs",           # manual subtitles first
        "--write-auto-subs",      # fallback to auto-generated
        "--sub-lang", "en",
        "--convert-subs", "srt",  # always output as clean SRT
        "-o", out_template,
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "yt-dlp is unavailable in the current Python environment. "
            "Please install project dependencies and retry."
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr[-500:]}")

    # Find audio file
    if not expected_wav.exists():
        matches = list(out_dir.glob(f"{stem}.*"))
        audio_matches = [m for m in matches if m.suffix in (".wav", ".mp3", ".m4a")]
        if not audio_matches:
            raise RuntimeError("yt-dlp did not produce an audio file")
        audio_path = str(audio_matches[0])
    else:
        audio_path = str(expected_wav)

    # Find subtitle file — yt-dlp names them like {stem}.en.srt or {stem}.en-orig.srt
    srt_files = sorted(out_dir.glob(f"{stem}.*.srt"))
    subtitle_lines = None
    if srt_files:
        subtitle_lines = parse_srt(str(srt_files[0]))
        for f in srt_files:
            f.unlink(missing_ok=True)

    return audio_path, subtitle_lines


def download_youtube_audio(url: str) -> str:
    """Download audio only (used for backward-compat). Returns local WAV path."""
    audio_path, _ = download_youtube_audio_and_subs(url)
    return audio_path


def parse_srt(path: str) -> list[dict]:
    """
    Parse an SRT file into a list of {start, end, text} dicts (times in seconds).
    Strips HTML/styling tags and skips empty lines.
    """
    content = Path(path).read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"\n\s*\n", content.strip())
    lines = []

    for block in blocks:
        block_lines = [l.strip() for l in block.strip().splitlines()]
        if len(block_lines) < 3:
            continue

        # Second line of block must be the timestamp line
        time_match = re.match(
            r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2}[,\.]\d{3})",
            block_lines[1],
        )
        if not time_match:
            continue

        start = _srt_to_seconds(time_match.group(1))
        end = _srt_to_seconds(time_match.group(2))
        text = " ".join(block_lines[2:])
        text = re.sub(r"<[^>]+>", "", text).strip()  # strip timing/colour tags

        if text:
            lines.append({"start": start, "end": end, "text": text})

    # YouTube auto-captions use a rolling-window format that produces
    # heavily overlapping lines. Detect and clean up if needed.
    if _is_rolling_window(lines):
        lines = _deduplicate_rolling_window(lines)

    return lines


def _is_rolling_window(lines: list[dict]) -> bool:
    """
    Rolling-window format has many near-zero-duration entries (~0.01s)
    that mark the overlap between consecutive full-display entries.
    """
    if len(lines) < 4:
        return False
    short = sum(1 for l in lines if l["end"] - l["start"] < 0.15)
    return short / len(lines) > 0.2


def _deduplicate_rolling_window(lines: list[dict]) -> list[dict]:
    """
    Remove rolling-window duplication.
    Strategy:
    1. Drop near-zero-duration entries (they are the overlap markers).
    2. For each remaining entry, strip the prefix that was already
       present in the previous entry (using word-level suffix/prefix match).
    """
    # Step 1: keep only meaningful-duration entries
    meaningful = [l for l in lines if l["end"] - l["start"] >= 0.15]
    if not meaningful:
        return lines

    # Step 2: extract only the new text added in each entry
    result = []
    for i, line in enumerate(meaningful):
        if i == 0:
            result.append(line)
            continue

        new_text = _extract_new_text(result[-1]["text"], line["text"])
        if new_text:
            result.append({"start": line["start"], "end": line["end"], "text": new_text})
        else:
            # Entire line is a duplicate — skip it
            pass

    return result


def _extract_new_text(prev: str, curr: str) -> str:
    """
    Return the part of `curr` that does not overlap with the end of `prev`.
    Finds the longest suffix of prev_words that matches a prefix of curr_words.
    """
    prev_words = prev.split()
    curr_words = curr.split()

    best_overlap = 0
    max_check = min(len(prev_words), len(curr_words))
    for overlap in range(max_check, 0, -1):
        if prev_words[-overlap:] == curr_words[:overlap]:
            best_overlap = overlap
            break

    new_words = curr_words[best_overlap:]
    return " ".join(new_words)


def _srt_to_seconds(t: str) -> float:
    """Convert SRT timestamp HH:MM:SS,mmm (or .mmm) to float seconds."""
    t = t.replace(",", ".")
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def get_youtube_title(url: str) -> str:
    """Fetch the video title without downloading."""
    try:
        result = subprocess.run(
            [*_yt_dlp_base_command(), "--get-title", "--no-playlist", url],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return "YouTube Video"
