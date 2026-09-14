"""Turning a companyfacts payload into numbers.

Everything here enforces the rules that stop a figure being quietly wrong:
one balance sheet date per company, the most recently filed value when several
filings report that date, the right taxonomy for a foreign filer, and one
currency throughout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from .tags import ANCHORS, BSCF_FIELDS, INSTANT_FIELDS, SWEEP_EXCLUDE_PATTERNS


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


@dataclass
class Fact:
    end: date
    val: float
    filed: str


@dataclass
class Resolution:
    value: float
    tags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    stale: tuple[date, float] | None = None  # found, but at the wrong date


def index_instants(facts: dict, namespace: str, unit: str) -> dict[str, dict[date, Fact]]:
    """tag -> {balance sheet date: fact}, keeping the most recently filed value.

    Original filing, restatement, amendment and prior-period comparative all
    report the same period end. The latest filing is the live one.
    """
    index: dict[str, dict[date, Fact]] = {}
    for tag, entry in facts.get(namespace, {}).items():
        best: dict[date, Fact] = {}
        for observation in entry.get("units", {}).get(unit, []):
            if observation.get("start") is not None or observation.get("val") is None:
                continue
            if not observation.get("filed"):
                continue
            end = parse_date(observation["end"])
            current = best.get(end)
            if current is None or observation["filed"] > current.filed:
                best[end] = Fact(end, float(observation["val"]), observation["filed"])
        if best:
            index[tag] = best
    return index


def parse_rung(rung: str) -> list[list[str]]:
    return [slot.split("|") for slot in rung.split(" + ")]


def resolve_instant(
    index: dict[str, dict[date, Fact]], namespace: str, fieldname: str, as_of: date
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
            return Resolution(total, tags, notes)

    # Nothing at as_of. Report the newest older value so a figure the filer did
    # publish is visibly excluded rather than silently read as absent.
    newest: tuple[date, float] | None = None
    for rung in ladder:
        for slot in parse_rung(rung):
            for tag in slot:
                for when, fact in index.get(tag, {}).items():
                    if when < as_of and (newest is None or when > newest[0]):
                        newest = (when, fact.val)
    return Resolution(0.0, [], [], stale=newest) if newest else None


def anchor_dates(index: dict[str, dict[date, Fact]], namespace: str) -> list[date]:
    """Every balance sheet date the filer has published, oldest first."""
    dates: set[date] = set()
    for anchor in ANCHORS[namespace]:
        dates.update(index.get(anchor, {}))
    return sorted(dates)


def choose_basis(facts: dict) -> tuple[str, str, date, str] | None:
    """Namespace, unit and balance sheet date to read everything at.

    A foreign private issuer's us-gaap facts are often frozen years before its
    live IFRS ones, so the namespace is chosen by which one reports the newest
    balance sheet, never by which one happens to exist.
    """
    best_ns, best_date = None, None
    for namespace in ANCHORS:
        if namespace not in facts:
            continue
        latest = None
        for anchor in ANCHORS[namespace]:
            for observations in facts[namespace].get(anchor, {}).get("units", {}).values():
                for observation in observations:
                    if observation.get("start") is None and observation.get("val") is not None:
                        end = parse_date(observation["end"])
                        if latest is None or end > latest:
                            latest = end
        if latest and (best_date is None or latest > best_date):
            best_ns, best_date = namespace, latest
    if best_ns is None:
        return None

    # One unit for the whole report: a filer publishing both its functional
    # currency and a convenience translation covers different tags in each, and
    # mixing them would add New Taiwan dollars to US dollars. The unit that
    # covers the most of the formula wins, with USD breaking ties.
    units: set[str] = set()
    for anchor in ANCHORS[best_ns]:
        units.update(facts[best_ns].get(anchor, {}).get("units", {}).keys())
    scored = []
    for unit in units:
        index = index_instants(facts, best_ns, unit)
        covered = sum(
            1 for name in BSCF_FIELDS
            if resolve_instant(index, best_ns, name, best_date) is not None
        )
        scored.append((covered, unit == "USD", unit))
    if not scored:
        return None
    _, _, unit = max(scored)

    filed = ""
    for anchor in ANCHORS[best_ns]:
        for observation in facts[best_ns].get(anchor, {}).get("units", {}).get(unit, []):
            if observation.get("start") is None and observation["end"] == best_date.isoformat():
                filed = max(filed, observation.get("filed", ""))
    return best_ns, unit, best_date, filed


def sweep_unclassified(
    facts: dict,
    namespace: str,
    unit: str,
    as_of: date,
    patterns: tuple[str, ...],
    counted_values: set[float],
    used_tags: set[str],
    extra_excludes: tuple[str, ...] = (),
) -> list[tuple[str, float]]:
    """Balances in a category that no ladder captured, largest first.

    Two exclusions, because filers hide the same balance two ways. `used_tags`
    drops the exact tags the ladders consumed; `counted_values` drops the
    alternate spellings most filers publish of those same amounts.
    """
    candidates = []
    for tag, entry in facts.get(namespace, {}).items():
        if tag in used_tags:
            continue
        lowered = tag.lower()
        if not any(p in lowered for p in patterns):
            continue
        if any(p in lowered for p in SWEEP_EXCLUDE_PATTERNS + extra_excludes):
            continue
        for observation in entry.get("units", {}).get(unit, []):
            if (
                observation.get("start") is None
                and observation.get("end") == as_of.isoformat()
                and observation.get("val") is not None
            ):
                value = float(observation["val"])
                if value > 0 and value not in counted_values:
                    candidates.append((tag, value))
                break

    seen: set[float] = set()
    unique = []
    for tag, value in sorted(candidates, key=lambda c: -c[1]):
        if value in seen:
            continue
        seen.add(value)
        unique.append((tag, value))
    return unique
