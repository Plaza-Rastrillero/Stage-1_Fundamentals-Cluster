"""Printing. Every figure carries the tag it came from, so a wrong number is
traceable to a wrong tag without reopening the filing."""

from __future__ import annotations

import csv
from datetime import date

from .formula import TREND_DISPLAY
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


def money(value: float, unit: str) -> str:
    prefix = "$" if unit == "USD" else f"{unit} "
    return f"{'-' if value < 0 else ''}{prefix}{abs(value):,.0f}"


def millions(value: float | None) -> str:
    return "n/a" if value is None else f"{value / MILLIONS:,.1f}"


def ratio(value: float | None, places: int = 3) -> str:
    return "n/a" if value is None else f"{value:,.{places}f}"


def flags(result: dict) -> str:
    out = ""
    if result["age_days"] > STALE_AFTER_DAYS:
        out += "*"
    if any(line is not None and not line.tags and line.stale
           for name, line in result["lines"].items() if name in BSCF_FIELDS):
        out += "~"
    if result["doubtful"]:
        out += "!"
    return out


# --------------------------------------------------------------------------
# Per-company breakdown
# --------------------------------------------------------------------------


def render(result: dict, column: int) -> list[str]:
    unit, lines, namespace = result["unit"], result["lines"], result["namespace"]
    out = ["=" * WIDTH, f"{result['ticker']} - {result['name'].upper()}"]
    filed = f", filed {result['filed']}" if result["filed"] else ""
    out.append(
        f"Balance sheet as of {result['as_of']} ({namespace}, {unit}{filed}, "
        f"{result['age_days']} days old)"
    )
    out.append("-" * WIDTH)

    def row(label: str, value: str, source: str = "") -> str:
        return f"  {label:<32}{value:>{column}}   {source}".rstrip()

    for name in BSCF_FIELDS:
        line = lines.get(name)
        if line is None:
            out.append(row(LABELS[name], "not reported"))
        elif not line.tags:
            when, was = line.stale
            out.append(row(LABELS[name],
                           f"EXCLUDED as stale - last reported {when} ({money(was, unit)})"))
        else:
            source = "  ".join(f"{namespace}:{tag}" for tag in line.tags)
            source += "".join(f"  [{note}]" for note in line.notes)
            out.append(row(LABELS[name], money(line.value, unit), source))

    out.append("-" * WIDTH)
    out.append(row("Net Assets", money(result["net_assets"], unit)))
    out.append(row("Total Debt", money(result["total_debt"], unit)))
    out.append(row("NET CASH" if result["core"] >= 0 else "NET DEBT",
                   money(result["core"], unit)))
    if result["norm"] is not None:
        out.append(row("  as a share of total assets", ratio(result["norm"])))

    excluded = [(n, l) for n, l in (("other_lt_liabilities", "Other long-term liabilities"),
                                    ("lease_liabilities", "Lease liabilities"))
                if lines.get(n) and lines[n].tags]
    if excluded:
        out.append("-" * WIDTH)
        for name, label in excluded:
            line = lines[name]
            source = "  ".join(f"{namespace}:{tag}" for tag in line.tags)
            out.append(row(label, money(line.value, unit), source))
            out.append("    (not included in debt total)")

    if result["asset_hits"] or result["liability_hits"]:
        out.append("-" * WIDTH)
        out.append(f"  Unclassified balances at this date  -  doubt {result['doubt']:.1%}"
                   + ("  !! outweighs the signal" if result["doubtful"] else ""))
        for side, hits in (("asset", result["asset_hits"]),
                           ("liability", result["liability_hits"])):
            for tag, value in hits:
                out.append(row(f"  {tag}", money(value, unit), f"[{side}]"))
        out.append("    (matched on vocabulary, not on an exact tag name, and NOT included")
        out.append("     above. companyfacts carries no roll-up hierarchy, so each is either")
        out.append("     already inside a total above or a genuine miss - only the filing")
        out.append("     settles which)")

    if result["overlap"]:
        out.append(f"  !! {', '.join(result['overlap'])} exceeds its own balance sheet "
                   "subtotal - double counting")
    return out


