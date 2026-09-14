# BSCF — context for a display-design session

Hand this to a fresh chat working on the **display only**. The data layer is
built, verified against live SEC data, and should not need changing.

---

## What the tool is

`BSCF` (balance sheet cash formula) takes a ticker, pulls that company's balance
sheet from SEC EDGAR's XBRL company facts API
(`https://data.sec.gov/api/xbrl/companyfacts/`), and reports how much net cash or
net debt it carries.

This is **Stage 1 (the "fundamentals cluster") of a larger earnings trading
strategy**. The output is an input to a trading decision, not the end product —
so correctness outranks polish, and thresholds are meant to be tuned rather than
fixed.

## The formula

```
Net Assets  = cash & equivalents + short-term investments + long-term investments
Total Debt  = debt (current) + debt (non-current)
Core Result = Net Assets - Total Debt      -> "Net Cash" if >= 0, "Net Debt" if < 0
```

**Debt means interest-bearing borrowings only**: bank debt, notes, bonds,
commercial paper, convertible notes, current portion of long-term debt.
Operating leases, payables, accruals and deferred tax are **not** debt. Other
long-term liabilities and lease liabilities are printed as separate lines,
explicitly marked as excluded from the debt total.

## Running it

```
$env:SEC_USER_AGENT = "Your Name your.email@example.com"   # EDGAR 403s without this

python bscf.py MSFT AAPL TSM        # detail mode: full labeled breakdown per ticker
python bscf.py --date 2026-09-15    # screen mode: whole earnings day, ranked table + CSV
python bscf.py MSFT --html          # detail mode, and a self-contained HTML page
```

Useful flags: `--refresh` (bypass cache), `--limit N`, `--min-cap`,
`--drop-doubtful`.

`--html [PATH]` is a second output target for detail mode, not a replacement:
the run prints the terminal breakdown **and** writes the page, so the two can be
diffed against each other. PATH is optional and defaults into `output/`. Screen
mode stays terminal-only; `--date --html` prints a note and writes nothing.

## Folder layout

```
bscf.py        the only file you run — CLI and mode switching
bscflib/
  report.py       >>> TERMINAL DISPLAY CODE <<<
  html_report.py  >>> HTML DISPLAY CODE <<<
  tags.py      XBRL tag ladders (data only)
  formula.py   net assets, total debt, doubt gate, trend
  resolve.py   tags -> numbers
  sec_client.py / sec_cache.py / earnings_calendar.py
output/        CSVs from --date runs, HTML pages from --html (both gitignored)
SEC cache/     downloaded SEC data, safe to delete (if renamed, update
               CACHE_DIR in bscflib/sec_cache.py to match)
archive (storage)/   retired screeners, imported by nothing — see below
```

The folder names above are the real ones on disk. A rename to `cache/` and
`archive/` has been floated but not carried out; doing it means moving the
folders **and** updating `CACHE_DIR` in `bscflib/sec_cache.py` in the same
change, or the next run silently re-downloads everything into a fresh folder
under the old name.

### What is in `archive (storage)/`

Two retired copies of the original monolithic screener. They are **not**
duplicates of each other and neither is a superset:

| file | has | lacks |
|---|---|---|
| `Stage1-BSCF.py` | the doubt gate that became `formula.py` | coverage ratios |
| `Stage1-BSCF-with-coverage-ratios.py` | `debt_coverage`, `interest_expense`, and the year-to-date differencing quarterizer for TTM windows | the doubt gate |

The coverage machinery in the second file exists nowhere else in the repository
and in no earlier commit. It is the reference implementation if leverage and the
coverage ratios are ever added back (see **Deliberately absent** below), so it is
kept deliberately rather than by accident.

**For display work, edit `bscflib/report.py` or `bscflib/html_report.py` and
nothing else.**

### `report.py` — terminal

Its surface:

| function | role |
|---|---|
| `money(value, unit)` | `$1,234,567`, or `TWD 1,234,567` for non-USD filers |
| `millions(value)` | scaled column for the wide screen table |
| `ratio(value, places=3)` | the normalized result |
| `flags(result)` | builds the `*` `~` `!` suffix on a ticker |
| `render(result, column)` | the per-company breakdown block |
| `column_width(results)` | shared money-column width across a run |
| `_table(headers, rows, align)` | generic fixed-width table builder |
| `summary(results)` | compact table, detail mode |
| `screen(results, target)` | wide table, screen mode |
| `legend(results)` / `trend(...)` | footers |
| `write_csv(results, path)` | full record incl. things the tables omit |

