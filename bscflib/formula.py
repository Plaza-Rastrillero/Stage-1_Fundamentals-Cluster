"""The BSCF formula, and the checks that say whether to believe it.

    Net Assets  = cash & equivalents + short-term investments + long-term investments
    Total Debt  = interest-bearing borrowings, current + non-current
    Core Result = Net Assets - Total Debt      ("Net Cash" >= 0, "Net Debt" < 0)

Debt means borrowings only: bank debt, notes, bonds, commercial paper,
convertible notes and the current portion of long-term debt. Operating leases,
payables, accruals and deferred tax are not debt and are reported separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import NamedTuple

from . import resolve, sec_client
from .resolve import Basis, Resolution
from .tags import (
    BSCF_FIELDS,
    COMBINED_DEBT_TAGS,
    GAP_CODES,
    INSTANT_FIELDS,
    SWEEP_ASSET_PATTERNS,
    SWEEP_DETAIL_TAGS,
    SWEEP_LIABILITY_EXCLUDE,
    SWEEP_LIABILITY_PATTERNS,
)

# The sweep answers "a component is missing - how much might be sitting in the
# hole", so it only runs for a side that actually has one. Without this it fires
# on companies whose components all resolved, where any hit is an overlapping
# disclosure-note restatement rather than a miss.
ASSET_GAP_FIELDS = {"short_term_investments", "long_term_investments"}
LIABILITY_GAP_FIELDS = {"debt_current", "debt_noncurrent"}

TREND_QUARTERS = 8  # balance sheet dates of history to compute
TREND_DISPLAY = 4  # how many of those the table shows

# Data-quality gate. `doubt` is the unspecified pool over total assets - roughly
# how far the normalized result could move if the swept balances belong in the
# formula. Judged against the signal rather than as a flat percentage: 5% doubt
# is noise against a -0.30 reading and fatal against a +0.001 one. The absolute
# cap catches a strong-looking ratio resting on a mostly unclassified sheet.
DOUBT_VS_SIGNAL_MAX = 1.0
DOUBT_ABSOLUTE_MAX = 0.25


class Reason(NamedTuple):
    """Why a ticker produced no result.

    `out_of_scope` splits "outside coverage by design" from "a data problem
    worth a look". It used to be a magic prefix on the front of `text`, matched
    with startswith in the printer and then sliced back off - three places that
    had to agree on the same string literal.
    """

    text: str
    out_of_scope: bool = False


Lines = dict[str, "Resolution | None"]
Hits = tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class Snapshot:
    """The whole formula at one balance sheet date."""

    as_of: date
    lines: Lines
    net_assets: float
    total_debt: float
    total_assets: float
    core: float
    norm: float | None
    resolved: frozenset[str]


@dataclass(frozen=True)
class SweepBasis:
    """What the sweep must not count again.

    Not part of the formula and not part of a Result - it is scaffolding the
    sweep needs, and it used to ride into every result dict and on into the
    renderers, which never read it.
    """

    counted: frozenset[float]  # values the ladders produced, any spelling
    used: frozenset[str]  # the exact tags they consumed


@dataclass(frozen=True)
class Result:
    """One company, fully analysed. The record every renderer reads.

    Flat rather than nested because it is also the CSV row: see
    `report.CSV_COLUMNS`, which names each exported field explicitly so a
    rename here fails loudly instead of writing a blank column.
    """

    ticker: str
    name: str
    namespace: str
    unit: str
    filed: str
    age_days: int

    # the snapshot at the newest balance sheet date
    as_of: date
    lines: Lines
    net_assets: float
    total_debt: float
    total_assets: float
    core: float
    norm: float | None
    resolved: frozenset[str]

    # what the checks concluded
    gaps: str
    trend: tuple[float, ...]
    trend_consistent: bool
    unspecified_assets: float
    unspecified_liabilities: float
    asset_hits: Hits
    liability_hits: Hits
    doubt: float
    doubtful: bool
    overlap: tuple[str, ...]


def _net_combined_debt(
    noncurrent: Resolution | None, current: Resolution | None
) -> Resolution | None:
    """Subtract a separately reported current portion from a combined caption.

    A caption bundling current maturities into the non-current figure would
    otherwise describe the same dollars as the current debt line.
    """
    if noncurrent is None or not noncurrent.resolved:
        return noncurrent
    if noncurrent.tags[0] not in COMBINED_DEBT_TAGS:
        return noncurrent
    if current is not None and current.resolved:
        return noncurrent.less(current.value, "combined total less current portion")
    return noncurrent.with_note("combined total; no separate current portion reported")


def snapshot(
    index: resolve.Index, namespace: str, as_of: date
) -> tuple[Snapshot, SweepBasis] | None:
    """The whole formula at one balance sheet date, plus the sweep's scaffolding."""
    lines: Lines = {name: resolve.resolve_instant(index, namespace, name, as_of)
                    for name in INSTANT_FIELDS}

    cash = lines["cash"]
    if cash is None or not cash.resolved:
        return None

    lines["debt_noncurrent"] = _net_combined_debt(
        lines["debt_noncurrent"], lines["debt_current"]
    )

    def amount(name: str) -> float:
        line = lines.get(name)
        return line.value if line and line.resolved else 0.0

    net_assets = (amount("cash") + amount("short_term_investments")
                  + amount("long_term_investments"))
    total_debt = amount("debt_current") + amount("debt_noncurrent")
    total_assets = amount("total_assets")
    core = net_assets - total_debt

    # A combined caption restates a total rather than a component, so the totals
    # are excluded from the sweep too - the per-component values alone would not
    # match one.
    counted = {line.value for line in lines.values() if line and line.resolved}
    counted.update({net_assets, total_debt, core})

    return (
        Snapshot(
            as_of=as_of,
            lines=lines,
            net_assets=net_assets,
            total_debt=total_debt,
            total_assets=total_assets,
            core=core,
            norm=core / total_assets if total_assets else None,
            resolved=frozenset(
                name for name in BSCF_FIELDS
                if lines.get(name) and lines[name].resolved
            ),
        ),
        SweepBasis(
            counted=frozenset(counted),
            used=frozenset(tag for line in lines.values() if line for tag in line.tags),
        ),
    )


