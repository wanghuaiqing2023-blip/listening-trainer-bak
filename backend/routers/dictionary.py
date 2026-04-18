"""Dictionary lookup endpoint."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.services.dictionary import lookup

router = APIRouter(prefix="/dictionary", tags=["dictionary"])


@router.get("/{word}")
def get_word(word: str):
    """Look up an English word. Returns phonetic and Chinese definitions."""
    result = lookup(word)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Word '{word}' not found")
    return result
