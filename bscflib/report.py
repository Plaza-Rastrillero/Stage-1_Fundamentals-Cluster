"""Printing. Every figure carries the tag it came from, so a wrong number is
traceable to a wrong tag without reopening the filing."""

from __future__ import annotations

import csv
from dataclasses import fields
from datetime import date
from typing import Callable, NamedTuple

from .earnings_calendar import Row as CalendarRow
from .formula import TREND_DISPLAY, Result
from .tags import BSCF_FIELDS

WIDTH = 78
MILLIONS = 1_000_000

# A balance sheet this far behind today is flagged: 20-F filers publish
# annually, so their newest sheet can be over a year old and is not comparable
# to a 10-Q.
STALE_AFTER_DAYS = 180

LABELS = {
    "cash": "Cash and cash equivalents",
    "short_term_investments": "Short-term investments",
    "long_term_investments": "Long-term investments",
    "debt_current": "Debt (current)",
    "debt_noncurrent": "Debt (non-current)",
}

EXCLUDED_LINES = (
    ("other_lt_liabilities", "Other long-term liabilities"),
    ("lease_liabilities", "Lease liabilities"),
)


def money(value: float, unit: str) -> str:
    prefix = "$" if unit == "USD" else f"{unit} "
    return f"{'-' if value < 0 else ''}{prefix}{abs(value):,.0f}"


def millions(value: float | None) -> str:
    return "n/a" if value is None else f"{value / MILLIONS:,.1f}"


def ratio(value: float | None, places: int = 3) -> str:
    return "n/a" if value is None else f"{value:,.{places}f}"


def calendar_warning(rows: list[CalendarRow]) -> list[str]:
    """The unrecognised-market-cap tripwire, as lines to print.

    Silent while the format holds. The first run that prints this is the run
    where Nasdaq changed something under us, which for an undocumented endpoint
    is the only warning there will ever be.
    """
    unrecognised = [r for r in rows if r.market_cap_raw is not None]
    if not unrecognised:
        return []
    sample = ", ".join(f'{r.symbol} "{r.market_cap_raw}"' for r in unrecognised[:3])
    return [
        f"  WARNING: {len(unrecognised)} market cap value(s) in an "
        f"unrecognised format ({sample}).",
        "           Treated as unknown and kept - --min-cap did not filter them.",
    ]


def flags(result: Result) -> str:
    out = ""
    if result.age_days > STALE_AFTER_DAYS:
        out += "*"
    if any(line is not None and not line.resolved and line.stale
           for name, line in result.lines.items() if name in BSCF_FIELDS):
        out += "~"
    if result.doubtful:
        out += "!"
    return out


# --------------------------------------------------------------------------
# Per-company breakdown
#
# One function per section, each returning its own lines or nothing at all.
# A section that has nothing to say emits no rule either, so the block cannot
# end on a separator with nothing under it.
# --------------------------------------------------------------------------

Row = Callable[..., str]


def _header(result: Result, row: Row) -> list[str]:
    filed = f", filed {result.filed}" if result.filed else ""
    return [
        "=" * WIDTH,
        f"{result.ticker} - {result.name.upper()}",
        f"Balance sheet as of {result.as_of} ({result.namespace}, {result.unit}"
        f"{filed}, {result.age_days} days old)",
        "-" * WIDTH,
    ]


def _ledger(result: Result, row: Row) -> list[str]:
    """The five formula components, each with its tag or its reason for absence."""
    out = []
    for name in BSCF_FIELDS:
        line = result.lines.get(name)
        if line is None:
            out.append(row(LABELS[name], "not reported"))
        elif not line.resolved:
            when, was = line.stale
            out.append(row(LABELS[name], f"EXCLUDED as stale - last reported "
                                         f"{when} ({money(was, result.unit)})"))
        else:
            source = "  ".join(f"{result.namespace}:{tag}" for tag in line.tags)
            source += "".join(f"  [{note}]" for note in line.notes)
            out.append(row(LABELS[name], money(line.value, result.unit), source))
    return out


def _totals(result: Result, row: Row) -> list[str]:
    out = [
        "-" * WIDTH,
        row("Net Assets", money(result.net_assets, result.unit)),
        row("Total Debt", money(result.total_debt, result.unit)),
        row("NET CASH" if result.core >= 0 else "NET DEBT",
            money(result.core, result.unit)),
    ]
    if result.norm is not None:
        out.append(row("  as a share of total assets", ratio(result.norm)))
    return out


