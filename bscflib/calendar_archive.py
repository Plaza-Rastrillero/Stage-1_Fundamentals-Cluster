"""The calendar archive - the one thing this tool writes that it cannot re-read.

Nasdaq drops the before-open/after-close field once a date has passed, so the
timing of an earnings day has to be recorded before the day happens or it is
gone. Nothing else here is perishable - SEC facts can always be re-fetched -
which is why this archive exists, and why it never overwrites what it cannot
get back.

Split out of earnings_calendar because it is the opposite kind of job: that
module reads a list for the screen and forgets it, this one owns durable files
with their own atomicity, merge and never-overwrite rules. They share
`fetch_raw` and nothing else.

Merge rules, in one place:
  - a confirmed slot is never replaced by a later "not supplied"
  - a *different* confirmed slot does replace it, and every change is appended
    to timing_history with the time it was seen
  - the whole row is stored unedited, so a later question about a field nothing
    reads today can still be answered
  - a file that will not parse is left alone, not overwritten
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta
from typing import NamedTuple

import requests

from .earnings_calendar import fetch_raw, normalise_symbol, parse_timing
from .jsonio import write_json_atomic

CAPTURE_WINDOW_DAYS = 14
CAPTURE_INTERVAL = 0.3


class Capture(NamedTuple):
    """What one archive pass managed to record."""

    dates: int
    rows: int
    timed: int
    failures: int


def capture_path(directory: str, day: date) -> str:
    return os.path.join(directory, f"{day.isoformat()}.json")


def _merge(record: dict, raw_rows: list[dict], observed: str) -> int:
    """Fold one observation into a day's archive.

    Returns the number of companies the archive now holds a confirmed timing
    for - what was preserved, not what this fetch happened to see. The two
    differ exactly when Nasdaq retracts a slot it already gave, which is the
    case the never-overwrite rule below exists for.
    """
    companies = record.setdefault("companies", {})
    for raw in raw_rows:
        symbol = normalise_symbol(raw)
        if not symbol:
            continue
        entry = companies.get(symbol)
        if entry is None:
            entry = {"first_seen": observed, "timing": "", "timing_history": []}
            companies[symbol] = entry
        entry["last_seen"] = observed
        # The whole row, as sent. Its shape changes once the date passes -
        # `eps` and `surprise` replace `lastYearRptDt` and `lastYearEPS` - and
        # it is stored unedited either way.
        entry["row"] = raw
        timing = parse_timing(raw.get("time"))
        # A confirmed slot is never overwritten by a later "not supplied".
        # Nasdaq wipes `time` the moment the date passes, so without this rule
        # one run after the fact would erase the archive's whole reason for
        # existing. A *different* confirmed slot does replace it, because
        # companies really do move.
        if timing and timing != entry.get("timing"):
            entry["timing"] = timing
            entry["timing_observed"] = observed
            entry["timing_history"].append([observed, timing])
    return sum(1 for entry in companies.values() if entry.get("timing"))


def _archive_day(path: str, day: date, raw_rows: list[dict], observed: str) -> int | None:
    """Fold one day's rows into its file. None when the file was left alone.

    Never raises: the caller counts a None as a failure and moves on.
    """
    record: dict = {"date": day.isoformat()}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                record = json.load(handle)
        except (OSError, ValueError):
            # Refuse to overwrite an archive file that would not read. It is
            # the one thing in this repository that cannot be fetched again,
            # so a damaged file is left alone to be looked at.
            return None
    record.setdefault("date", day.isoformat())

    timed = _merge(record, raw_rows, observed)
    try:
        write_json_atomic(path, record, indent=1, sort_keys=True)
    except OSError:
        return None
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

    Never raises, for any reason: an incomplete archive is a cost, a run that
    dies before it reaches SEC is a bigger one. Callers do not need a guard.
    """
    start = start or date.today()
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        return Capture(dates=0, rows=0, timed=0, failures=days + 1)

    observed = datetime.now().astimezone().isoformat(timespec="seconds")
    dates = rows = timed = failures = 0

    for offset in range(days + 1):
        if offset:
            time.sleep(CAPTURE_INTERVAL)
        day = start + timedelta(days=offset)
        try:
            raw_rows = fetch_raw(day)
        except (requests.RequestException, ValueError):
            # Transport and malformed JSON only. A shape change raises out of
            # here on purpose: this module's whole premise is that a silent
            # change to the feed is the failure worth stopping for.
            failures += 1
            continue
        if not raw_rows:
            continue

        recorded = _archive_day(capture_path(directory, day), day, raw_rows, observed)
        if recorded is None:
            failures += 1
            continue
        timed += recorded
        dates += 1
        rows += len(raw_rows)

    return Capture(dates=dates, rows=rows, timed=timed, failures=failures)
