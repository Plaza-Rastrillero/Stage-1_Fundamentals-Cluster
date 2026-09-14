"""Nasdaq's earnings calendar - the only non-SEC source in the tool.

Kept separate from sec_client because it is a different service with different
manners: it wants a browser User-Agent and an Origin header, it is uncached,
and it can simply stop answering without that being a SEC problem.
"""

from __future__ import annotations

import re
from datetime import date

import requests

CALENDAR_URL = "https://api.nasdaq.com/api/calendar/earnings"
TIMEOUT = 20
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


def parse_market_cap(raw: object) -> float | None:
    """Nasdaq reports market cap as a display string like '$1,234,567,890'."""
    digits = re.sub(r"[^0-9.]", "", str(raw or ""))
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


def fetch(day: date) -> list[tuple[str, float | None]]:
    """(ticker, market cap) for every company reporting on `day`."""
    headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.nasdaq.com",
        "Referer": "https://www.nasdaq.com/",
    }
    response = requests.get(
        CALENDAR_URL, params={"date": day.isoformat()}, headers=headers, timeout=TIMEOUT
    )
    response.raise_for_status()
    rows = ((response.json() or {}).get("data") or {}).get("rows") or []
    return [
        (row["symbol"].strip().upper(), parse_market_cap(row.get("marketCap")))
        for row in rows
        if row.get("symbol")
    ]