Module constants: `WIDTH = 78`, `MILLIONS`, `STALE_AFTER_DAYS = 180`.

### `html_report.py` — a self-contained page

One `.html` file with the CSS and JS inlined. No build step, no server, no CDN,
no network at open time — it works from `file://`. Vanilla everything.

| function | role |
|---|---|
| `page(results, title=None)` | the whole document, one card per company |
| `card(result)` | one company as a self-contained `<article>` |
| `write(results, path)` | `page()` on disk |
| `suggest_path(results, dir)` | default `output/bscf_<tickers>_<stamp>.html` |
| `compact(value, unit)` | `$72.9B` — the glanceable form, never the only form |
| `band(result)` | which verdict band a reading falls in |
| `_verdict` / `_beam` / `_ledger` | the three parts of a card body |
| `_excluded` / `_sweep` / `_overlap` | the caveat panels |
| `_sparkline(result, accent)` | the trend, as hand-written inline SVG |
| `_tag_chip(namespace, tag)` | one XBRL tag, visible and copy-on-click |

`page()` takes a **list** at every size. One company is a one-card page; with two
or more an index rail appears above the cards. That is the seam a ranked
multi-company view grows from — no rewrite needed.

`money`, `ratio`, `flags`, `LABELS` and `STALE_AFTER_DAYS` are imported from
`report.py` rather than reimplemented, so the terminal and the page cannot drift
apart on what a number looks like.

**The `CONFIG` block at the top of the module owns every threshold and colour**
— `BANDS` (five verdict bands, read on `norm` so they mean the same thing for a
$5bn company and a $3tn one), `DOUBT_NOTICE` / `DOUBT_ALARM` /
`DOUBT_METER_FULL`, `AGE_WARN` / `AGE_ALARM`, `NORM_SCALE`, `TREND_FLAT`, and
`PALETTE`. Nothing emphasised or coloured is set anywhere else; these are meant
to be tuned. The block is also serialized into every page as
`<script type="application/json" id="bscf-config">`, so a saved file explains
the thresholds it was drawn with.

How the five formatting rules survive the medium:

1. the tag is printed under each figure, not hidden behind a hover — a
   screenshot has to stay diagnosable
2. one CSS grid track sized to the widest cell gives the same shared
   right-aligned money column the fixed-width terminal column does
3. an absent figure still prints its reason (`not reported`, `EXCLUDED` with the
   date and amount alongside), never a bare zero
4. a derived figure's bracket note renders as an amber pill, a visibly
   different shape from a grey tag chip, so provenance is never read as
   derivation
5. flags ride the ticker and carry their meaning next to them, with the legend
   kept at the foot as a reminder rather than a lookup

`tags.py` / `resolve.py` / `formula.py` are the correctness core — changing them
changes the numbers, not the presentation.

---

## Formatting rules that must survive any redesign

These came from the original spec and are the reason the tool is trustworthy:

1. **Every number is labeled with the exact XBRL tag it came from.** A wrong
   number must be traceable to a wrong tag without reopening the filing.
2. **Money is right-aligned in a fixed-width column**, `$1,234,567` style, with a
   leading minus for negatives.
3. **Never print a bare `0` where data is absent.** Print the reason instead —
   `not reported`, or
   `EXCLUDED as stale - last reported 2014-12-31 ($1,890,000,000)`.
4. **A fallback or derived figure says so inline in brackets**, e.g.
   `[combined total less current portion]`,
   `[sum of 3 separately reported components]`,
   `[caption bundles finance leases with borrowings]`.
5. **Summary sorted by result descending**, single-character flags on the ticker
   with a legend underneath.

## Flag meanings

| flag | meaning |
|---|---|
| `*` | balance sheet older than 180 days (20-F filers report annually) |
| `~` | a line item was excluded as stale — see that company's breakdown |
| `!` | unclassified balances outweigh the signal; weak seed however extreme the ratio |

---

## Current output — detail mode

A clean company and a messy one, verbatim:

