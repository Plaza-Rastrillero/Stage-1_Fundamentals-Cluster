"""BSCF - balance sheet cash formula.

    Net Assets  = cash & equivalents + short-term investments + long-term investments
    Total Debt  = interest-bearing borrowings, current + non-current
    Core Result = Net Assets - Total Debt      ("Net Cash" >= 0, "Net Debt" < 0)

Reads SEC EDGAR's XBRL company facts API. Two modes:

    python bscf.py MSFT AAPL TSM           named tickers, full labeled breakdown
    python bscf.py --date 2026-09-15       whole earnings day, ranked table + CSV

EDGAR 403s requests without a contact address, so set one first:
    $env:SEC_USER_AGENT = "Your Name your.email@example.com"

Folder layout:
    bscf.py        this file - the only one you run
    bscflib/       the code, one file per job:
                     tags.py     the XBRL tag ladders. EDIT HERE when a company
                                 resolves to a wrong or missing figure.
                     formula.py  net assets, total debt, doubt gate, trend
                     resolve.py  tags -> numbers
                     report.py   the printed breakdown, tables and CSV
                     sec_client.py / sec_cache.py / earnings_calendar.py
                                 fetching and caching
    output/        the CSVs a --date run writes
    calendar/      raw Nasdaq calendar rows, archived by every --date run.
                   NOT safe to delete: Nasdaq drops the before-open/after-close
                   field once a date has passed, so these cannot be re-fetched.
    SEC cache/     downloaded SEC data. Safe to delete at any time; the next run
                   re-fetches. Use --refresh to bypass it for one run. If you
                   rename it, update CACHE_DIR in bscflib/sec_cache.py to match.
    archive (storage)/   retired code, imported by nothing
"""

from __future__ import annotations

import sys

# Set before bscflib is imported, so Python does not scatter __pycache__ folders
# through the project. Costs a few milliseconds of recompilation per run and
# keeps the folder readable; delete this line to get the normal caching back.
sys.dont_write_bytecode = True

import argparse  # noqa: E402
import os  # noqa: E402
from datetime import datetime  # noqa: E402

from bscflib import earnings_calendar, formula, report, sec_client  # noqa: E402

MIN_MARKET_CAP = 300_000_000
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
# Gitignored like output/ and SEC cache/, but unlike them NOT regenerable:
# everything else this tool writes can be produced again from the sources,
# these files cannot. Git is not their backup - copy them out of the repo.
CALENDAR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calendar")


def collect(
    tickers: list[str], refresh: bool, verbose: bool
) -> tuple[list[dict], list[tuple[str, str]]]:
    cik_map = sec_client.fetch_ticker_cik_map(refresh)
    results: list[dict] = []
    skipped: list[tuple[str, str]] = []
    seen: dict[int, str] = {}

    for position, ticker in enumerate(tickers, start=1):
        prefix = f"[{position}/{len(tickers)}] {ticker}"
        cik = sec_client.lookup_cik(ticker, cik_map)
        if cik is None:
            skipped.append((ticker, "not a SEC filer in company_tickers.json"))
            continue
        # Share classes (DGICA/DGICB) are one filer with one balance sheet, so a
        # second ticker would put the same company in the ranking twice.
        if cik in seen:
            skipped.append((ticker, f"{formula.OUT_OF_SCOPE}share class of {seen[cik]}"))
            continue
        seen[cik] = ticker
        try:
            result, reason = formula.analyze(ticker, cik, refresh)
        except Exception as exc:  # noqa: BLE001 - one bad ticker shouldn't end the run
            skipped.append((ticker, f"{type(exc).__name__}: {exc}"))
            print(f"{prefix}: skipped - {type(exc).__name__}: {exc}", flush=True)
            continue
        if result is None:
            skipped.append((ticker, reason))
            if verbose:
                print(f"{prefix}: skipped - {reason}", flush=True)
            continue
        results.append(result)
        if verbose:
            print(f"{prefix}: {report.ratio(result['norm'])} as of {result['as_of']}",
                  flush=True)
    return results, skipped


