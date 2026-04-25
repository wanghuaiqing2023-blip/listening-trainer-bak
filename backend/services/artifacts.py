from __future__ import annotations

import json
import shutil
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.config import settings


def summarize_issue_types(issues: list[dict]) -> dict[str, int]:
    return dict(Counter(issue.get("type", "unknown") for issue in issues))


def get_content_artifact_root(content_id: int) -> Path:
    return settings.artifacts_dir / f"content_{content_id}"


def _root_has_artifact_files(root: Path) -> bool:
    if not root.exists():
        return False
    return any(path.is_file() for path in root.iterdir())


def find_latest_run_dir(content_id: int) -> Path | None:
    root = get_content_artifact_root(content_id)
    if not root.exists():
        return None

    # New layout: resumable artifacts live directly under content_<id>/.
    # If files already exist there, always prefer that stable directory.
    if _root_has_artifact_files(root):
        return root

    # Backward compatibility for legacy timestamped run directories.
    legacy_run_dirs = sorted(
        (
            path
            for path in root.iterdir()
            if path.is_dir() and path.name[:8].isdigit()
        ),
        key=lambda path: path.name,
        reverse=True,
    )
    return legacy_run_dirs[0] if legacy_run_dirs else root


def read_json(run_dir: Path | None, filename: str) -> Any | None:
    if run_dir is None:
        return None
    path = run_dir / filename
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def read_text(run_dir: Path | None, filename: str) -> str | None:
    if run_dir is None:
        return None
    path = run_dir / filename
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def serialize_segment_data_list(segments: list[Any]) -> list[dict]:
    payload: list[dict] = []
    for index, segment in enumerate(segments):
        words = list(getattr(segment, "words", []) or [])
        payload.append({
            "index": index,
            "text": getattr(segment, "text", ""),
            "start": getattr(segment, "start", None),
            "end": getattr(segment, "end", None),
            "word_count": len(words),
            "words": words,
            "explanation": getattr(segment, "explanation", ""),
        })
    return payload


def _serialize_datetime(value: Any) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def serialize_saved_segments(segments: list[Any]) -> list[dict]:
    payload: list[dict] = []
    for segment in segments:
        payload.append({
            "id": getattr(segment, "id", None),
            "content_id": getattr(segment, "content_id", None),
            "index": getattr(segment, "index", None),
            "text": getattr(segment, "text", ""),
            "start_time": getattr(segment, "start_time", None),
            "end_time": getattr(segment, "end_time", None),
            "audio_path": getattr(segment, "audio_path", ""),
            "diff_speech_rate": getattr(segment, "diff_speech_rate", None),
            "diff_phonetics": getattr(segment, "diff_phonetics", None),
            "diff_vocabulary": getattr(segment, "diff_vocabulary", None),
            "diff_complexity": getattr(segment, "diff_complexity", None),
            "diff_audio_quality": getattr(segment, "diff_audio_quality", None),
            "diff_total": getattr(segment, "diff_total", None),
            "phonetic_annotations": list(getattr(segment, "phonetic_annotations", []) or []),
            "word_timestamps": list(getattr(segment, "word_timestamps", []) or []),
            "explanation": getattr(segment, "explanation", ""),
        })
    return payload


def serialize_vocabulary_entries(entries: list[Any]) -> list[dict]:
    payload: list[dict] = []
    for entry in entries:
        payload.append({
            "id": getattr(entry, "id", None),
            "user_id": getattr(entry, "user_id", None),
            "word": getattr(entry, "word", ""),
            "mastery_prob": getattr(entry, "mastery_prob", None),
            "encounters": getattr(entry, "encounters", None),
            "correct_count": getattr(entry, "correct_count", None),
            "last_seen": _serialize_datetime(getattr(entry, "last_seen", None)),
            "created_at": _serialize_datetime(getattr(entry, "created_at", None)),
        })
    return sorted(payload, key=lambda item: item.get("word", ""))


@dataclass
class ContentArtifactRun:
    content_id: int
    run_dir: Path
    summary: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        content_id: int,
        title: str,
        source_type: str,
        source_path: str,
        audio_path: str,
    ) -> "ContentArtifactRun":
        content_root = settings.artifacts_dir / f"content_{content_id}"
        content_root.mkdir(parents=True, exist_ok=True)

        run = cls(content_id=content_id, run_dir=content_root)
        run.write_json("run-meta.json", {
            "content_id": content_id,
            "title": title,
            "source_type": source_type,
            "source_path": source_path,
            "audio_path": audio_path,
            "run_dir": str(content_root),
            "started_at": datetime.now().isoformat(),
        })
        run.update_summary(
            status="running",
            current_step="",
            error="",
            finished_at=None,
            step_states={},
        )
        return run

    def write_json(self, filename: str, payload: Any) -> Path:
        path = self.run_dir / filename
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def write_text(self, filename: str, text: str) -> Path:
        path = self.run_dir / filename
        path.write_text(text, encoding="utf-8")
        return path

    def copy_file(self, source_path: str | Path | None, filename_stem: str) -> Path | None:
        if not source_path:
            return None

        source = Path(source_path)
        if not source.exists() or not source.is_file():
            return None

        suffix = "".join(source.suffixes) or source.suffix
        destination = self.run_dir / f"{filename_stem}{suffix}"
        if source.resolve() == destination.resolve():
            return destination

        shutil.copy2(source, destination)
        return destination

    def update_summary(self, **fields: Any) -> None:
        self.summary.update(fields)
        self.summary["content_id"] = self.content_id
        self.summary["run_dir"] = str(self.run_dir)
        self.summary["updated_at"] = datetime.now().isoformat()
        self.write_json("run-summary.json", self.summary)
