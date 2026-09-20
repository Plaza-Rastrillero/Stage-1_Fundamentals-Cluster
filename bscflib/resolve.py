"""Turning a companyfacts payload into numbers.

Everything here enforces the rules that stop a figure being quietly wrong:
one balance sheet date per company, the most recently filed value when several
filings report that date, the right taxonomy for a foreign filer, and one
currency throughout.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from functools import lru_cache
from typing import Iterator, NamedTuple

from .tags import ANCHORS, BSCF_FIELDS, INSTANT_FIELDS, SWEEP_EXCLUDE_PATTERNS


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


@dataclass(frozen=True)
class Fact:
    end: date
    val: float
    filed: str


@dataclass(frozen=True)
class Resolution:
    """One field's figure at one date, with the tags that produced it.

    Frozen on purpose: this is what `resolve` hands to `formula`, and the
    combined-debt netting used to edit it in place, so nothing in this file
    told you that `value` was not final.
    """

    value: float
    tags: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    stale: tuple[date, float] | None = None  # found, but at the wrong date

    @property
    def resolved(self) -> bool:
        """True when a tag actually supplied this figure at the target date.

        A Resolution with no tags is the stale case: the filer published the
        figure, but not at the date being read, so it is visibly excluded
        rather than counted - invariant 1.3 rule 3. Seven call sites used to
        spell this out by hand as `line and line.tags`.
        """
        return bool(self.tags)

    def with_note(self, note: str) -> Resolution:
        return replace(self, notes=self.notes + (note,))

    def less(self, amount: float, note: str) -> Resolution:
        """This figure reduced by `amount`, clamped at nil, saying which."""
        if self.value - amount < 0:
            return replace(
                self, value=0.0,
                notes=self.notes + (note, "netted below zero; treated as nil"),
            )
        return replace(self, value=self.value - amount, notes=self.notes + (note,))


Index = dict[str, dict[date, Fact]]


class Basis(NamedTuple):
    """The one namespace, unit and date the whole company is read at."""

    namespace: str
    unit: str
    as_of: date
    filed: str
    index: Index


def _instants(
    facts: dict, namespace: str, tag: str, unit: str | None = None
) -> Iterator[dict]:
    """Every instant observation for one tag: no `start`, and a `val`.

    This is the one payload shape that namespace selection, unit selection,
    the filed-date lookup and the sweep each used to spell out as its own
    four-level nested walk. `unit=None` means every unit that tag carries.
    """
    units = facts.get(namespace, {}).get(tag, {}).get("units", {})
    streams = [units.get(unit, [])] if unit is not None else units.values()
    for observations in streams:
        for observation in observations:
            if observation.get("start") is None and observation.get("val") is not None:
                yield observation


@lru_cache(maxsize=None)
def parse_rung(rung: str) -> tuple[tuple[str, ...], ...]:
    """The two-operator rung syntax -> slots of alternative tags.

    Cached because the ladders are static module data: without it the same
    handful of strings is re-split for every field, at every date in the trend
    window, on both passes of every lookup.
    """
    return tuple(tuple(slot.split("|")) for slot in rung.split(" + "))


def _ladder_tags(ladder: list[str]) -> Iterator[str]:
    """Every tag named anywhere in a ladder, in ladder order."""
    for rung in ladder:
        for slot in parse_rung(rung):
            yield from slot


def index_instants(facts: dict, namespace: str, unit: str) -> Index:
    """tag -> {balance sheet date: fact}, keeping the most recently filed value.

    Original filing, restatement, amendment and prior-period comparative all
    report the same period end. The latest filing is the live one.
    """
    index: Index = {}
    for tag in facts.get(namespace, {}):
        best: dict[date, Fact] = {}
        for observation in _instants(facts, namespace, tag, unit):
            if not observation.get("filed"):
                continue
            end = parse_date(observation["end"])
            current = best.get(end)
            if current is None or observation["filed"] > current.filed:
                best[end] = Fact(end, float(observation["val"]), observation["filed"])
        if best:
            index[tag] = best
    return index


def resolve_instant(
    index: Index, namespace: str, fieldname: str, as_of: date
) -> Resolution | None:
    """Walk one field's ladder at a single date. Only that date counts."""
    ladder = INSTANT_FIELDS[fieldname].get(namespace, [])
    for rung in ladder:
        total, tags = 0.0, []
        for slot in parse_rung(rung):
            for tag in slot:
                fact = index.get(tag, {}).get(as_of)
                if fact is not None:
                    total += fact.val
                    tags.append(tag)
                    break
        if tags:
            notes = []
            if len(tags) > 1:
                notes.append(f"sum of {len(tags)} separately reported components")
            if any("CapitalLease" in tag for tag in tags):
                notes.append("caption bundles finance leases with borrowings")
            return Resolution(total, tuple(tags), tuple(notes))

    # Nothing at as_of. Report the newest older value so a figure the filer did
    # publish is visibly excluded rather than silently read as absent.
    newest: tuple[date, float] | None = None
    for tag in _ladder_tags(ladder):
        for when, fact in index.get(tag, {}).items():
            if when < as_of and (newest is None or when > newest[0]):
                newest = (when, fact.val)
    return Resolution(0.0, stale=newest) if newest else None