def print_skipped(skipped: list[tuple[str, str]]) -> None:
    out_of_scope = [(t, r) for t, r in skipped if r.startswith(formula.OUT_OF_SCOPE)]
    problems = [(t, r) for t, r in skipped if not r.startswith(formula.OUT_OF_SCOPE)]
    print(f"\n=== Skipped ({len(skipped)}) ===")
    if out_of_scope:
        print(f"\n  Outside coverage by design ({len(out_of_scope)}):")
        for ticker, reason in out_of_scope:
            print(f"    {ticker}: {reason[len(formula.OUT_OF_SCOPE):]}")
    if problems:
        print(f"\n  Data problems worth a look ({len(problems)}):")
        for ticker, reason in problems:
            print(f"    {ticker}: {reason}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("tickers", nargs="*", help="ticker symbols")
    parser.add_argument("--date", help="screen every company reporting on this date "
                                       "(YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, help="process at most N tickers")
    parser.add_argument("--min-cap", type=float, default=MIN_MARKET_CAP,
                        help=f"minimum market cap for --date (default "
                             f"{MIN_MARKET_CAP:,.0f}); 0 disables")
    parser.add_argument("--refresh", action="store_true",
                        help="ignore cached SEC data and re-fetch")
    parser.add_argument("--drop-doubtful", action="store_true",
                        help="exclude rows whose unclassified pool outweighs the signal")
    args = parser.parse_args()

    if not sec_client.SEC_USER_AGENT:
        print(sec_client.USER_AGENT_HELP, file=sys.stderr)
        return 1
    if not args.tickers and not args.date:
        parser.error("give one or more tickers, or --date for a whole earnings day")

    target = None
    if args.date:
        try:
            target = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"Could not parse {args.date!r}; expected YYYY-MM-DD.", file=sys.stderr)
            return 1

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers]
    else:
        print(f"Fetching Nasdaq earnings calendar for {target}...")
        rows = earnings_calendar.fetch(target)
        print(f"  {len(rows)} ticker(s) reporting.")
        # Silent while the format holds. The first run that prints this is the
        # run where Nasdaq changed something under us, which is the only
        # warning this endpoint will ever give.
        unrecognised = [r for r in rows if r.market_cap_raw is not None]
        if unrecognised:
            sample = ", ".join(f'{r.symbol} "{r.market_cap_raw}"'
                               for r in unrecognised[:3])
            print(f"  WARNING: {len(unrecognised)} market cap value(s) in an "
                  f"unrecognised format ({sample}).")
            print("           Treated as unknown and kept - --min-cap did not "
                  "filter them.")
        if args.min_cap > 0:
            # Unknown cap is kept: missing calendar metadata is not evidence of
            # a small company, and the SEC data will speak for itself.
            tickers = [r.symbol for r in rows
                       if r.market_cap is None or r.market_cap >= args.min_cap]
            print(f"  {len(rows) - len(tickers)} below "
                  f"${args.min_cap / report.MILLIONS:,.0f}M market cap, "
                  f"{len(tickers)} remain.")
        else:
            tickers = [r.symbol for r in rows]
        # Runs whatever date is under screen, because what it preserves is a
        # property of now rather than of the target: Nasdaq wipes the
        # before-open/after-close field once a date has passed, so a day not
        # recorded before it happens cannot be recovered.
        try:
            captured = earnings_calendar.capture(CALENDAR_DIR)
        except Exception as exc:  # noqa: BLE001 - the archive is never the point
            print(f"  Calendar archive skipped - {type(exc).__name__}: {exc}")
        else:
            print(f"  Archived {captured.rows} calendar row(s) over "
                  f"{captured.dates} upcoming date(s); {captured.timed} carry a "
                  f"confirmed before-open/after-close slot.")
            if captured.failures:
                print(f"  {captured.failures} date(s) could not be archived.")
    if args.limit:
        tickers = tickers[: args.limit]
    if not tickers:
        print("Nothing to process.")
        return 0

    # Per-ticker progress matters on a 200-name screen and is noise in front of
    # a handful of full breakdowns.
    results, skipped = collect(tickers, args.refresh, verbose=target is not None)

    if args.drop_doubtful:
        kept = [r for r in results if not r["doubtful"]]
        for r in results:
            if r["doubtful"]:
                skipped.append((r["ticker"], f"{formula.DOUBTFUL}doubt {r['doubt']:.1%} "
                                             f"vs norm {report.ratio(r['norm'])}"))
        results = kept

    if not results:
        print("\nNo ticker produced a complete set of figures.")
    elif target is None:
        column = report.column_width(results)
        for result in results:
            print("\n".join(report.render(result, column)))
            print()
        print("\n".join(report.summary(results)))
    else:
        print("\n".join(report.screen(results, target)))
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        path = os.path.join(OUTPUT_DIR, f"bscf_{target.isoformat()}_{stamp}.csv")
        report.write_csv(results, path)
        print(f"\nWrote {path}")

    if skipped:
        print_skipped(skipped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
