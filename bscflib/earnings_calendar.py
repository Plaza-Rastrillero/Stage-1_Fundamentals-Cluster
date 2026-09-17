"""Nasdaq's earnings calendar - the only non-SEC source in the tool.

Kept separate from sec_client because it is a different service with different
manners: it wants a browser User-Agent and an Origin header, it is uncached,
and it can simply stop answering without that being a SEC problem.

Two jobs:

    fetch(day)          the ticker list a --date screen runs on
    capture(directory)  an archive of the raw rows, because one field in them
                        is perishable

Nasdaq fills in `time` - before-open vs after-close - only while a date is
still in the future, and replaces it with "time-not-supplied" once the date
has passed. Measured Sep 2026: of 5,253 historical rows spanning Oct 2025 to
Sep 2026, none carried a timing value. Coverage on a future date also improves
as the date approaches and companies confirm: ~4% of screened names six weeks
out, ~90% inside two weeks.

So the timing of an earnings day has to be recorded before the day happens or
it is gone. Nothing else here is perishable - SEC facts can always be
re-fetched - which is why the archive exists, and why it never overwrites what
it cannot re-read.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime, timedelta
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
MARKET_CAP_PATTERN = re.compile(r"^\$?\s*[\d,]+(?:\.\d+)?$")

# The three values Nasdaq uses, normalised. Anything else is passed through
# unchanged rather than folded into "", so a new value shows up in the data
# instead of disappearing into it.
TIMING = {
    "time-pre-market": "pre-market",
    "time-after-hours": "after-hours",
    "time-not-supplied": "",
    "": "",
}

CAPTURE_WINDOW_DAYS = 14
CAPTURE_INTERVAL = 0.3


class Row(NamedTuple):
    """One company on the calendar, parsed for the screen.

    market_cap_raw is None unless the value failed MARKET_CAP_PATTERN, in
    which case it holds the text Nasdaq actually sent, for reporting.
    """

    symbol: str
    market_cap: float | None
    market_cap_raw: str | None
    timing: str


class Capture(NamedTuple):
    """What one archive pass managed to record."""

    dates: int
    rows: int
    timed: int
    failures: int


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
    if not text or text.upper() in {"N/A", "NA", "-", "--"}:
        return None, None
    if not MARKET_CAP_PATTERN.match(text):
        return None, text
    digits = re.sub(r"[^0-9.]", "", text)
    if not digits:
        return None, text
    try:
        return float(digits), None
    except ValueError:
        return None, text


def parse_timing(raw: object) -> str:
    """Normalise Nasdaq's time field; "" when it has not been told yet."""
    text = str(raw if raw is not None else "").strip()
    return TIMING.get(text, text)


def _get(day: date) -> list[dict]:
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
    for raw in _get(day):
        # `symbol` is load-bearing and a row without one is not a company we
        # can look up; market cap is advisory and may be missing.
        if not raw.get("symbol"):
            continue
        market_cap, unrecognised = parse_market_cap(raw.get("marketCap"))
        rows.append(
            Row(
                symbol=str(raw["symbol"]).strip().upper(),
                market_cap=market_cap,
                market_cap_raw=unrecognised,
                timing=parse_timing(raw.get("time")),
            )
        )
    return rows


def capture_path(directory: str, day: date) -> str:
    return os.path.join(directory, f"{day.isoformat()}.json")


def _merge(record: dict, raw_rows: list[dict], observed: str) -> int:
    """Fold one observation into a day's archive. Returns rows with a timing."""
    companies = record.setdefault("companies", {})
    timed = 0
    for raw in raw_rows:
        symbol = str(raw.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        entry = companies.get(symbol)
        if entry is None:
            entry = {"first_seen": observed, "timing": "", "timing_history": []}
            companies[symbol] = entry
        entry["last_seen"] = observed
        # The whole row, as sent. Its shape changes once the date passes -
        # `eps` and `surprise` replace `lastYearRptDt` and `lastYearEPS` - and
        # it is stored unedited either way, so a later question about a field
        # nothing reads today can still be answered from the archive.
        entry["row"] = raw
        timing = parse_timing(raw.get("time"))
        if timing:
            timed += 1
        # A confirmed slot is never overwritten by a later "not supplied".
        # Nasdaq wipes `time` the moment the date passes, so without this rule
        # one run after the fact would erase the archive's whole reason for
        # existing. A *different* confirmed slot does replace it, because
        # companies really do move, and every change is appended with the time
        # it was seen rather than overwriting the one before.
        if timing and timing != entry.get("timing"):
            entry["timing"] = timing
            entry["timing_observed"] = observed
            entry["timing_history"].append([observed, timing])
    return timed


def capture(
    directory: str, start: date | None = None, days: int = CAPTURE_WINDOW_DAYS
) -> Capture:
    """Archive the raw calendar for `start` through `start + days`.

    Anchored on today rather than on whatever date is being screened, because
    what it preserves is a property of now: timing fills in as a date nears and
    is gone once it passes. Archiving the window rather than the single date
    under screen means each upcoming day is observed on every run between now
    and the day it happens, so a slot confirmed after the last screen is still
    recorded - and the archive does not depend on having screened that exact
    date at exactly the right moment.

    Never raises for a date it could not fetch or read. An incomplete archive
    is a cost; a run that dies before it reaches SEC is a bigger one.
    """
    start = start or date.today()
    os.makedirs(directory, exist_ok=True)
    observed = datetime.now().astimezone().isoformat(timespec="seconds")
    dates = rows = timed = failures = 0

    for offset in range(days + 1):
        if offset:
            time.sleep(CAPTURE_INTERVAL)
        day = start + timedelta(days=offset)
        try:
            raw_rows = _get(day)
        except Exception:  # noqa: BLE001 - the archive never ends a run
            failures += 1
            continue
        if not raw_rows:
            continue

        path = capture_path(directory, day)
        record: dict = {"date": day.isoformat()}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    record = json.load(handle)
            except (OSError, ValueError):
                # Refuse to overwrite an archive file that would not read. It
                # is the one thing in this repository that cannot be fetched
                # again, so a damaged file is left alone to be looked at.
                failures += 1
                continue
        record.setdefault("date", day.isoformat())

        timed += _merge(record, raw_rows, observed)
        # Written via a temporary file so an interrupted run cannot leave a
        # half-written archive behind.
        temporary = f"{path}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=1, sort_keys=True)
        os.replace(temporary, path)
        dates += 1
        rows += len(raw_rows)

    return Capture(dates=dates, rows=rows, timed=timed, failures=failures)
