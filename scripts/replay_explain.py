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

from backend.services import artifacts, openai_service


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay explanation generation only and emit detailed debug artifacts.",
    )
    parser.add_argument("--content-id", type=int, help="Content id to load the latest artifact run from.")
    parser.add_argument("--run-dir", type=Path, help="Artifact run directory containing segments-corrected.json.")
    parser.add_argument("--segments-file", type=Path, help="Direct path to segments-corrected.json.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Run artifact directory. Defaults to test_output/explain_runs/<timestamp>-<source>",
    )
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Only dump prompts and summary artifacts. Do not call the LLM.",
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        help="Only replay the first N batches (each batch is 30 sentences).",
    )
    return parser.parse_args()


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def resolve_source(
    content_id: int | None,
    run_dir: Path | None,
    segments_file: Path | None,
) -> tuple[Path, dict[str, Any], Path | None]:
    provided = [content_id is not None, run_dir is not None, segments_file is not None]
    if sum(provided) != 1:
        raise SystemExit("Provide exactly one of --content-id, --run-dir, or --segments-file.")

    if content_id is not None:
        latest = artifacts.find_latest_run_dir(content_id)
        if latest is None:
            raise SystemExit(f"No artifact directory found for content id={content_id}.")
        candidate = latest / "segments-corrected.json"
        if not candidate.exists():
            raise SystemExit(f"segments-corrected.json was not found in the latest run directory: {candidate}")
        return candidate, {
            "source": "content",
            "content_id": content_id,
            "run_dir": str(latest),
        }, latest

    if run_dir is not None:
        resolved_run = run_dir.expanduser().resolve()
        candidate = resolved_run / "segments-corrected.json"
        if not candidate.exists():
            raise SystemExit(f"segments-corrected.json was not found in the given run directory: {candidate}")
        return candidate, {
            "source": "run_dir",
            "run_dir": str(resolved_run),
        }, resolved_run

    resolved_file = segments_file.expanduser().resolve()
    if not resolved_file.exists():
        raise SystemExit(f"Segments file does not exist: {resolved_file}")
    return resolved_file, {
        "source": "segments_file",
        "segments_file": str(resolved_file),
    }, resolved_file.parent


def default_output_dir(source_meta: dict[str, Any]) -> Path:
    run_root = ROOT / "test_output" / "explain_runs"
    run_root.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    if source_meta.get("content_id") is not None:
        suffix = f"content-{source_meta['content_id']}"
    else:
        suffix = Path(source_meta.get("run_dir") or source_meta.get("segments_file")).name.replace(" ", "_")
    return run_root / f"{ts}-{suffix}"