```
==============================================================================
MSFT - MICROSOFT CORPORATION
Balance sheet as of 2026-06-30 (us-gaap, USD, filed 2026-07-29, 75 days old)
------------------------------------------------------------------------------
  Cash and cash equivalents          $20,935,000,000   us-gaap:CashAndCashEquivalentsAtCarryingValue
  Short-term investments             $55,908,000,000   us-gaap:ShortTermInvestments
  Long-term investments              $36,348,000,000   us-gaap:LongTermInvestments
  Debt (current)                      $9,227,000,000   us-gaap:LongTermDebtCurrent
  Debt (non-current)                 $31,067,000,000   us-gaap:LongTermDebtNoncurrent
------------------------------------------------------------------------------
  Net Assets                        $113,191,000,000
  Total Debt                         $40,294,000,000
  NET CASH                           $72,897,000,000
    as a share of total assets                 0.096
------------------------------------------------------------------------------
  Other long-term liabilities        $65,117,000,000   us-gaap:OtherLiabilitiesNoncurrent
    (not included in debt total)
  Lease liabilities                  $16,532,000,000   us-gaap:OperatingLeaseLiabilityNoncurrent
    (not included in debt total)

==============================================================================
T - AT&T INC.
Balance sheet as of 2026-06-30 (us-gaap, USD, filed 2026-07-22, 75 days old)
------------------------------------------------------------------------------
  Cash and cash equivalents          $17,570,000,000   us-gaap:CashAndCashEquivalentsAtCarryingValue
  Short-term investments          EXCLUDED as stale - last reported 2014-12-31 ($1,890,000,000)
  Long-term investments                 not reported
  Debt (current)                      $9,323,000,000   us-gaap:DebtCurrent
  Debt (non-current)                $125,308,000,000   us-gaap:LongTermDebtAndCapitalLeaseObligations  [caption bundles finance leases with borrowings]  [combined total less current portion]
------------------------------------------------------------------------------
  Net Assets                         $17,570,000,000
  Total Debt                        $134,631,000,000
  NET DEBT                         -$117,061,000,000
    as a share of total assets                -0.273
------------------------------------------------------------------------------
  Other long-term liabilities        $24,305,000,000   us-gaap:OtherLiabilitiesNoncurrent
    (not included in debt total)
  Lease liabilities                  $18,934,000,000   us-gaap:OperatingLeaseLiabilityNoncurrent
    (not included in debt total)
------------------------------------------------------------------------------
  Unclassified balances at this date  -  doubt 0.3%
    EquityMethodInvestments           $1,130,000,000   [asset]
    AvailableForSaleSecuritiesDebtSecurities      $579,000,000   [asset]
    (matched on vocabulary, not on an exact tag name, and NOT included
     above. companyfacts carries no roll-up hierarchy, so each is either
     already inside a total above or a genuine miss - only the filing
     settles which)


SUMMARY - sorted by result, most net cash first

TICKER  AS OF       CCY        NET ASSETS        TOTAL DEBT             RESULT
----------------------------------------------------------------------------------------
MSFT    2026-06-30  USD  $113,191,000,000   $40,294,000,000    $72,897,000,000  Net Cash
T~      2026-06-30  USD   $17,570,000,000  $134,631,000,000  -$117,061,000,000  Net Debt
----------------------------------------------------------------------------------------
*  balance sheet older than 180 days
~  a line item was excluded as stale
!  unclassified balances outweigh the signal - weak seed however extreme the ratio looks
```

A foreign filer (TSMC) resolves under `ifrs-full` and produces very long source
lines, because its investments are a sum of three measurement categories:

```
  Short-term investments              $8,977,700,000   ifrs-full:CurrentFinancialAssetsAtFairValueThroughProfitOrLoss  ifrs-full:CurrentFinancialAssetsAtFairValueThroughOtherComprehensiveIncome  ifrs-full:CurrentFinancialAssetsAtAmortisedCost  [sum of 3 separately reported components]
```

## Current output — screen mode

