from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import SessionLocal
from backend.models import Content
from backend.services import segmenter, transcriber


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run WhisperX transcription only and save a reusable fixture JSON.",
    )
    parser.add_argument("--content-id", type=int, help="Existing content id with a usable audio_path.")
    parser.add_argument("--audio-path", type=Path, help="Local WAV/audio path to transcribe.")
    parser.add_argument(
        "--output",
        type=Path,
        help="Fixture output path. Defaults to test_output/segmentation_fixtures/<timestamp>.json",
    )
    return parser.parse_args()


def resolve_audio_source(content_id: int | None, audio_path: Path | None) -> tuple[Path, dict]:
    if content_id is None and audio_path is None:
        raise SystemExit("请提供 --content-id 或 --audio-path。")
    if content_id is not None and audio_path is not None:
        raise SystemExit("--content-id 和 --audio-path 只能二选一。")

    if audio_path is not None:
        resolved = audio_path.expanduser().resolve()
        if not resolved.exists():
            raise SystemExit(f"音频文件不存在: {resolved}")
        return resolved, {
            "source": "audio_path",
            "audio_path": str(resolved),
        }

    db = SessionLocal()
    try:
        content = db.get(Content, content_id)
        if not content:
            raise SystemExit(f"未找到 content id={content_id}")
        if not content.audio_path:
            raise SystemExit(f"content id={content_id} 没有可复用的 audio_path")
        resolved = Path(content.audio_path).expanduser().resolve()
        if not resolved.exists():
            raise SystemExit(f"content id={content_id} 的音频文件不存在: {resolved}")
        return resolved, {
            "source": "content",
            "content_id": content.id,
            "title": content.title,
            "status": content.status,
            "source_type": content.source_type,
            "source_path": content.source_path,
            "audio_path": str(resolved),
        }
    finally:
        db.close()


def default_output_path(source_meta: dict) -> Path:
    fixture_dir = ROOT / "test_output" / "segmentation_fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = f"content-{source_meta['content_id']}" if source_meta.get("content_id") else "audio"
    return fixture_dir / f"{ts}-{suffix}.json"


def summarize_validation(whisperx_result: dict) -> dict:
    try:
        warnings = segmenter.validate_transcription_result(whisperx_result)
        warning_counts = Counter(issue["type"] for issue in warnings)
        return {
            "ok": True,
            "warning_count": len(warnings),
            "warning_type_counts": dict(warning_counts),
            "warnings": warnings,
        }
    except segmenter.SegmentationValidationError as exc:
        error_counts = Counter(issue["type"] for issue in exc.issues)
        return {
            "ok": False,
            "error_count": len(exc.issues),
            "error_type_counts": dict(error_counts),
            "errors": exc.issues,
        }


def main() -> int:
    args = parse_args()
    audio_path, source_meta = resolve_audio_source(args.content_id, args.audio_path)
    output_path = (args.output.expanduser().resolve() if args.output else default_output_path(source_meta))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] 音频来源: {audio_path}")
    print("[INFO] 开始执行 WhisperX 转录，仅导出 fixture...")

    last_printed_percent = -1

    def on_progress(percent: float, message: str) -> None:
        nonlocal last_printed_percent
        rounded = int(percent)
        if rounded == last_printed_percent and rounded not in {0, 100}:
            return
        last_printed_percent = rounded
        print(f"[TRANSCRIBE] {rounded:3d}% {message}")

    whisperx_result = transcriber.transcribe(str(audio_path), on_progress)
    validation = summarize_validation(whisperx_result)

    payload = {
        "meta": {
            "generated_at": datetime.now().isoformat(),
            "audio_path": str(audio_path),
            "source_meta": source_meta,
            "validation": validation,
        },
        "whisperx_result": whisperx_result,
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"[INFO] Fixture 已写入: {output_path}")
    print(json.dumps(payload["meta"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
