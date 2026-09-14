"""The BSCF formula, and the checks that say whether to believe it.

    Net Assets  = cash & equivalents + short-term investments + long-term investments
    Total Debt  = interest-bearing borrowings, current + non-current
    Core Result = Net Assets - Total Debt      ("Net Cash" >= 0, "Net Debt" < 0)

Debt means borrowings only: bank debt, notes, bonds, commercial paper,
convertible notes and the current portion of long-term debt. Operating leases,
payables, accruals and deferred tax are not debt and are reported separately.
"""

from __future__ import annotations

from datetime import date

from . import resolve, sec_client
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

OUT_OF_SCOPE = "not covered: "
DOUBTFUL = "doubtful data: "


def snapshot(index: dict, namespace: str, as_of: date) -> dict | None:
    """The whole formula at one balance sheet date."""
    lines = {name: resolve.resolve_instant(index, namespace, name, as_of)
             for name in INSTANT_FIELDS}

    cash = lines["cash"]
    if cash is None or not cash.tags:
        return None

    # A caption bundling current maturities into the non-current figure would
    # otherwise overlap the current debt line.
    noncurrent, current = lines["debt_noncurrent"], lines["debt_current"]
    if noncurrent and noncurrent.tags and noncurrent.tags[0] in COMBINED_DEBT_TAGS:
        if current and current.tags:
            noncurrent.value -= current.value
            noncurrent.notes.append("combined total less current portion")
            if noncurrent.value < 0:
                noncurrent.value = 0.0
                noncurrent.notes.append("netted below zero; treated as nil")
        else:
            noncurrent.notes.append("combined total; no separate current portion reported")

    def amount(name: str) -> float:
        line = lines.get(name)
        return line.value if line and line.tags else 0.0

    net_assets = amount("cash") + amount("short_term_investments") + amount("long_term_investments")
    total_debt = amount("debt_current") + amount("debt_noncurrent")
    total_assets = amount("total_assets")

    # A combined caption restates a total rather than a component, so the totals
    # are excluded from the sweep too - the per-component values alone would not
    # match one.
    counted = {line.value for line in lines.values() if line and line.tags}
    counted.update({net_assets, total_debt, net_assets - total_debt})
    used = {tag for line in lines.values() if line for tag in line.tags}

    return {
        "as_of": as_of,
        "lines": lines,
        "net_assets": net_assets,
        "total_debt": total_debt,
        "total_assets": total_assets,
        "core": net_assets - total_debt,
        "norm": (net_assets - total_debt) / total_assets if total_assets else None,
        "resolved": frozenset(n for n in BSCF_FIELDS if lines.get(n) and lines[n].tags),
        "counted": counted,
        "used": used,
    }


def analyze(ticker: str, cik: int, refresh: bool = False) -> tuple[dict | None, str | None]:
    payload = sec_client.fetch_company_facts(cik, refresh)
    if not payload:
        return None, "no XBRL company facts published"
    facts = payload.get("facts", {})

    basis = resolve.choose_basis(facts)
    if basis is None:
        return None, f"{OUT_OF_SCOPE}no balance sheet anchor tag reported"
    namespace, unit, as_of, filed = basis

    index = resolve.index_instants(facts, namespace, unit)
    latest = snapshot(index, namespace, as_of)
    if latest is None:
        return None, f"no cash tag reported at {as_of}"

    history = []
    for when in resolve.anchor_dates(index, namespace)[-TREND_QUARTERS:]:
        past = snapshot(index, namespace, when)
        if past and past["norm"] is not None:
            history.append(past)

    # A company that changes how it tags a component mid-history produces a
    # trend step that is a tagging artifact rather than a balance sheet move.
    window = history[-TREND_DISPLAY:]
    trend_consistent = all(h["resolved"] == latest["resolved"] for h in window)

    gaps = {name for name in BSCF_FIELDS if name not in latest["resolved"]}
    asset_hits = resolve.sweep_unclassified(
        facts, namespace, unit, as_of, SWEEP_ASSET_PATTERNS,
        latest["counted"], latest["used"],
    ) if gaps & ASSET_GAP_FIELDS else []
    liability_hits = resolve.sweep_unclassified(
        facts, namespace, unit, as_of, SWEEP_LIABILITY_PATTERNS,
        latest["counted"], latest["used"], SWEEP_LIABILITY_EXCLUDE,
    ) if gaps & LIABILITY_GAP_FIELDS else []

    # Overlapping disclosure tags make a sum meaningless, so each side's
    # headline is its largest single unclassified balance: a floor on what the
    # ladders missed, and a figure the filer actually reported.
    unspecified_assets = asset_hits[0][1] if asset_hits else 0.0
    unspecified_liabilities = liability_hits[0][1] if liability_hits else 0.0
    total_assets = latest["total_assets"]
    doubt = ((unspecified_assets + unspecified_liabilities) / total_assets
             if total_assets else 0.0)
    signal = abs(latest["norm"]) if latest["norm"] is not None else 0.0
    doubtful = doubt > DOUBT_ABSOLUTE_MAX or doubt > DOUBT_VS_SIGNAL_MAX * signal

    # Debt above its own balance sheet subtotal is proof of double counting.
    overlap = []
    for side, parent_name in (("debt_current", "liabilities_current"),
                              ("debt_noncurrent", "liabilities_noncurrent")):
        parent, child = latest["lines"].get(parent_name), latest["lines"].get(side)
        if parent and parent.tags and child and child.tags and child.value > parent.value:
            overlap.append(side)

    return {
        "ticker": ticker,
        "name": payload.get("entityName", ""),
        "namespace": namespace,
        "unit": unit,
        "filed": filed,
        "age_days": (date.today() - as_of).days,
        **latest,
        "gaps": ",".join(code for name, code in GAP_CODES.items()
                         if name not in latest["resolved"]),
        "trend": [h["norm"] for h in history],
        "trend_consistent": trend_consistent,
        "unspecified_assets": unspecified_assets,
        "unspecified_liabilities": unspecified_liabilities,
        "asset_hits": asset_hits[:SWEEP_DETAIL_TAGS],
        "liability_hits": liability_hits[:SWEEP_DETAIL_TAGS],
        "doubt": doubt,
        "doubtful": doubtful,
        "overlap": overlap,
    }, None