def _excluded(result: Result, row: Row) -> list[str]:
    """Liabilities reported for context and deliberately kept out of the debt total."""
    present = [(name, label) for name, label in EXCLUDED_LINES
               if result.lines.get(name) and result.lines[name].resolved]
    if not present:
        return []
    out = ["-" * WIDTH]
    for name, label in present:
        line = result.lines[name]
        source = "  ".join(f"{result.namespace}:{tag}" for tag in line.tags)
        out.append(row(label, money(line.value, result.unit), source))
        out.append("    (not included in debt total)")
    return out


def _sweep(result: Result, row: Row) -> list[str]:
    """Balances in the formula's categories that no ladder claimed."""
    if not result.asset_hits and not result.liability_hits:
        return []
    out = ["-" * WIDTH,
           f"  Unclassified balances at this date  -  doubt {result.doubt:.1%}"
           + ("  !! outweighs the signal" if result.doubtful else "")]
    for side, hits in (("asset", result.asset_hits),
                       ("liability", result.liability_hits)):
        for tag, value in hits:
            out.append(row(f"  {tag}", money(value, result.unit), f"[{side}]"))
    out.append("    (matched on vocabulary, not on an exact tag name, and NOT included")
    out.append("     above. companyfacts carries no roll-up hierarchy, so each is either")
    out.append("     already inside a total above or a genuine miss - only the filing")
    out.append("     settles which)")
    return out


def _overlap(result: Result, row: Row) -> list[str]:
    if not result.overlap:
        return []
    return [f"  !! {', '.join(result.overlap)} exceeds its own balance sheet "
            "subtotal - double counting"]


SECTIONS = (_header, _ledger, _totals, _excluded, _sweep, _overlap)


def render(result: Result, column: int) -> list[str]:
    """The per-company breakdown block."""
    def row(label: str, value: str, source: str = "") -> str:
        return f"  {label:<32}{value:>{column}}   {source}".rstrip()

    out: list[str] = []
    for section in SECTIONS:
        out.extend(section(result, row))
    return out


def column_width(results: list[Result]) -> int:
    """One money-column width shared by every breakdown in a run."""
    widest = max(
        len(money(value, r.unit))
        for r in results
        for value in [r.net_assets, r.total_debt, r.core]
        + [line.value for line in r.lines.values() if line and line.resolved]
    )
    return max(widest, 18)


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------


class Column(NamedTuple):
    """One table column: header, alignment and how to read it off a Result.

    Keeping the three together is the point. They used to be a header list and
    a positional alignment string ("<<<>>>>>") that had to be counted against
    each other by hand.
    """

    header: str
    align: str
    value: Callable[[Result], str]


def _table(columns: tuple[Column, ...], results: list[Result]) -> list[str]:
    headers = [c.header for c in columns]
    rows = [[c.value(r) for c in columns] for r in results]
    widths = [max([len(c.header)] + [len(row[i]) for row in rows])
              for i, c in enumerate(columns)]

    def line(cells: list[str]) -> str:
        return "  ".join(f"{cell:{columns[i].align}{widths[i]}}"
                         for i, cell in enumerate(cells)).rstrip()

    body = [line(headers)] + [line(r) for r in rows]
    rule = "-" * max(len(entry) for entry in body)
    return [body[0], rule] + body[1:] + [rule]


SUMMARY_COLUMNS = (
    Column("TICKER", "<", lambda r: f"{r.ticker}{flags(r)}"),
    Column("AS OF", "<", lambda r: r.as_of.isoformat()),
    Column("CCY", "<", lambda r: r.unit),
    Column("NET ASSETS", ">", lambda r: money(r.net_assets, r.unit)),
    Column("TOTAL DEBT", ">", lambda r: money(r.total_debt, r.unit)),
    Column("RESULT", ">", lambda r: money(r.core, r.unit)),
    Column("", ">", lambda r: "Net Cash" if r.core >= 0 else "Net Debt"),
)

SCREEN_COLUMNS = (
    Column("TICKER", "<", lambda r: f"{r.ticker}{flags(r)}"),
    Column("AS OF", "<", lambda r: r.as_of.isoformat()),
    Column("CCY", "<", lambda r: r.unit),
    Column("NORM", ">", lambda r: ratio(r.norm)),
    Column("NET ASSETS(M)", ">", lambda r: millions(r.net_assets)),
    Column("TOTAL DEBT(M)", ">", lambda r: millions(r.total_debt)),
    Column("RESULT(M)", ">", lambda r: millions(r.core)),
    Column("DOUBT", ">", lambda r: f"{r.doubt:.1%}"),
    Column(f"TREND(last {TREND_DISPLAY})", "<",
           lambda r: trend(r.trend, r.trend_consistent)),
    Column("GAPS", "<", lambda r: r.gaps or "-"),
)


