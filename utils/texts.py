from __future__ import annotations
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _load_texts() -> dict:
    texts_path = Path(__file__).resolve().parent.parent / "texts.json"
    try:
        with open(texts_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def t(key: str, **kwargs) -> str:
    """Fetch a text by key from texts.json and format it."""
    value = _load_texts().get(key, key)
    try:
        return value.format(**kwargs)
    except Exception:
        return value
