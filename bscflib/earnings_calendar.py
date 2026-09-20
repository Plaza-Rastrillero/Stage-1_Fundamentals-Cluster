"""Nasdaq's earnings calendar - the only non-SEC source in the tool.

Kept separate from sec_client because it is a different service with different
manners: it wants a browser User-Agent and an Origin header, it is uncached,
and it can simply stop answering without that being a SEC problem.

This module is the read path: the ticker list a --date screen runs on. The
archive that preserves those rows lives in calendar_archive, because it is a
different kind of job - a durable write with its own atomicity and
never-overwrite rules - and the two only share `fetch_raw`.

Nasdaq fills in `time` - before-open vs after-close - only while a date is
still in the future, and replaces it with "time-not-supplied" once the date
has passed. Measured Sep 2026: of 5,253 historical rows spanning Oct 2025 to
Sep 2026, none carried a timing value. Coverage on a future date also improves
as the date approaches and companies confirm: ~4% of screened names six weeks
out, ~90% inside two weeks. That is what calendar_archive exists to catch.
"""

from __future__ import annotations

import re
from datetime import date
from typing import NamedTuple

import requests

CALENDAR_URL = "https://api.nasdaq.com/api/calendar/earnings"
TIMEOUT = 20
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# Nasdaq has only ever sent a dollar-prefixed, comma-grouped whole number:
# "$24,715,929,600". Checked over 5,557 rows across 37 dates from Oct 2025 to
# Nov 2026 - no decimals, no abbreviations ("$1.2B"), no currency other than
# USD (foreign filers are quoted at their US listing), no non-ASCII characters.
#
# This is a tripwire, not a parser. It exists because the endpoint is the
# website's own rather than a published API and can change shape without
# notice, and a format change that slips through silently is worse than one
# that stops the line.
#
# The leading \d is load-bearing: it makes the pattern the single thing that
# decides whether a string is a figure, so the parse below needs no guards of
# its own. It also means comma-leading junk ("$,,5") trips the wire instead of
# quietly parsing as 5.
MARKET_CAP_PATTERN = re.compile(r"^\$?\s*\d[\d,]*(?:\.\d+)?$")

# The values Nasdaq uses, normalised. Anything else is passed through unchanged
# rather than folded into "", so a new value shows up in the data instead of
# disappearing into it.
TIMING = {
    "time-pre-market": "pre-market",
    "time-after-hours": "after-hours",
    "time-not-supplied": "",
}

NOT_SUPPLIED = {"N/A", "NA", "-", "--"}


class Row(NamedTuple):
    """One company on the calendar, parsed for the screen.

    market_cap_raw is None unless the value failed MARKET_CAP_PATTERN, in
    which case it holds the text Nasdaq actually sent, for reporting.
    """

    symbol: str
    market_cap: float | None
    market_cap_raw: str | None
    timing: str


def parse_market_cap(raw: object) -> tuple[float | None, str | None]:
    """(value, unrecognised text) for Nasdaq's display-string market cap.

    Three outcomes, and both of the None ones mean "unknown", never "small":

        "$24,715,929,600"  -> (24715929600.0, None)   a figure
        "" / None / "N/A"  -> (None, None)            not supplied
        "$1.23B"           -> (None, "$1.23B")        the format changed

    The third case is why this returns a pair. Stripping non-digits out of
    "$1.23B" yields 1.23, which --min-cap reads as a company worth a dollar and
    drops from the screen without a word - a silent exclusion off a misread,
    which is the failure this tool is least able to notice. Unknown is kept and
    looked up at SEC; only a figure that actually parsed may exclude anything.

    Blanks are real and ordinary: SPACs and commodity trusts (LKSP, GTEN, BAR)
    file earnings with no market cap on the row.
    """
    text = str(raw if raw is not None else "").strip()
    if not text or text.upper() in NOT_SUPPLIED:
        return None, None
    if not MARKET_CAP_PATTERN.match(text):
        return None, text
    # The pattern guarantees a leading digit and at most one decimal point, so
    # what is left after stripping is always a parseable float.
    return float(re.sub(r"[^0-9.]", "", text)), None


def parse_timing(raw: object) -> str:
    """Normalise Nasdaq's time field; "" when it has not been told yet."""
    text = str(raw if raw is not None else "").strip()
    return TIMING.get(text, text)


def normalise_symbol(raw: dict) -> str:
    """The row's ticker, upper-cased and trimmed. "" when there is not one.

    `symbol` is load-bearing: a row without one is not a company anything can
    look up, and a whitespace-only one is not either.
    """
    return str(raw.get("symbol") or "").strip().upper()


def fetch_raw(day: date) -> list[dict]:
    """The raw Nasdaq rows for `day`, verbatim. [] when nothing is scheduled."""
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
    payload = response.json() or {}
    # A weekend, holiday or far-out date answers 200 with a null where the data
    # should be, in either of two shapes: {"data": null} (seen on 2026-12-25)
    # and {"data": {"rows": null}} (seen on 2026-09-13, a Sunday). Both are
    # real, and .get(key, {}) catches neither - the key is present, its value
    # is None - so each step needs the `or`.
    data = payload.get("data") or {}
    return data.get("rows") or []


def fetch(day: date) -> list[Row]:
    """Every company reporting on `day`."""
    rows: list[Row] = []
    for raw in fetch_raw(day):
        symbol = normalise_symbol(raw)
        if not symbol:
            continue
        market_cap, unrecognised = parse_market_cap(raw.get("marketCap"))
        rows.append(
            Row(
                symbol=symbol,
                market_cap=market_cap,
                market_cap_raw=unrecognised,
                timing=parse_timing(raw.get("time")),
            )
        )
    return rows
