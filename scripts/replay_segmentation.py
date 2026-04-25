from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services import openai_service, segmenter


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay segmentation only and emit detailed debug artifacts.",
    )
    parser.add_argument("--fixture", type=Path, required=True, help="Path to whisperx fixture JSON.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Run artifact directory. Defaults to test_output/segmentation_runs/<timestamp>-<fixture-name>",
    )
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Only dump prompt/candidates/summary, do not call the LLM.",
    )
    return parser.parse_args()


def load_fixture(path: Path) -> tuple[dict, dict]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise SystemExit(f"fixture 不存在: {resolved}")
    payload = json.loads(resolved.read_text(encoding="utf-8-sig"))
    if "whisperx_result" in payload:
        return payload["whisperx_result"], payload.get("meta", {})
    return payload, {}


def default_output_dir(fixture_path: Path) -> Path:
    run_root = ROOT / "test_output" / "segmentation_runs"
    run_root.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_name = fixture_path.stem.replace(" ", "_")
    return run_root / f"{ts}-{safe_name}"


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def extract_all_words(whisperx_result: dict) -> list[dict]:
    words: list[dict] = []
    for segment in whisperx_result.get("segments", []):
        for word in segment.get("words", []):
            if isinstance(word, dict) and "start" in word and "end" in word:
                words.append(word)
    return words


def summarize_transcription(whisperx_result: dict, all_words: list[dict]) -> dict:
    segments = whisperx_result.get("segments", [])
    token_samples = []
    for idx, word in enumerate(all_words[:20]):
        token_samples.append({
            "index": idx,
            "word": word.get("word"),
            "start": word.get("start"),
            "end": word.get("end"),
        })

    tail_samples = []
    tail_start = max(0, len(all_words) - 20)
    for idx in range(tail_start, len(all_words)):
        word = all_words[idx]
        tail_samples.append({
            "index": idx,
            "word": word.get("word"),
            "start": word.get("start"),
            "end": word.get("end"),
        })

    try:
        warnings = segmenter.validate_transcription_result(whisperx_result)
        warning_counts = Counter(issue["type"] for issue in warnings)
        validation = {
            "ok": True,
            "warning_count": len(warnings),
            "warning_type_counts": dict(warning_counts),
            "warnings": warnings,
        }
    except segmenter.SegmentationValidationError as exc:
        error_counts = Counter(issue["type"] for issue in exc.issues)
        validation = {
            "ok": False,
            "error_count": len(exc.issues),
            "error_type_counts": dict(error_counts),
            "errors": exc.issues,
        }

    return {
        "segment_count": len(segments),
        "timed_token_count": len(all_words),
        "first_tokens": token_samples,
        "last_tokens": tail_samples,
        "validation": validation,
    }


def build_candidate_details(all_words: list[dict], candidate_boundaries: list[int]) -> dict:
    payload = openai_service.build_segment_boundary_candidate_payload(all_words, candidate_boundaries)
    gap_counter = {"gte_120ms": 0, "gte_200ms": 0, "gte_500ms": 0}
    for item in payload:
        gap_ms = item.get("gap_after_ms")
        if gap_ms is None:
            continue
        if gap_ms >= 120:
            gap_counter["gte_120ms"] += 1
        if gap_ms >= 200:
            gap_counter["gte_200ms"] += 1
        if gap_ms >= 500:
            gap_counter["gte_500ms"] += 1
    return {
        "candidate_count": len(candidate_boundaries),
        "candidate_ratio": (len(candidate_boundaries) / len(all_words)) if all_words else 0,
        "gap_buckets": gap_counter,
        "candidates": payload,
    }


def count_problem_types(problems: list[dict]) -> dict[str, int]:
    return dict(Counter(problem.get("type", "unknown") for problem in problems))


def build_segment_preview(boundaries: list[int], all_words: list[dict]) -> list[dict]:
    segments = segmenter._segments_from_boundaries(boundaries, all_words)
    segmenter._adjust_boundaries(segments)
    preview: list[dict] = []
    for idx, item in enumerate(segments):
        preview.append({
            "index": idx,
            "text": item.text,
            "start": item.start,
            "end": item.end,
            "token_count": len(item.words),
            "first_token": item.words[0].get("word") if item.words else None,
            "last_token": item.words[-1].get("word") if item.words else None,
        })
    return preview


