"""
Run chunk extraction on sample sentences or user-provided sentences.

Examples:
    python test_chunks.py
    python test_chunks.py --sentence "At the end of the day, we need to decide what matters most."
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from backend.services.chunking import extract_chunks


DEFAULT_SENTENCES = [
    "At the end of the day, we need to decide what matters most.",
    "It took me a long time to figure out what he was trying to say.",
    "I mean, if you really want to do it, you should just start now.",
    "The thing is, we don't actually know why they left so early.",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Test sentence chunk extraction.")
    parser.add_argument(
        "--sentence",
        action="append",
        dest="sentences",
        help="Sentence to test. Repeat to provide multiple sentences.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print full JSON results.",
    )
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="Use the online LLM extractor instead of local heuristic chunking.",
    )
    args = parser.parse_args()

    sentences = args.sentences or DEFAULT_SENTENCES

    for index, sentence in enumerate(sentences, start=1):
        result = extract_chunks(sentence, use_llm=args.use_llm)
        print(f"\n[{index}] {sentence}")
        print(f"Raw chunks: {result['raw_chunks']}")
        for chunk_index, chunk in enumerate(result["chunks"], start=1):
            print(
                f"  {chunk_index}. {chunk['text']} "
                f"[{chunk['start_word']}-{chunk['end_word']}]"
            )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