def column_width(results: list[dict]) -> int:
    widest = max(
        len(money(value, r["unit"]))
        for r in results
        for value in [r["net_assets"], r["total_debt"], r["core"]]
        + [line.value for line in r["lines"].values() if line and line.tags]
    )
    return max(widest, 18)


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------


def _table(headers: list[str], rows: list[list[str]], align: str) -> list[str]:
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]

    def line(cells: list[str]) -> str:
        return "  ".join(f"{c:{align[i]}{widths[i]}}" for i, c in enumerate(cells)).rstrip()

    body = [line(headers)] + [line(r) for r in rows]
    rule = "-" * max(len(row) for row in body)
    return [body[0], rule] + body[1:] + [rule]


def summary(results: list[dict]) -> list[str]:
    """Compact table for a handful of named tickers."""
    rows = [
        [
            f"{r['ticker']}{flags(r)}",
            r["as_of"].isoformat(),
            r["unit"],
            money(r["net_assets"], r["unit"]),
            money(r["total_debt"], r["unit"]),
            money(r["core"], r["unit"]),
            "Net Cash" if r["core"] >= 0 else "Net Debt",
        ]
        for r in sorted(results, key=lambda r: -r["core"])
    ]
    headers = ["TICKER", "AS OF", "CCY", "NET ASSETS", "TOTAL DEBT", "RESULT", ""]
    out = ["", "SUMMARY - sorted by result, most net cash first", ""]
    out.extend(_table(headers, rows, "<<<>>>>"))
    out.extend(legend(results))
    return out


def screen(results: list[dict], target: date) -> list[str]:
    """Wide table for a whole earnings day.

    Ranked on the normalized result rather than the dollar one: the pool spans
    filers reporting in different currencies, and a ratio is the only column
    that compares across them.
    """
    rows = [
        [
            f"{r['ticker']}{flags(r)}",
            r["as_of"].isoformat(),
            r["unit"],
            ratio(r["norm"]),
            millions(r["net_assets"]),
            millions(r["total_debt"]),
            millions(r["core"]),
            f"{r['doubt']:.1%}",
            trend(r["trend"], r["trend_consistent"]),
            r["gaps"] or "-",
        ]
        for r in sorted(results, key=lambda r: -(r["norm"] if r["norm"] is not None else -9e9))
    ]
    headers = ["TICKER", "AS OF", "CCY", "NORM", "NET ASSETS(M)", "TOTAL DEBT(M)",
               "RESULT(M)", "DOUBT", f"TREND(last {TREND_DISPLAY})", "GAPS"]
    out = ["", f"=== BSCF - {target} ({len(results)} tickers) ===", ""]
    out.extend(_table(headers, rows, "<<<>>>>><<"))
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


def legend(results: list[dict]) -> list[str]:
    out = [f"*  balance sheet older than {STALE_AFTER_DAYS} days",
           "~  a line item was excluded as stale",
           "!  unclassified balances outweigh the signal - weak seed however extreme "
           "the ratio looks"]
    if len({r["unit"] for r in results}) > 1:
        out.append("   dollar columns are in each filer's own currency; rank on NORM instead")
    return out


def trend(values: list[float], consistent: bool) -> str:
    body = " ".join(f"{v:+.3f}" for v in values[-TREND_DISPLAY:]) or "n/a"
    return body if consistent else body + " *"


def write_csv(results: list[dict], path: str) -> None:
    """Everything the tables leave out, including the full trend and sweep tags."""
    columns = ["ticker", "name", "as_of", "namespace", "unit", "filed", "age_days",
               "net_assets", "total_debt", "core", "norm", "total_assets", "gaps",
               "doubt", "doubtful", "unspecified_assets", "unspecified_liabilities",
               "asset_tags", "liability_tags", "trend", "trend_consistent", "overlap"]
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for r in results:
            writer.writerow({
                **{c: r.get(c) for c in columns},
                "as_of": r["as_of"].isoformat(),
                "asset_tags": " | ".join(f"{t}:{millions(v)}M" for t, v in r["asset_hits"]),
                "liability_tags": " | ".join(f"{t}:{millions(v)}M" for t, v in r["liability_hits"]),
                "trend": " ".join(f"{v:+.4f}" for v in r["trend"]),
                "overlap": ",".join(r["overlap"]),
            })