def main() -> int:
    args = parse_args()
    fixture_path = args.fixture.expanduser().resolve()
    output_dir = (args.output_dir.expanduser().resolve() if args.output_dir else default_output_dir(fixture_path))
    output_dir.mkdir(parents=True, exist_ok=True)

    whisperx_result, fixture_meta = load_fixture(fixture_path)
    all_words = extract_all_words(whisperx_result)
    transcription_summary = summarize_transcription(whisperx_result, all_words)

    write_json(output_dir / "01_fixture_meta.json", {
        "fixture_path": str(fixture_path),
        "loaded_at": datetime.now().isoformat(),
        "fixture_meta": fixture_meta,
    })
    write_json(output_dir / "02_transcription_summary.json", transcription_summary)

    if not transcription_summary["validation"]["ok"]:
        write_json(output_dir / "10_run_summary.json", {
            "status": "transcription_invalid",
            "message": "WhisperX timing/token validation failed before segmentation replay.",
            "output_dir": str(output_dir),
        })
        print(f"[ERROR] 转录材料本身无效，详情见: {output_dir}")
        return 1

    candidate_boundaries = segmenter.build_candidate_boundaries(all_words)
    candidate_details = build_candidate_details(all_words, candidate_boundaries)
    write_json(output_dir / "03_candidate_boundaries.json", candidate_details)

    if args.skip_llm:
        prompt = openai_service.build_segment_boundary_prompt(all_words, candidate_boundaries)
        write_text(output_dir / "04_prompt_attempt_01.txt", prompt)
        write_json(output_dir / "05_prompt_meta_attempt_01.json", {
            "attempt": 1,
            "prompt_chars": len(prompt),
            "token_count": len(all_words),
            "candidate_count": len(candidate_boundaries),
            "requested_max_tokens": openai_service.SEGMENT_BOUNDARY_MAX_TOKENS,
            "has_retry_feedback": False,
        })
        write_json(output_dir / "10_run_summary.json", {
            "status": "prepared",
            "message": "Prompt and candidate artifacts generated. LLM call was skipped.",
            "output_dir": str(output_dir),
        })
        print(f"[INFO] 已生成观察点目录（未调用 LLM）: {output_dir}")
        return 0

    problems: list[dict] | None = None
    final_boundaries: list[int] = []
    raw_responses: list[str] = []
    attempt_summaries: list[dict] = []

    for attempt in range(1, segmenter.MAX_BOUNDARY_REVISIONS + 1):
        prompt = openai_service.build_segment_boundary_prompt(
            all_words,
            candidate_boundaries,
            problems,
        )
        write_text(output_dir / f"04_prompt_attempt_{attempt:02d}.txt", prompt)
        write_json(output_dir / f"05_prompt_meta_attempt_{attempt:02d}.json", {
            "attempt": attempt,
            "prompt_chars": len(prompt),
            "token_count": len(all_words),
            "candidate_count": len(candidate_boundaries),
            "requested_max_tokens": openai_service.SEGMENT_BOUNDARY_MAX_TOKENS,
            "has_retry_feedback": bool(problems),
            "previous_problem_type_counts": count_problem_types(problems or []),
        })

        try:
            response_payload = openai_service._chat_with_metadata(
                prompt,
                temperature=0.1,
                max_tokens=openai_service.SEGMENT_BOUNDARY_MAX_TOKENS,
            )
        except Exception as exc:
            write_json(output_dir / f"06_llm_error_attempt_{attempt:02d}.json", {
                "attempt": attempt,
                "error_type": type(exc).__name__,
                "error": str(exc),
            })
            write_json(output_dir / "10_run_summary.json", {
                "status": "llm_call_failed",
                "attempt": attempt,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "output_dir": str(output_dir),
            })
            print(f"[ERROR] LLM 调用失败，详情见: {output_dir}")
            return 1

        raw_text = response_payload["text"]
        raw_responses.append(raw_text)
        write_json(output_dir / f"06_llm_response_meta_attempt_{attempt:02d}.json", {
            "attempt": attempt,
            **response_payload["response_meta"],
        })
        write_text(output_dir / f"06_llm_raw_response_attempt_{attempt:02d}.txt", raw_text)

        final_boundaries = openai_service.parse_segment_boundary_response(raw_text)
        write_json(output_dir / f"07_parsed_boundaries_attempt_{attempt:02d}.json", {
            "attempt": attempt,
            "boundaries": final_boundaries,
            "boundary_count": len(final_boundaries),
        })

        problems = segmenter.validate_boundaries(
            final_boundaries,
            token_count=len(all_words),
            candidate_boundaries=candidate_boundaries,
        )
        write_json(output_dir / f"08_validation_problems_attempt_{attempt:02d}.json", {
            "attempt": attempt,
            "problem_count": len(problems),
            "problem_type_counts": count_problem_types(problems),
            "problems": problems,
        })

        attempt_summaries.append({
            "attempt": attempt,
            "boundary_count": len(final_boundaries),
            "problem_count": len(problems),
            "problem_type_counts": count_problem_types(problems),
            "response_stop_reason": response_payload["response_meta"].get("stop_reason"),
            "response_output_tokens": (
                (response_payload["response_meta"].get("usage") or {}).get("output_tokens")
            ),
            "response_content_block_count": response_payload["response_meta"].get("content_block_count"),
            "response_text_block_count": response_payload["response_meta"].get("text_block_count"),
            "response_text_chars": response_payload["response_meta"].get("text_chars"),
        })
        if not problems:
            break

        for problem in problems:
            problem["attempt"] = attempt

    success = not problems
    if success:
        segment_preview = build_segment_preview(final_boundaries, all_words)
        write_json(output_dir / "09_segments_preview.json", {
            "segment_count": len(segment_preview),
            "segments": segment_preview,
        })

    write_json(output_dir / "10_run_summary.json", {
        "status": "success" if success else "invalid_boundaries",
        "attempt_count": len(attempt_summaries),
        "token_count": len(all_words),
        "candidate_count": len(candidate_boundaries),
        "final_boundaries": final_boundaries,
        "attempt_summaries": attempt_summaries,
        "last_problem_type_counts": count_problem_types(problems or []),
        "output_dir": str(output_dir),
    })

    if success:
        print(f"[INFO] 语义切分回放成功，调试目录: {output_dir}")
        return 0

    print(f"[WARN] LLM boundary output is invalid，调试目录: {output_dir}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