def anchor_dates(index: Index, namespace: str) -> list[date]:
    """Every balance sheet date the filer has published, oldest first."""
    dates: set[date] = set()
    for anchor in ANCHORS[namespace]:
        dates.update(index.get(anchor, {}))
    return sorted(dates)


def choose_basis(facts: dict) -> Basis | None:
    """Namespace, unit, date and index to read everything at.

    A foreign private issuer's us-gaap facts are often frozen years before its
    live IFRS ones, so the namespace is chosen by which one reports the newest
    balance sheet, never by which one happens to exist.

    Returns the index it had to build to score the units, because `analyze`
    would otherwise immediately rebuild the same multi-megabyte walk.
    """
    best_ns, best_date = None, None
    for namespace in ANCHORS:
        if namespace not in facts:
            continue
        latest = max(
            (parse_date(observation["end"])
             for anchor in ANCHORS[namespace]
             for observation in _instants(facts, namespace, anchor)),
            default=None,
        )
        if latest and (best_date is None or latest > best_date):
            best_ns, best_date = namespace, latest
    if best_ns is None:
        return None

    # One unit for the whole report: a filer publishing both its functional
    # currency and a convenience translation covers different tags in each, and
    # mixing them would add New Taiwan dollars to US dollars. The unit that
    # covers the most of the formula wins, with USD breaking ties.
    units = {
        unit
        for anchor in ANCHORS[best_ns]
        for unit in facts[best_ns].get(anchor, {}).get("units", {})
    }
    scored = []
    for unit in units:
        index = index_instants(facts, best_ns, unit)
        covered = sum(
            1 for name in BSCF_FIELDS
            if resolve_instant(index, best_ns, name, best_date) is not None
        )
        scored.append((covered, unit == "USD", unit, index))
    if not scored:
        return None
    # Ranked on the first three only - an Index is not orderable.
    _, _, unit, index = max(scored, key=lambda entry: entry[:3])

    filed = max(
        (observation.get("filed", "")
         for anchor in ANCHORS[best_ns]
         for observation in _instants(facts, best_ns, anchor, unit)
         if observation["end"] == best_date.isoformat()),
        default="",
    )
    return Basis(best_ns, unit, best_date, filed, index)


def sweep_unclassified(
    facts: dict,
    namespace: str,
    unit: str,
    as_of: date,
    patterns: tuple[str, ...],
    counted_values: frozenset[float],
    used_tags: frozenset[str],
    extra_excludes: tuple[str, ...] = (),
) -> list[tuple[str, float]]:
    """Balances in a category that no ladder captured, largest first.

    Two exclusions, because filers hide the same balance two ways. `used_tags`
    drops the exact tags the ladders consumed; `counted_values` drops the
    alternate spellings most filers publish of those same amounts.
    """
    excludes = SWEEP_EXCLUDE_PATTERNS + extra_excludes
    candidates = []
    for tag in facts.get(namespace, {}):
        if tag in used_tags:
            continue
        lowered = tag.lower()
        if not any(p in lowered for p in patterns):
            continue
        if any(p in lowered for p in excludes):
            continue
        # The first instant observation at this date, which is what the old
        # loop's `break` took: a tag restating the same date twice is one
        # candidate, not two.
        observation = next(
            (o for o in _instants(facts, namespace, tag, unit)
             if o.get("end") == as_of.isoformat()),
            None,
        )
        if observation is None:
            continue
        value = float(observation["val"])
        if value > 0 and value not in counted_values:
            candidates.append((tag, value))

    seen: set[float] = set()
    unique = []
    for tag, value in sorted(candidates, key=lambda c: -c[1]):
        if value in seen:
            continue
        seen.add(value)
        unique.append((tag, value))
    return unique
