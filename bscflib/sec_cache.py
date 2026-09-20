"""On-disk cache for SEC responses.

A single companyfacts payload runs to several megabytes, so a 300-name earnings
day is gigabytes of repeat download every time a threshold is tuned. Staleness
costs almost nothing here: a company reporting today has not filed its new 10-Q
yet, so the figures being screened are last quarter's either way.

Cache files land in "SEC cache" next to bscf.py and are safe to delete at any
time - the next run just re-fetches. --refresh bypasses the cache for one run.

Renaming that folder is fine, but this constant has to follow it or the next run
silently re-downloads everything into a fresh folder under the old name.
"""

from __future__ import annotations

import hashlib
import json
import os
import time

from .jsonio import write_json_atomic

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(PROJECT_ROOT, "SEC cache")
CACHE_TTL_HOURS = 24


def cache_path(url: str) -> str:
    return os.path.join(CACHE_DIR, hashlib.sha1(url.encode()).hexdigest()[:16] + ".json")


def load(url: str, refresh: bool = False) -> dict | None:
    """Cached payload for a URL, or None if absent, expired or unreadable."""
    if refresh:
        return None
    path = cache_path(url)
    if not os.path.exists(path):
        return None
    if time.time() - os.path.getmtime(path) >= CACHE_TTL_HOURS * 3600:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None  # half-written or corrupt; the caller re-fetches


def store(url: str, payload: dict) -> None:
    write_json_atomic(cache_path(url), payload)
