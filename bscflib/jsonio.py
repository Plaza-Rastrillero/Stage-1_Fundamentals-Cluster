"""Atomic JSON writes - one discipline for every file this tool owns.

Both on-disk writers (the SEC cache and the calendar archive) go through here,
so an interrupted run can never leave a half-written file behind. The archive
needs this because its files cannot be re-fetched at any price; the cache does
not, strictly, because a corrupt cache file self-heals into a re-fetch - but
two writers with two disciplines is how the careful one eventually gets edited
into the careless one.
"""

from __future__ import annotations

import json
import os
from typing import Any


def write_json_atomic(path: str, payload: Any, **dump_kwargs: Any) -> None:
    """Write `payload` to `path` via a temporary file in the same directory."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, **dump_kwargs)
    os.replace(temporary, path)
