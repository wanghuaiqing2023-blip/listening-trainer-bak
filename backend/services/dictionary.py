"""ECDICT-based English-Chinese dictionary service."""
from __future__ import annotations

import re
import sqlite3
from functools import lru_cache
from pathlib import Path

from backend.config import settings


def _get_conn() -> sqlite3.Connection:
    path = settings.ecdict_path
    if not Path(path).exists():
        raise FileNotFoundError(
            f"ECDICT database not found at {path}. "
            "Download ecdict-sqlite-28.zip from https://github.com/skywind3000/ECDICT/releases "
            "and place ecdict.db in the backend/ directory."
        )
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@lru_cache(maxsize=4096)
def lookup(word: str) -> dict | None:
    """
    Look up a word in ECDICT. Returns dict with phonetic and entries, or None if not found.
    Results are cached in-process (LRU, up to 4096 words).
    """
    word_clean = word.strip().lower()
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT word, phonetic, translation FROM stardict WHERE word = ? LIMIT 1",
            (word_clean,),
        ).fetchone()
        conn.close()
    except FileNotFoundError:
        return None

    if not row:
        return None

    return {
        "word": row["word"],
        "phonetic": row["phonetic"] or "",
        "entries": _parse_translation(row["translation"] or ""),
    }


# Matches lines like: "n. 含义" or "vt. 含义" or "abbr. 含义"
_POS_RE = re.compile(r"^([a-zA-Z]+\.)\s*(.*)")


def _parse_translation(translation: str) -> list[dict]:
    """Parse ECDICT translation field into [{pos, meanings}] list."""
    entries: list[dict] = []
    for line in translation.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        m = _POS_RE.match(line)
        if m:
            pos = m.group(1)
            rest = m.group(2)
        else:
            pos = ""
            rest = line
        meanings = [s.strip() for s in re.split(r"[；;]", rest) if s.strip()]
        if meanings:
            entries.append({"pos": pos, "meanings": meanings})
    return entries
