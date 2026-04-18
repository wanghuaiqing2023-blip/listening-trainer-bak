"""Text utility functions."""
import re


def normalize_word(word: str) -> str:
    """Lowercase and strip punctuation for vocabulary lookup."""
    return re.sub(r"[^a-z'']", "", word.lower())


def extract_words(text: str) -> list[str]:
    """Extract normalized word list from a sentence."""
    tokens = re.findall(r"[A-Za-z'']+", text)
    return [normalize_word(t) for t in tokens if normalize_word(t)]


def dictation_accuracy(reference: str, hypothesis: str) -> float:
    """
    Compute word-level accuracy between reference and user hypothesis.
    Returns a float in [0, 1].
    """
    ref_words = extract_words(reference)
    hyp_words = extract_words(hypothesis)
    if not ref_words:
        return 1.0
    matches = sum(r == h for r, h in zip(ref_words, hyp_words))
    return matches / len(ref_words)