def summary(results: list[Result]) -> list[str]:
    """Compact table for a handful of named tickers."""
    ranked = sorted(results, key=lambda r: -r.core)
    out = ["", "SUMMARY - sorted by result, most net cash first", ""]
    out.extend(_table(SUMMARY_COLUMNS, ranked))
    out.extend(legend(results))
    return out


def screen(results: list[Result], target: date) -> list[str]:
    """Wide table for a whole earnings day.

    Ranked on the normalized result rather than the dollar one: the pool spans
    filers reporting in different currencies, and a ratio is the only column
    that compares across them. A row with no norm ranks last, said as a sort
    key rather than as a sentinel value standing in for one.
    """
    ranked = sorted(results, key=lambda r: (r.norm is None, -(r.norm or 0.0)))
    out = ["", f"=== BSCF - {target} ({len(results)} tickers) ===", ""]
    out.extend(_table(SCREEN_COLUMNS, ranked))
    out.extend(legend(results))
    out.append(
        "\nnorm: result as a share of total assets - the ranking column, and the only"
        "\n  one comparable across filers reporting in different currencies."
        "\ndoubt: largest unclassified balance on each side, over total assets - roughly"
        "\n  how far norm could move if those balances belong in the formula. NOT included"
        "\n  in the figures. Source tags are in the CSV."
        "\ngaps: formula components with no matching tag, counted as zero"
        "\n  (sti/lti = short/long-term investments, dcur/dnc = current/non-current debt)."
    )
    return out


def legend(results: list[Result]) -> list[str]:
    out = [f"*  balance sheet older than {STALE_AFTER_DAYS} days",
           "~  a line item was excluded as stale",
           "!  unclassified balances outweigh the signal - weak seed however extreme "
           "the ratio looks"]
    if len({r.unit for r in results}) > 1:
        out.append("   dollar columns are in each filer's own currency; rank on NORM instead")
    return out


def trend(values: tuple[float, ...], consistent: bool) -> str:
    body = " ".join(f"{v:+.3f}" for v in values[-TREND_DISPLAY:]) or "n/a"
    return body if consistent else body + " *"


# --------------------------------------------------------------------------
# CSV
#
# The schema is declared, not derived from Result's field order: Stage 2 reads
# this file, so an unrelated edit to Result must not silently reorder or add a
# column. What the declaration does buy is that every value is read by name off
# the dataclass, so a renamed field raises instead of writing a blank cell.
# --------------------------------------------------------------------------

# Written straight off the Result.
CSV_PLAIN = ("ticker", "name", "namespace", "unit", "filed", "age_days",
             "net_assets", "total_debt", "core", "norm", "total_assets", "gaps",
             "doubt", "doubtful", "unspecified_assets", "unspecified_liabilities",
             "trend_consistent")

# Columns that are a rendering of a field rather than the field itself.
CSV_DERIVED: dict[str, Callable[[Result], object]] = {
    "as_of": lambda r: r.as_of.isoformat(),
    "asset_tags": lambda r: " | ".join(f"{t}:{millions(v)}M" for t, v in r.asset_hits),
    "liability_tags": lambda r: " | ".join(f"{t}:{millions(v)}M"
                                           for t, v in r.liability_hits),
    "trend": lambda r: " ".join(f"{v:+.4f}" for v in r.trend),
    "overlap": lambda r: ",".join(r.overlap),
}

# The order columns are written in.
CSV_COLUMNS = ("ticker", "name", "as_of", "namespace", "unit", "filed", "age_days",
               "net_assets", "total_debt", "core", "norm", "total_assets", "gaps",
               "doubt", "doubtful", "unspecified_assets", "unspecified_liabilities",
               "asset_tags", "liability_tags", "trend", "trend_consistent", "overlap")


def _check_csv_schema() -> None:
    """Fail at import if the CSV spec and Result have drifted apart."""
    missing = set(CSV_PLAIN) - {f.name for f in fields(Result)}
    if missing:
        raise RuntimeError(f"CSV names fields Result does not have: {sorted(missing)}")
    if set(CSV_COLUMNS) != set(CSV_PLAIN) | set(CSV_DERIVED):
        raise RuntimeError("CSV_COLUMNS does not match CSV_PLAIN + CSV_DERIVED")


_check_csv_schema()


def write_csv(results: list[Result], path: str) -> None:
    """Everything the tables leave out, including the full trend and sweep tags."""
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for r in results:
            writer.writerow({
                name: CSV_DERIVED[name](r) if name in CSV_DERIVED else getattr(r, name)
                for name in CSV_COLUMNS
            })
