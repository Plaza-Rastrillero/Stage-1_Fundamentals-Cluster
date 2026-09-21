# BSCF — balance sheet cash formula

Takes a ticker, pulls that company's balance sheet from SEC EDGAR's XBRL
company facts API, and reports how much net cash or net debt it carries.

```
Net Assets  = cash & equivalents + short-term investments + long-term investments
Total Debt  = interest-bearing borrowings, current + non-current
Core Result = Net Assets - Total Debt      -> "Net Cash" if >= 0, "Net Debt" if < 0
```

**Debt means interest-bearing borrowings only**: bank debt, notes, bonds,
commercial paper, convertible notes, current portion of long-term debt.
Operating leases, payables, accruals and deferred tax are *not* debt — they are
printed as separate lines, explicitly marked as excluded from the debt total.

This is **Stage 1 (the fundamentals cluster) of a larger earnings trading
strategy**. The output is an input to a trading decision, not the end product,
so correctness outranks polish and thresholds are meant to be tuned.

---

## Setup

Python 3.10+, no third-party dependencies. EDGAR 403s requests that do not
carry a contact address, so set one before the first run:

```powershell
$env:SEC_USER_AGENT = "Your Name your.email@example.com"
```

## Running it

Two modes:

```powershell
python bscf.py MSFT AAPL TSM        # detail: full labeled breakdown per ticker
python bscf.py --date 2026-09-15    # screen: whole earnings day, ranked table + CSV
```

Either tickers or `--date` is required.

| flag | effect |
|---|---|
| `tickers...` | one or more symbols — detail mode |
| `--date YYYY-MM-DD` | screen every company reporting that day — screen mode |
| `--limit N` | process at most N tickers |
| `--min-cap N` | minimum market cap for `--date`; default `300,000,000`, `0` disables |
| `--refresh` | ignore cached SEC data and re-fetch |
| `--drop-doubtful` | exclude rows whose unclassified pool outweighs the signal |

`--min-cap` **keeps** companies whose market cap is unknown: missing calendar
metadata is not evidence of a small company, and the SEC data will settle it.

### What a run writes to disk

| mode | writes |
|---|---|
| detail | nothing |
| `--date` | one CSV into `output/`, **and** the calendar archive into `calendar/` |
| any SEC fetch | cache files into `SEC cache/` |

The `calendar/` write happens on every `--date` run, whatever date is being
screened — see [Folder layout](#folder-layout).

## Reading the output

Screen mode ranks on `norm` (result ÷ total assets), the only column comparable
across filers reporting in different currencies:

```
=== BSCF - 2026-09-15 (3 tickers) ===

TICKER  AS OF       CCY    NORM  NET ASSETS(M)  TOTAL DEBT(M)  RESULT(M)  DOUBT  TREND(last 4)                GAPS
------------------------------------------------------------------------------------------------------------------
TCOM*   2025-12-31  USD   0.383       19,052.0        4,399.0   14,653.0   0.0%  +0.244 +0.291 +0.349 +0.383  -
GTEN    2026-06-30  USD   0.001            0.2            0.0        0.2   0.0%  +0.001 +0.002 +0.001 +0.001  sti,lti,dcur,dnc
FPS     2026-03-31  USD  -0.265           93.8          584.1     -490.3   0.0%  -0.236 -0.254 -0.281 -0.265  sti,lti
------------------------------------------------------------------------------------------------------------------
```

`gaps` names formula components with no matching tag: `sti`/`lti` =
short/long-term investments, `dcur`/`dnc` = current/non-current debt.

| flag | meaning |
|---|---|
| `*` | balance sheet older than 180 days (20-F filers report annually) |
| `~` | a line item was excluded as stale — see that company's breakdown |
| `!` | unclassified balances outweigh the signal; weak seed however extreme the ratio |

Skipped tickers are printed in two groups: **outside coverage by design**
(not a SEC filer, a second share class of a company already ranked) and
**data problems worth a look**.

## Folder layout

```
bscf.py        the only file you run
bscflib/       the code, one file per job
output/        the CSVs a --date run writes
calendar/      raw Nasdaq calendar rows, archived by every --date run
SEC cache/     downloaded SEC data
archive (storage)/   retired code, imported by nothing
```

| module | job |
|---|---|
| `tags.py` | the XBRL tag ladders, as pure data. **Edit here** when a company resolves to a wrong or missing figure |
| `resolve.py` | tag ladders → numbers; picks one balance-sheet date, taxonomy and currency per company |
| `formula.py` | net assets, total debt, the trend window, the doubt gate, the double-counting check |
| `report.py` | all terminal output: the breakdown, the tables, the legend, the CSV |
| `sec_client.py` / `sec_cache.py` | EDGAR fetching, rate limiting, 24-hour disk cache |
| `earnings_calendar.py` / `calendar_archive.py` | the Nasdaq calendar, and its archive |
| `jsonio.py` | atomic JSON writes, so an interrupted run leaves no half-written file |

**Delete safety.** `SEC cache/` and `output/` are regenerable — delete them
freely, the next run rebuilds what it needs. `calendar/` is **not**: Nasdaq
drops the before-open/after-close field once a date has passed, so a day not
recorded before it happens cannot be recovered. It is gitignored, so git is not
its backup — copy it out of the repo. If you rename `SEC cache/`, update
`CACHE_DIR` in `bscflib/sec_cache.py` to match.

## Design rules

Rules that must survive any redesign. They are the reason the output is
trustworthy:

1. **Every number is labeled with the exact XBRL tag it came from.** A wrong
   number must be traceable to a wrong tag without reopening the filing.
2. **Money is right-aligned in a fixed-width column**, `$1,234,567` style.
3. **Never print a bare `0` where data is absent.** Print the reason —
   `not reported`, or `EXCLUDED as stale - last reported 2014-12-31 (...)`.
4. **A fallback or derived figure says so inline in brackets**, e.g.
   `[combined total less current portion]`.
5. **Summary sorted by result descending**, single-character flags on the
   ticker with a legend underneath.
6. **Nothing is excluded on evidence the tool does not trust.** A value that is
   absent, or present in a shape the code does not recognize, is *unknown* and
   is kept — never silently dropped.

## Further reading

- **`CONTEXT.md`** — the spec: what the tool does, how it is structured, what
  must not change. Written to be read cold at the start of a session.
- **`DESIGN_NOTES.md`** — the user's own working understanding of the code, in
  their own words. Read it, don't rewrite it.

## Caveats

- The Nasdaq earnings calendar is not a public API — the tool presents itself as
  a browser hitting Nasdaq's site. It can break without warning if Nasdaq
  changes anything. The market-cap tripwire refuses to guess at an unfamiliar
  format and warns instead.
- SEC data quality varies by filer. `tags.py` is the tuning surface for it.
