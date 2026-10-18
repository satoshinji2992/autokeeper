"""Versioned, tested Python extensions for scenario and improvised rules."""

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def api_text() -> str:
    return Path(__file__).with_name("API.md").read_text(encoding="utf-8")
