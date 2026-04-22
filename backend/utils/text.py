"""Text and dictation analysis utilities."""
from __future__ import annotations

import re

WORD_PATTERN = re.compile(r"[A-Za-z0-9']+")


def normalize_word(word: str) -> str:
    """Lowercase and strip punctuation for stable word matching."""
    return re.sub(r"[^a-z0-9']+", "", word.lower())


def tokenize_words(text: str) -> list[dict]:
    """Tokenize a sentence into word objects used for alignment."""
    tokens: list[dict] = []
    for match in WORD_PATTERN.finditer(text):
        original = match.group(0)
        normalized = normalize_word(original)
        if not normalized:
            continue
        tokens.append(
            {
                "index": len(tokens),
                "text": original,
                "normalized": normalized,
                "char_start": match.start(),
                "char_end": match.end(),
            }
        )
    return tokens


def extract_words(text: str) -> list[str]:
    """Extract normalized word list from a sentence."""
    return [token["normalized"] for token in tokenize_words(text)]


def _align_token_sequences(ref_tokens: list[dict], hyp_tokens: list[dict]) -> list[dict]:
    """Align two token sequences with Levenshtein backtracking."""
    ref_norm = [token["normalized"] for token in ref_tokens]
    hyp_norm = [token["normalized"] for token in hyp_tokens]
    n = len(ref_norm)
    m = len(hyp_norm)

    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = i
    for j in range(1, m + 1):
        dp[0][j] = j

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref_norm[i - 1] == hyp_norm[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = min(
                    dp[i - 1][j - 1] + 1,
                    dp[i - 1][j] + 1,
                    dp[i][j - 1] + 1,
                )

    operations: list[dict] = []
    i = n
    j = m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref_norm[i - 1] == hyp_norm[j - 1] and dp[i][j] == dp[i - 1][j - 1]:
            operations.append(
                {
                    "type": "equal",
                    "ref_index": i - 1,
                    "hyp_index": j - 1,
                }
            )
            i -= 1
            j -= 1
            continue

        if i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            operations.append(
                {
                    "type": "replace",
                    "ref_index": i - 1,
                    "hyp_index": j - 1,
                }
            )
            i -= 1
            j -= 1
            continue

        if i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            operations.append(
                {
                    "type": "missing",
                    "ref_index": i - 1,
                    "hyp_index": None,
                }
            )
            i -= 1
            continue

        operations.append(
            {
                "type": "extra",
                "ref_index": None,
                "hyp_index": j - 1,
            }
        )
        j -= 1

    operations.reverse()
    return operations


def _build_token_views(
    ref_tokens: list[dict],
    hyp_tokens: list[dict],
    operations: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Attach correctness status to reference and hypothesis tokens."""
    reference_view = [
        {
            "text": token["text"],
            "normalized": token["normalized"],
            "status": "unseen",
            "partner_text": "",
            "index": token["index"],
        }
        for token in ref_tokens
    ]
    hypothesis_view = [
        {
            "text": token["text"],
            "normalized": token["normalized"],
            "status": "unseen",
            "partner_text": "",
            "index": token["index"],
        }
        for token in hyp_tokens
    ]

    for op in operations:
        ref_index = op["ref_index"]
        hyp_index = op["hyp_index"]
        op_type = op["type"]

        if op_type == "equal":
            reference_view[ref_index]["status"] = "correct"
            reference_view[ref_index]["partner_text"] = hyp_tokens[hyp_index]["text"]
            hypothesis_view[hyp_index]["status"] = "correct"
            hypothesis_view[hyp_index]["partner_text"] = ref_tokens[ref_index]["text"]
        elif op_type == "replace":
            reference_view[ref_index]["status"] = "replace"
            reference_view[ref_index]["partner_text"] = hyp_tokens[hyp_index]["text"]
            hypothesis_view[hyp_index]["status"] = "replace"
            hypothesis_view[hyp_index]["partner_text"] = ref_tokens[ref_index]["text"]
        elif op_type == "missing":
            reference_view[ref_index]["status"] = "missing"
        elif op_type == "extra":
            hypothesis_view[hyp_index]["status"] = "extra"

    for token in reference_view:
        if token["status"] == "unseen":
            token["status"] = "correct"
    for token in hypothesis_view:
        if token["status"] == "unseen":
            token["status"] = "correct"

    return reference_view, hypothesis_view


def _build_error_groups(
    ref_tokens: list[dict],
    hyp_tokens: list[dict],
    operations: list[dict],
) -> list[dict]:
    """Merge consecutive non-equal operations into span-like errors."""
    errors: list[dict] = []
    pending: list[dict] = []

    def flush_pending() -> None:
        if not pending:
            return

        ref_indices = [op["ref_index"] for op in pending if op["ref_index"] is not None]
        hyp_indices = [op["hyp_index"] for op in pending if op["hyp_index"] is not None]

        if ref_indices and hyp_indices:
            error_type = "replace"
        elif ref_indices:
            error_type = "missing"
        else:
            error_type = "extra"

        errors.append(
            {
                "type": error_type,
                "reference_text": " ".join(ref_tokens[i]["text"] for i in ref_indices),
                "user_text": " ".join(hyp_tokens[i]["text"] for i in hyp_indices),
                "reference_start": ref_indices[0] if ref_indices else None,
                "reference_end": ref_indices[-1] if ref_indices else None,
                "user_start": hyp_indices[0] if hyp_indices else None,
                "user_end": hyp_indices[-1] if hyp_indices else None,
                "reference_count": len(ref_indices),
                "user_count": len(hyp_indices),
            }
        )
        pending.clear()

    for op in operations:
        if op["type"] == "equal":
            flush_pending()
            continue
        pending.append(op)

    flush_pending()
    return errors


def analyze_dictation(reference: str, hypothesis: str) -> dict:
    """Return detailed dictation alignment, error spans, and metrics."""
    ref_tokens = tokenize_words(reference)
    hyp_tokens = tokenize_words(hypothesis)
    operations = _align_token_sequences(ref_tokens, hyp_tokens)
    reference_view, hypothesis_view = _build_token_views(ref_tokens, hyp_tokens, operations)
    errors = _build_error_groups(ref_tokens, hyp_tokens, operations)

    reference_word_count = len(ref_tokens)
    hypothesis_word_count = len(hyp_tokens)
    correct_word_count = sum(1 for op in operations if op["type"] == "equal")
    edit_count = sum(1 for op in operations if op["type"] != "equal")

    if reference_word_count == 0:
        accuracy = 1.0 if hypothesis_word_count == 0 else 0.0
        correct_ratio = accuracy
        word_error_rate = 0.0 if hypothesis_word_count == 0 else 1.0
    else:
        word_error_rate = edit_count / reference_word_count
        accuracy = max(0.0, 1.0 - word_error_rate)
        correct_ratio = correct_word_count / reference_word_count

    return {
        "reference": reference,
        "hypothesis": hypothesis,
        "accuracy": round(accuracy, 3),
        "correct_ratio": round(correct_ratio, 3),
        "word_error_rate": round(word_error_rate, 3),
        "reference_word_count": reference_word_count,
        "hypothesis_word_count": hypothesis_word_count,
        "correct_word_count": correct_word_count,
        "edit_count": edit_count,
        "error_count": len(errors),
        "errors": errors,
        "reference_tokens": reference_view,
        "hypothesis_tokens": hypothesis_view,
    }


def dictation_accuracy(reference: str, hypothesis: str) -> float:
    """Compute alignment-based dictation accuracy in [0, 1]."""
    return analyze_dictation(reference, hypothesis)["accuracy"]