def _overlaps(lines: Lines) -> tuple[str, ...]:
    """Debt lines exceeding their own balance sheet subtotal - double counting."""
    found = []
    for side, parent_name in (("debt_current", "liabilities_current"),
                              ("debt_noncurrent", "liabilities_noncurrent")):
        parent, child = lines.get(parent_name), lines.get(side)
        if (parent and parent.resolved and child and child.resolved
                and child.value > parent.value):
            found.append(side)
    return tuple(found)


def _sweep(facts: dict, basis: Basis, scaffold: SweepBasis, gaps: set[str]) -> tuple[Hits, Hits]:
    """(asset hits, liability hits), each empty unless that side has a gap."""
    sides = []
    for patterns, gate, excludes in (
        (SWEEP_ASSET_PATTERNS, ASSET_GAP_FIELDS, ()),
        (SWEEP_LIABILITY_PATTERNS, LIABILITY_GAP_FIELDS, SWEEP_LIABILITY_EXCLUDE),
    ):
        sides.append(tuple(
            resolve.sweep_unclassified(
                facts, basis.namespace, basis.unit, basis.as_of, patterns,
                scaffold.counted, scaffold.used, excludes,
            ) if gaps & gate else ()
        ))
    return sides[0], sides[1]


def analyze(ticker: str, cik: int, refresh: bool = False) -> tuple[Result | None, Reason | None]:
    payload = sec_client.fetch_company_facts(cik, refresh)
    if not payload:
        return None, Reason("no XBRL company facts published")
    facts = payload.get("facts", {})

    basis = resolve.choose_basis(facts)
    if basis is None:
        return None, Reason("no balance sheet anchor tag reported", out_of_scope=True)

    taken = snapshot(basis.index, basis.namespace, basis.as_of)
    if taken is None:
        return None, Reason(f"no cash tag reported at {basis.as_of}")
    latest, scaffold = taken

    history = []
    for when in resolve.anchor_dates(basis.index, basis.namespace)[-TREND_QUARTERS:]:
        past = snapshot(basis.index, basis.namespace, when)
        if past and past[0].norm is not None:
            history.append(past[0])

    # A company that changes how it tags a component mid-history produces a
    # trend step that is a tagging artifact rather than a balance sheet move.
    window = history[-TREND_DISPLAY:]
    trend_consistent = all(h.resolved == latest.resolved for h in window)

    gaps = {name for name in BSCF_FIELDS if name not in latest.resolved}
    asset_hits, liability_hits = _sweep(facts, basis, scaffold, gaps)

    # Overlapping disclosure tags make a sum meaningless, so each side's
    # headline is its largest single unclassified balance: a floor on what the
    # ladders missed, and a figure the filer actually reported.
    unspecified_assets = asset_hits[0][1] if asset_hits else 0.0
    unspecified_liabilities = liability_hits[0][1] if liability_hits else 0.0
    doubt = ((unspecified_assets + unspecified_liabilities) / latest.total_assets
             if latest.total_assets else 0.0)
    signal = abs(latest.norm) if latest.norm is not None else 0.0

    return Result(
        ticker=ticker,
        name=payload.get("entityName", ""),
        namespace=basis.namespace,
        unit=basis.unit,
        filed=basis.filed,
        age_days=(date.today() - latest.as_of).days,
        as_of=latest.as_of,
        lines=latest.lines,
        net_assets=latest.net_assets,
        total_debt=latest.total_debt,
        total_assets=latest.total_assets,
        core=latest.core,
        norm=latest.norm,
        resolved=latest.resolved,
        gaps=",".join(code for name, code in GAP_CODES.items()
                      if name not in latest.resolved),
        trend=tuple(h.norm for h in history),
        trend_consistent=trend_consistent,
        unspecified_assets=unspecified_assets,
        unspecified_liabilities=unspecified_liabilities,
        asset_hits=asset_hits[:SWEEP_DETAIL_TAGS],
        liability_hits=liability_hits[:SWEEP_DETAIL_TAGS],
        doubt=doubt,
        doubtful=doubt > DOUBT_ABSOLUTE_MAX or doubt > DOUBT_VS_SIGNAL_MAX * signal,
        overlap=_overlaps(latest.lines),
    ), None