def load_segments(path: Path) -> tuple[list[str], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    items = payload.get("segments")
    if not isinstance(items, list):
        raise SystemExit(f"Invalid segments file format. Missing segments array: {path}")

    sentences: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            raise SystemExit(f"Invalid segments file format. Found a non-object item: {path}")
        sentences.append(str(item.get("text", "")))

    return sentences, payload


def build_input_summary(
    all_sentences: list[str],
    replay_sentences: list[str],
) -> dict[str, Any]:
    full_text = " ".join(all_sentences)
    batch_size = openai_service.EXPLAIN_BATCH_SIZE
    total_batches = max(1, (len(replay_sentences) + batch_size - 1) // batch_size) if replay_sentences else 0

    prompt_sizes: list[int] = []
    batch_sentence_chars: list[int] = []
    for batch_start in range(0, len(replay_sentences), batch_size):
        batch = replay_sentences[batch_start: batch_start + batch_size]
        prompt = openai_service.build_explain_prompt(full_text, batch)
        prompt_sizes.append(len(prompt))
        batch_sentence_chars.append(sum(len(sentence) for sentence in batch))

    return {
        "source_sentence_count": len(all_sentences),
        "replay_sentence_count": len(replay_sentences),
        "batch_size": batch_size,
        "replay_batch_count": total_batches,
        "full_text_chars": len(full_text),
        "full_text_word_like_count": len(full_text.split()),
        "sentence_chars_total": sum(len(sentence) for sentence in replay_sentences),
        "batch_sentence_chars": batch_sentence_chars,
        "prompt_chars_per_batch": prompt_sizes,
        "prompt_chars_total": sum(prompt_sizes),
        "prompt_chars_min": min(prompt_sizes) if prompt_sizes else 0,
        "prompt_chars_max": max(prompt_sizes) if prompt_sizes else 0,
        "estimated_repeated_full_text_chars": len(full_text) * total_batches,
        "estimated_context_duplication_ratio": (
            (len(full_text) * total_batches) / max(1, sum(batch_sentence_chars))
        ) if total_batches else 0,
    }


def main() -> int:
    args = parse_args()
    segments_path, source_meta, source_dir = resolve_source(args.content_id, args.run_dir, args.segments_file)
    output_dir = args.output_dir.expanduser().resolve() if args.output_dir else default_output_dir(source_meta)
    output_dir.mkdir(parents=True, exist_ok=True)

    all_sentences, segments_payload = load_segments(segments_path)
    batch_size = openai_service.EXPLAIN_BATCH_SIZE
    replay_limit = len(all_sentences)
    if args.max_batches is not None:
        replay_limit = min(len(all_sentences), max(0, args.max_batches) * batch_size)
    replay_sentences = all_sentences[:replay_limit]
    full_text = " ".join(all_sentences)
    input_summary = build_input_summary(all_sentences, replay_sentences)

    write_json(output_dir / "01_source_meta.json", {
        "loaded_at": datetime.now().isoformat(),
        "source_meta": source_meta,
        "source_dir": str(source_dir) if source_dir else None,
        "segments_file": str(segments_path),
    })
    write_json(output_dir / "02_input_summary.json", input_summary)
    write_json(output_dir / "02_segments_meta.json", {
        "segment_count": segments_payload.get("segment_count"),
        "replay_sentence_count": len(replay_sentences),
        "first_sentences": replay_sentences[:5],
        "last_sentences": replay_sentences[-5:],
    })

    if not replay_sentences:
        write_json(output_dir / "10_run_summary.json", {
            "status": "no_sentences",
            "message": "No sentences available for replay.",
            "output_dir": str(output_dir),
        })
        print(f"[ERROR] No sentences available for replay. Details: {output_dir}")
        return 1

    if args.skip_llm:
        for batch_index, batch_start in enumerate(range(0, len(replay_sentences), batch_size), start=1):
            batch = replay_sentences[batch_start: batch_start + batch_size]
            prompt = openai_service.build_explain_prompt(full_text, batch)
            write_text(output_dir / f"03_batch_{batch_index:02d}_prompt.txt", prompt)
            write_json(output_dir / f"03_batch_{batch_index:02d}_prompt_meta.json", {
                "batch_index": batch_index,
                "batch_start": batch_start,
                "batch_end": batch_start + len(batch) - 1,
                "batch_size": len(batch),
                "sentence_chars": sum(len(sentence) for sentence in batch),
                "full_text_chars": len(full_text),
                "prompt_chars": len(prompt),
            })

        write_json(output_dir / "10_run_summary.json", {
            "status": "prepared",
            "message": "Explain replay artifacts generated. LLM call was skipped.",
            "output_dir": str(output_dir),
            "replay_sentence_count": len(replay_sentences),
            "replay_batch_count": input_summary["replay_batch_count"],
        })
        print(f"[INFO] Explain replay artifacts generated (LLM skipped). Directory: {output_dir}")
        return 0

    batch_outcomes: list[dict[str, Any]] = []

    def debug_callback(event: dict[str, Any]) -> None:
        batch_index = event["batch_index"]
        if event["type"] == "prompt":
            write_text(output_dir / f"03_batch_{batch_index:02d}_prompt.txt", event["prompt"])
            write_json(output_dir / f"03_batch_{batch_index:02d}_prompt_meta.json", {
                "batch_index": batch_index,
                "total_batches": event["total_batches"],
                "batch_start": event["batch_start"],
                "batch_end": event["batch_end"],
                "batch_size": event["batch_size"],
                "completed_before": event["completed_before"],
                "total_sentences": event["total_sentences"],
                "full_text_chars": event["full_text_chars"],
                "prompt_chars": event["prompt_chars"],
            })
        elif event["type"] == "response":
            write_text(output_dir / f"04_batch_{batch_index:02d}_raw_response.txt", event["raw_response"])
            write_json(output_dir / f"04_batch_{batch_index:02d}_response_meta.json", {
                "batch_index": batch_index,
                "total_batches": event["total_batches"],
                "batch_start": event["batch_start"],
                "batch_end": event["batch_end"],
                "batch_size": event["batch_size"],
                "elapsed_ms": event["elapsed_ms"],
                **event["response_meta"],
            })
        elif event["type"] == "result":
            payload = {
                "batch_index": batch_index,
                "total_batches": event["total_batches"],
                "batch_start": event["batch_start"],
                "batch_end": event["batch_end"],
                "batch_size": event["batch_size"],
                "elapsed_ms": event["elapsed_ms"],
                "used_fallback": event["used_fallback"],
                "parsed_count": event["parsed_count"],
                "completed_after": event["completed_after"],
                "result_preview": event["result_preview"],
            }
            batch_outcomes.append(payload)
            write_json(output_dir / f"05_batch_{batch_index:02d}_result_meta.json", payload)

    try:
        explanations = openai_service.explain_segments(
            full_text,
            replay_sentences,
            debug_callback=debug_callback,
        )
    except Exception as exc:
        write_json(output_dir / "09_error.json", {
            "error_type": type(exc).__name__,
            "error": str(exc),
        })
        write_json(output_dir / "10_run_summary.json", {
            "status": "llm_call_failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "output_dir": str(output_dir),
        })
        print(f"[ERROR] Explain replay failed. Details: {output_dir}")
        return 1

    stop_reasons = Counter()
    output_tokens_total = 0
    input_tokens_total = 0
    response_files = sorted(output_dir.glob("04_batch_*_response_meta.json"))
    for path in response_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        stop_reasons[payload.get("stop_reason") or "unknown"] += 1
        usage = payload.get("usage") or {}
        output_tokens_total += int(usage.get("output_tokens") or 0)
        input_tokens_total += int(usage.get("input_tokens") or 0)

    elapsed_values = [item["elapsed_ms"] for item in batch_outcomes]
    fallback_batches = [item["batch_index"] for item in batch_outcomes if item["used_fallback"]]

    write_json(output_dir / "11_explanations_preview.json", {
        "explanation_count": len(explanations),
        "first_explanations": explanations[:10],
        "last_explanations": explanations[-10:],
        "blank_explanation_count": sum(1 for item in explanations if not item),
    })

    write_json(output_dir / "10_run_summary.json", {
        "status": "success",
        "output_dir": str(output_dir),
        "replay_sentence_count": len(replay_sentences),
        "replay_batch_count": input_summary["replay_batch_count"],
        "fallback_batch_count": len(fallback_batches),
        "fallback_batches": fallback_batches,
        "stop_reason_counts": dict(stop_reasons),
        "input_tokens_total": input_tokens_total,
        "output_tokens_total": output_tokens_total,
        "elapsed_ms_total": sum(elapsed_values),
        "elapsed_ms_min": min(elapsed_values) if elapsed_values else 0,
        "elapsed_ms_max": max(elapsed_values) if elapsed_values else 0,
        "elapsed_ms_avg": (sum(elapsed_values) / len(elapsed_values)) if elapsed_values else 0,
        "estimated_repeated_full_text_chars": input_summary["estimated_repeated_full_text_chars"],
        "prompt_chars_total": input_summary["prompt_chars_total"],
    })
    print(f"[INFO] Explain replay succeeded. Debug directory: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