```
=== BSCF - 2026-09-15 (3 tickers) ===

TICKER  AS OF       CCY    NORM  NET ASSETS(M)  TOTAL DEBT(M)  RESULT(M)  DOUBT  TREND(last 4)                GAPS
------------------------------------------------------------------------------------------------------------------
TCOM*   2025-12-31  USD   0.383       19,052.0        4,399.0   14,653.0   0.0%  +0.244 +0.291 +0.349 +0.383  -
GTEN    2026-06-30  USD   0.001            0.2            0.0        0.2   0.0%  +0.001 +0.002 +0.001 +0.001  sti,lti,dcur,dnc
FPS     2026-03-31  USD  -0.265           93.8          584.1     -490.3   0.0%  -0.236 -0.254 -0.281 -0.265  sti,lti
------------------------------------------------------------------------------------------------------------------
```

`norm` (result ÷ total assets) is the ranking column and the only one comparable
across filers reporting in different currencies. `gaps` names formula components
with no matching tag: `sti`/`lti` = short/long-term investments, `dcur`/`dnc` =
current/non-current debt.

---

## Known display rough edges

These all describe the **terminal** renderer. `--html` was built partly to
dissolve them rather than patch them; where it does, that is noted.

1. **Redundant column.** The summary's last column (`Net Cash` / `Net Debt`) is
   unheaded and says the same thing as the sign on `RESULT`.
2. **Source lines run arbitrarily long.** A three-component IFRS sum plus its
   bracket note is ~250 characters and blows past any terminal width. No
   wrapping or truncation strategy exists.
   *Gone in HTML:* each tag is its own chip on a row that spans the grid, so
   TSM's three-tag sums wrap instead of running off the end. Verified on TSM.
3. **Two different rulers.** `render()` uses a fixed `WIDTH = 78` for its rules,
   while `summary()` and `screen()` compute their own width from content, so the
   separators in one run don't line up with each other.
4. **Dangling separator.** A company with no excluded lines and no sweep hits
   ends its block on a trailing `---` rule with nothing under it.
   *Gone in HTML:* sections are elements that either exist or don't, so there is
   no rule to leave hanging. Verified on MSFT and TSM.
5. **Non-USD money columns aren't comparable** across rows. Screen mode dodges
   this by ranking on `norm`; detail mode just prints the currency code.
   *Unchanged in HTML, deliberately:* the currency is printed, the chip is
   highlighted and the card says "figures as filed in TWD - not converted".
   Nothing is converted, because the data does not support the comparison.
6. **The sweep note is four lines of prose** inside an otherwise tabular block.
   *Gone in HTML:* it is a collapsed `<details>` on the caveat panel — present
   and discoverable, not shouting.

## Deliberately absent

**EBITDA and leverage (`Total Debt / EBITDA`) were removed at the user's
request** and may be added back later as a separate piece of work. Operating
cash flow and interest expense went with them, since all three read XBRL
*duration* facts and shared the same machinery (a year-to-date differencing
quarterizer for TTM windows). If they return, the display needs a row for
EBITDA with its period, a leverage row, and a `#` flag for "EBITDA unavailable"
— in both renderers now, and in `html_report.py` a band for leverage would go in
the `CONFIG` block with the rest.

The quarterizer and the `debt_coverage` / `interest_coverage` implementations
survive only in `archive (storage)/Stage1-BSCF-with-coverage-ratios.py`. Read
that before rebuilding any of it from scratch.

## How the user likes to work

- **Discuss the approach and give one recommendation before writing code.** Wait
  for an explicit go-ahead ("build it", "do your recommended changes").
- **Verify against real data before stating a number.** SEC data is cheap to
  query; run the check, then state the figure. Label estimates as estimates.
- **Never run a git command that changes state without being told to, every
  time.** Reading git state is fine. Branching, staging, committing, pushing:
  only on an explicit instruction, and confirm the scope first. An unrequested
  commit rewrites the user's own record of the work.
- **Every commit carries an updated `CONTEXT.md`.** This file is the memory that
  travels with the code, and keeping it current is the assistant's job, at its
  discretion. Before committing, re-read it and fix whatever the change made
  stale — layout, module surface, flags, rough edges, this list. Stage it in the
  same commit, never a follow-up. Correct inaccuracies even where a human wrote
  them, and say so in the report rather than silently.
- **If an instruction rests on a premise that turns out to be false, stop and
  say so** instead of executing it. "Delete it if it's the same file" is not
  authorization to delete when the files differ.
- The user is new to Python but not new to the domain — pitch explanations of
  Python mechanics plainly, and financial/XBRL reasoning at expert level.
