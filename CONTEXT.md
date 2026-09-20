# BSCF — project context

The spec and current state of this tool, written to be read cold at the start of
a session, whatever the task. It records what the code does now, what is known
about the data it reads, what is deliberately missing, and what is still rough.

Read `DESIGN_NOTES.md` alongside this file. It is the user's own notes on the
code — written by hand, filled in over time — and it says what the user has
already worked out, so that does not need re-explaining. **This file is the
spec; that one is the understanding.** Neither replaces the other, and this one
never edits that one.

---

## Maintaining this file

This file is read at the start of essentially every session and updated
constantly. It is organized as nine fixed sections so that new material has an
obvious home and nothing has to be reshuffled to add to it.

**Where a change goes:**

| you changed | it goes in |
|---|---|
| a function's name, signature or role | §4, that module's surface table |
| a CLI flag | §2.3 |
| a threshold or tunable constant | §4 (that module); also §1.3 if it is an invariant |
| something learned about SEC or Nasdaq data | §5, as a dated finding |
| a display problem you are not fixing now | §7 |
| a feature removed or postponed | §8 |
| a new file or folder | §3.1 tree, plus a §4 subsection |
| what the printed output looks like | §6 |

**Conventions that keep it from drifting:**

1. **The nine sections are fixed.** Add a tenth only for a genuinely new *kind*
   of knowledge, not for a new topic inside an existing kind. Almost everything
   is an entry within a section.
2. **§4 uses one template per module** — *Role*, *Surface*, *Constants*,
   *Worth knowing*. A new module is a new subsection in the same shape, so the
   section scales without restyling.
3. **Every empirical claim in §5 carries the date it was checked**
   (`**Verified Sep 2026:**`). These are facts about someone else's service and
   they expire. An undated claim in §5 is one nobody has verified.
4. **§7 entries are titled and status-stamped, never numbered.** Resolving one
   is an edit to its `*Status:*` line, not a renumber of the list.
5. **§4 says what the code does; §5 says what the data does.** When a design
   decision was forced by a data quirk, the decision goes in §4 and the
   evidence in §5, each pointing at the other.

---

## 1. The tool

### 1.1 What it is

`BSCF` (balance sheet cash formula) takes a ticker, pulls that company's balance
sheet from SEC EDGAR's XBRL company facts API
(`https://data.sec.gov/api/xbrl/companyfacts/`), and reports how much net cash or
net debt it carries.

This is **Stage 1 (the "fundamentals cluster") of a larger earnings trading
strategy**. The output is an input to a trading decision, not the end product —
so correctness outranks polish, and thresholds are meant to be tuned rather than
fixed.

### 1.2 The formula

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

### 1.3 Invariants

Rules that must survive any redesign. These came from the original spec and are
the reason the tool is trustworthy.

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
6. **Nothing is excluded on evidence the tool does not trust.** A value that is
   absent, or present in a shape the code does not recognize, is *unknown* and
   is kept for the SEC data to settle — never silently dropped. See §4.5 and
   §5.3 for where this rule is enforced and why.

---

## 2. Running it

### 2.1 Setup

```
$env:SEC_USER_AGENT = "Your Name your.email@example.com"   # EDGAR 403s without this
```

### 2.2 The two modes

```
python bscf.py MSFT AAPL TSM        # detail mode: full labeled breakdown per ticker
python bscf.py --date 2026-09-15    # screen mode: whole earnings day, ranked table + CSV
python bscf.py MSFT --html          # detail mode, and a self-contained HTML page
```

`--html [PATH]` is a second output target for detail mode, not a replacement:
the run prints the terminal breakdown **and** writes the page, so the two can be
diffed against each other. PATH is optional and defaults into `output/`. Screen
mode stays terminal-only; `--date --html` prints a note and writes nothing.

### 2.3 Flags

| flag | effect |
|---|---|
| `tickers...` | one or more symbols — detail mode |
| `--date YYYY-MM-DD` | screen every company reporting that day — screen mode |
| `--limit N` | process at most N tickers |
| `--min-cap N` | minimum market cap for `--date`; default `300_000_000`, `0` disables |
| `--refresh` | ignore cached SEC data and re-fetch |
| `--drop-doubtful` | exclude rows whose unclassified pool outweighs the signal |
| `--html [PATH]` | also write a self-contained HTML page of the detail breakdown |

Either tickers or `--date` is required. `--min-cap` keeps companies whose cap is
*unknown* — see §1.3 rule 6.

### 2.4 What a run writes to disk

| mode | writes |
|---|---|
| detail | nothing, unless `--html` |
| detail `--html` | one HTML page into `output/` (or PATH) |
| `--date` | one CSV into `output/`, **and** the calendar archive into `calendar/` |
| any SEC fetch | cache files into `SEC cache/` |

The `calendar/` write happens on every `--date` run regardless of which date is
being screened. See §5.4.

---

## 3. Repository layout

### 3.1 The tree

```
bscf.py        the only file you run — CLI and mode switching
CONTEXT.md     this file — the spec a fresh session reads
DESIGN_NOTES.md  the user's own notes on the code — read, don't rewrite
bscflib/
  tags.py         XBRL tag ladders (data only)
  resolve.py      tags -> numbers
  formula.py      net assets, total debt, doubt gate, trend; owns Result
  report.py       terminal display
  html_report.py  HTML display (on the display branch only)
  earnings_calendar.py  the Nasdaq calendar (read path)
  calendar_archive.py   its archive (durable write path)
  jsonio.py       one atomic JSON write, shared by both writers
  sec_client.py / sec_cache.py
output/        CSVs from --date runs, HTML pages from --html (both gitignored)
calendar/      raw Nasdaq calendar rows, archived by every --date run.
               gitignored, but NOT safe to delete — see §5.4
SEC cache/     downloaded SEC data, safe to delete
archive (storage)/   retired screeners, imported by nothing — see §3.3
```

### 3.2 Delete safety and rename hazards

| directory | safe to delete? |
|---|---|
| `output/` | yes — regenerated by the next run |
| `SEC cache/` | yes — the next run re-fetches |
| `calendar/` | **no** — cannot be re-fetched at any price (§5.4) |
| `archive (storage)/` | no — sole copy of the coverage machinery (§3.3) |

The folder names above are the real ones on disk. A rename to `cache/` and
`archive/` has been floated but not carried out; doing it means moving the
folders **and** updating `CACHE_DIR` in `bscflib/sec_cache.py` in the same
change, or the next run silently re-downloads everything into a fresh folder
under the old name.

### 3.3 What is in `archive (storage)/`

Two retired copies of the original monolithic screener. They are **not**
duplicates of each other and neither is a superset:

| file | has | lacks |
|---|---|---|
| `Stage1-BSCF.py` | the doubt gate that became `formula.py` | coverage ratios |
| `Stage1-BSCF-with-coverage-ratios.py` | `debt_coverage`, `interest_expense`, and the year-to-date differencing quarterizer for TTM windows | the doubt gate |

The coverage machinery in the second file exists nowhere else in the repository
and in no earlier commit. It is the reference implementation if leverage and the
coverage ratios are ever added back (see §8), so it is kept deliberately rather
than by accident.

---

## 4. Modules

`tags.py` / `resolve.py` / `formula.py` are the **correctness core** — changing
them changes the numbers, not the presentation. `report.py` and
`html_report.py` are the **display layer**.

**When the task is display work, edit `bscflib/report.py` or
`bscflib/html_report.py` and nothing else.** That rule is scoped to display
work; it is not a general prohibition on the rest of the tree.

The record that travels between the two is `formula.Result`, a frozen
dataclass. It is the one contract every renderer reads; add a field there and
`report.CSV_COLUMNS` decides whether it reaches the CSV.

### 4.1 `bscf.py`

*Role.* The only file you run. Argument parsing, mode switching, and the loop
that turns a ticker list into results.

| function / type | role |
|---|---|
| `Skip` | `ticker`, `reason` — one ticker that produced nothing |
| `collect(tickers, refresh, verbose)` | ticker list -> `(results, skipped)` |
| `print_skipped(skipped)` | splits "outside coverage by design" from "data problems worth a look" |
| `calendar_tickers(target, min_cap)` | the day's calendar -> the ticker list |
| `archive_calendar()` | preserves the perishable half of the calendar |
| `render(results, target)` | whichever of the two output shapes the run calls for |
| `build_parser()` | the flags |
| `main()` | validation, mode switch, orchestration |

*Constants.* `MIN_MARKET_CAP = 300_000_000`, `OUTPUT_DIR`, `CALENDAR_DIR`.

*Worth knowing.*
- `sys.dont_write_bytecode = True` is set **before** `bscflib` is imported, to
  keep `__pycache__` folders out of the project. Costs a few ms per run.
- One bad ticker never ends a run: every `formula.analyze` call is wrapped, and
  the failure is recorded in `skipped`.
- The calendar fetch is **not** wrapped, deliberately — no calendar means no
  ticker list, so there is no partial work to salvage.
- `archive_calendar()` is **not** wrapped either, because
  `calendar_archive.capture` never raises (§4.6). The guarantee lives in one
  place rather than being asserted at both ends.
- Share classes are de-duplicated **by CIK**, not by ticker: `DGICA`/`DGICB` are
  one filer with one balance sheet, and would otherwise rank twice.
- Per-ticker progress prints only in screen mode (`verbose=target is not None`).
- A skip carries a `formula.Reason`, which is `(text, out_of_scope)`. The
  category used to be a magic prefix on the front of the text, matched with
  `startswith` and sliced back off by the printer.

### 4.2 `bscflib/tags.py`

*Role.* The XBRL tag ladders. **Data only — no logic lives here.** This is the
file to edit when a company resolves to a wrong or missing figure.

One shape for every lookup: `{field: {namespace: [rung, ...]}}`, walked by the
single resolver in `resolve.py`. A rung uses a two-operator syntax:

```
"TagA"              a single tag
"TagA|TagB"         alternative spellings of one concept, widest first
"TagA + TagB|TagC"  disjoint concepts summed
```

*Constants.*

| name | holds |
|---|---|
| `ANCHORS` | tags that establish a balance sheet date, per namespace |
| `BSCF_FIELDS` | the five formula components, in display order |
| `GAP_CODES` | short codes for the screen table's `gaps` column |
| `INSTANT_FIELDS` | the ladders themselves |
| `COMBINED_DEBT_TAGS` | captions that bundle current maturities into a non-current figure |
| `SWEEP_ASSET_PATTERNS` / `SWEEP_LIABILITY_PATTERNS` | sweep vocabulary |
| `SWEEP_LIABILITY_EXCLUDE` / `SWEEP_EXCLUDE_PATTERNS` | what the sweep must not match |
| `SWEEP_DETAIL_TAGS = 4` | source tags reported per side |

*Worth knowing.*
- **Rung order is load-bearing.** A rung naming a total must come before the
  rung naming that total's components, or a filer publishing both gets the same
  money counted twice.
- The sweep matches on **vocabulary**, not exact tag names, so it needs no
  per-ticker maintenance as the universe grows. It is the backstop for a filer
  using a tag no ladder knows.
- `SWEEP_LIABILITY_PATTERNS` is borrowings vocabulary only. Deferred credits,
  accruals and other long-term liabilities are out of the formula *by design*
  rather than missed, and sweeping them would flag every company with a pension.
- **`SWEEP_EXCLUDE_PATTERNS` is assembled from four named groups** —
  `_DISCLOSURE_ARTIFACTS`, `_NOT_IN_FORMULA`, `_OPERATING_INVESTMENTS`,
  `_NOT_A_BALANCE` — so a change can target one reason without re-reading all
  forty entries. The groups were always in the comments; they are now in the
  data.
- Matching is by substring, so **an entry containing another entry is dead
  weight.** `"unrealized"` (subsumed by `"realized"`) and
  `"fairvaluedisclosure"` (subsumed by `"fairvalue"`) were dropped for that
  reason and could never have fired.
- `_OPERATING_INVESTMENTS` excludes REIT/BDC operating assets — for a property
  trust, "investment" is the business, not a liquid balance.

### 4.3 `bscflib/resolve.py`

*Role.* Turning a companyfacts payload into numbers. Everything here enforces
the rules that stop a figure being quietly wrong: one balance sheet date per
company, the most recently filed value when several filings report that date,
the right taxonomy for a foreign filer, and one currency throughout.

| function / type | role |
|---|---|
| `Fact` | one observation: `end`, `val`, `filed` |
| `Resolution` | frozen: `value`, `tags`, `notes`, `stale` |
| `Resolution.resolved` | did a tag supply this figure **at the target date** |
| `Resolution.with_note` / `.less` | a new Resolution, annotated / netted down |
| `Basis` | `namespace`, `unit`, `as_of`, `filed`, `index` |
| `Index` | `tag -> {date: Fact}` |
| `parse_date(value)` | `"YYYY-MM-DD"` -> `date` |
| `_instants(facts, ns, tag, unit=None)` | every instant observation for one tag |
| `index_instants(facts, namespace, unit)` | build an `Index`, latest filing wins |
| `parse_rung(rung)` | the two-operator rung syntax -> slots (cached) |
| `_ladder_tags(ladder)` | every tag a ladder names, in order |
| `resolve_instant(index, ns, field, as_of)` | walk one ladder at one date |
| `anchor_dates(index, namespace)` | every balance sheet date published, oldest first |
| `choose_basis(facts)` | `Basis`, or `None` |
| `sweep_unclassified(...)` | balances in a category no ladder captured, largest first |

*Worth knowing.*
- **Latest filing wins.** Original filing, restatement, amendment and
  prior-period comparative all report the same period end; the most recently
  filed value is the live one.
- **Namespace is chosen by which taxonomy reports the newest balance sheet**,
  never by which one happens to exist. A foreign private issuer's `us-gaap`
  facts are often frozen years before its live `ifrs-full` ones.
- **`_instants` is the one payload shape the module walks.** Namespace
  selection, unit selection, the filed-date lookup and the sweep each used to
  spell out their own four-level nested loop over it.
- **`choose_basis` returns the index it built.** It has to construct one per
  candidate unit to score them; `analyze` used to throw the winner away and
  rebuild the same multi-megabyte walk immediately afterwards.
- `resolve_instant` only counts facts at exactly `as_of`. If nothing resolves
  there, it reports the newest *older* value as `stale`, so a figure the filer
  did publish is visibly excluded rather than silently read as absent —
  invariant §1.3 rule 3. **`Resolution.resolved` is the predicate for this**;
  it replaced `line and line.tags` spelled out at seven call sites.
- **`Resolution` is frozen.** The combined-debt netting used to edit it in
  place from `formula.py`, so nothing in `resolve.py` told you `value` was not
  final. `.less()` returns a new one, clamped at nil and saying so.
- `parse_rung` is cached — the ladders are static module data, and it was
  re-splitting the same strings for every field, at every date in the trend
  window, on both passes of every lookup.
- The bracket notes in the output originate here: `sum of N separately reported
  components` when a rung sums, and `caption bundles finance leases with
  borrowings` when a tag contains `CapitalLease`.
- The sweep excludes twice, because filers hide the same balance two ways:
  `used_tags` drops the exact tags the ladders consumed, `counted_values` drops
  the alternate spellings of those same amounts.

### 4.4 `bscflib/formula.py`

*Role.* The BSCF formula, and the checks that say whether to believe it.

| function / type | role |
|---|---|
| `Result` | **the record every renderer reads** — frozen, flat, 24 fields |
| `Snapshot` | the formula at one date |
| `SweepBasis` | what the sweep must not count again (`counted`, `used`) |
| `Reason` | `text`, `out_of_scope` — why a ticker produced no result |
| `snapshot(index, namespace, as_of)` | `(Snapshot, SweepBasis)`, or `None` |
| `_net_combined_debt(noncurrent, current)` | the combined-caption netting |
| `_overlaps(lines)` | debt lines exceeding their own subtotal |
| `_sweep(facts, basis, scaffold, gaps)` | both sides of the sweep |
| `analyze(ticker, cik, refresh)` | `(Result, None)` or `(None, Reason)` |

*Constants.*

| name | value / meaning |
|---|---|
| `TREND_QUARTERS` | `8` — balance sheet dates of history computed |
| `TREND_DISPLAY` | `4` — how many the table shows |
| `DOUBT_VS_SIGNAL_MAX` | `1.0` |
| `DOUBT_ABSOLUTE_MAX` | `0.25` |
| `ASSET_GAP_FIELDS` / `LIABILITY_GAP_FIELDS` | which gaps arm which side of the sweep |

*Worth knowing.*
- **`Result` is the contract.** It used to be a bare `dict` assembled by
  splatting `snapshot`'s return and adding twelve more keys, with its shape
  written down nowhere. `report.py` reads it by attribute, so a rename fails
  loudly instead of producing a blank CSV column.
- **`SweepBasis` is not part of a `Result`.** `counted` and `used` are
  scaffolding the sweep needs; they used to ride into every result and on into
  the renderers, which never read them.
- **Combined-caption netting.** When the non-current debt line comes from a
  `COMBINED_DEBT_TAGS` caption, the separately reported current portion is
  subtracted so the two debt lines cannot describe the same dollars. Netting
  below zero is clamped to nil and says so. Lives in `_net_combined_debt`.
- **The sweep only runs for a side that actually has a gap.** Without that it
  fires on companies whose components all resolved, where any hit is an
  overlapping disclosure restatement rather than a miss.
- **Each side's headline is its largest single unclassified balance, not a
  sum.** Overlapping disclosure tags make a sum meaningless; the largest is a
  floor on what the ladders missed and a figure the filer actually reported.
- **`doubt`** = (unspecified assets + unspecified liabilities) ÷ total assets —
  roughly how far `norm` could move if the swept balances belong in the formula.
  Judged against the signal rather than as a flat percentage: 5% doubt is noise
  against a −0.30 reading and fatal against a +0.001 one. `DOUBT_ABSOLUTE_MAX`
  catches a strong-looking ratio resting on a mostly unclassified sheet.
- **`trend_consistent`** is false when a company changed how it tags a component
  mid-history, because that produces a trend step that is a tagging artifact
  rather than a balance sheet move.
- **`overlap`** flags debt exceeding its own balance sheet subtotal — proof of
  double counting.

### 4.5 `bscflib/earnings_calendar.py`

*Role.* Nasdaq's earnings calendar — the only non-SEC source in the tool. Kept
separate from `sec_client` because it is a different service with different
manners: a browser User-Agent and an `Origin` header, uncached, and free to stop
answering without that being a SEC problem.

**This module is the read path only.** The archive that preserves these rows is
§4.6; the two share `fetch_raw` and nothing else.

| function / type | role |
|---|---|
| `Row` | `symbol`, `market_cap`, `market_cap_raw`, `timing` |
| `parse_market_cap(raw)` | `(value, unrecognised_text)` |
| `parse_timing(raw)` | `"pre-market"` / `"after-hours"` / `""` |
| `normalise_symbol(raw)` | the row's ticker, or `""` |
| `fetch_raw(day)` | raw Nasdaq rows, verbatim |
| `fetch(day)` | every company reporting on `day` |

*Constants.* `CALENDAR_URL`, `TIMEOUT = 20`, `BROWSER_USER_AGENT`,
`MARKET_CAP_PATTERN`, `TIMING`, `NOT_SUPPLIED`.

*Worth knowing.*
- **`parse_market_cap` is a tripwire, not a parser.** It distinguishes three
  outcomes — a figure, absent, and *present but unrecognized* — and both
  non-figures are kept, never dropped. Earlier code stripped non-digits and
  trusted the remainder, so a hypothetical `"$1.23B"` became `1.23` and was
  dropped by `--min-cap` as a company worth a dollar: a silent exclusion off a
  misread, invisible in the output *and* in the `Skipped` report. No unit
  interpretation is attempted — a `B`/`M` suffix handler would be guessing at a
  schema change nobody has observed. Evidence in §5.3.
- **`MARKET_CAP_PATTERN` requires a leading digit**, which makes the pattern the
  single thing deciding whether a string is a figure — the parse below it needs
  no guards of its own. It also means comma-leading junk (`"$,,5"`) trips the
  wire instead of quietly parsing as `5`.
- The unrecognized-format warning is rendered by `report.calendar_warning` and
  **is silent while the format holds.** The first run that prints it is the run
  where Nasdaq changed something, which for an undocumented endpoint is the only
  warning there will be.
- `symbol` is load-bearing; `marketCap` is advisory. `normalise_symbol` treats a
  whitespace-only symbol as absent, which `fetch` previously let through as a
  `Row` with an empty ticker.
- Ticker separator reconciliation (`BRK.B` vs `BRK-B`) is **not** done here — it
  belongs to SEC's ticker file and lives in `sec_client.lookup_cik`.

### 4.6 `bscflib/calendar_archive.py`

*Role.* The calendar archive — the one thing this tool writes that it cannot
re-read. Split from `earnings_calendar` because it is the opposite kind of job:
that module reads a list for the screen and forgets it, this one owns durable
files with their own atomicity, merge and never-overwrite rules.

| function / type | role |
|---|---|
| `Capture` | `dates`, `rows`, `timed`, `failures` |
| `capture_path(directory, day)` | `calendar/<date>.json` |
| `_merge(record, rows, observed)` | fold one observation into a day's archive |
| `_archive_day(path, day, rows, observed)` | read, merge and write one day |
| `capture(directory, start, days)` | archive the raw rows for a forward window |

*Constants.* `CAPTURE_WINDOW_DAYS = 14`, `CAPTURE_INTERVAL = 0.3`.

*Worth knowing.*
- **`capture` never raises, for any reason** — including an unusable directory.
  An incomplete archive is a cost; a run that dies before it reaches SEC is a
  bigger one. Callers do not need a guard, and `bscf.py` no longer has one.
- **It catches transport errors and malformed JSON only.** A payload *shape*
  change raises out of it on purpose: this module's premise is that a silent
  change to the feed is the failure worth stopping for, so folding one into a
  failure tally would contradict the tripwire in §4.5.
- **`_merge` returns what the archive now holds**, not what the fetch happened
  to see. The two differ exactly when Nasdaq retracts a slot it already gave —
  which is the case the never-overwrite rule exists for, so the counter now
  reflects the protection instead of hiding it.
- A confirmed slot is never overwritten by a later "not supplied"; a *different*
  confirmed slot does replace it, and every change is appended to
  `timing_history` with the time it was seen.
- Writes go through `jsonio.write_json_atomic` (§4.11). A file that will not
  parse is **left alone**, counted as a failure, never overwritten.
- Merge rules in full: §5.4.

### 4.7 `bscflib/report.py` — terminal

*Role.* The printed breakdown, the tables and the CSV.

| function / type | role |
|---|---|
| `money(value, unit)` | `$1,234,567`, or `TWD 1,234,567` for non-USD filers |
| `millions(value)` | scaled column for the wide screen table |
| `ratio(value, places=3)` | the normalized result |
| `calendar_warning(rows)` | the unrecognized-market-cap tripwire, as lines |
| `flags(result)` | builds the `*` `~` `!` suffix on a ticker |
| `_header` / `_ledger` / `_totals` | the three parts of a breakdown body |
| `_excluded` / `_sweep` / `_overlap` | the caveat sections |
| `render(result, column)` | the per-company breakdown block |
| `column_width(results)` | shared money-column width across a run |
| `Column` | one table column: `header`, `align`, `value` |
| `_table(columns, results)` | generic fixed-width table builder |
| `summary(results)` | compact table, detail mode |
| `screen(results, target)` | wide table, screen mode |
| `legend(results)` / `trend(...)` | footers |
| `write_csv(results, path)` | full record incl. things the tables omit |

*Constants.* `WIDTH = 78`, `MILLIONS`, `STALE_AFTER_DAYS = 180`, `LABELS`,
`EXCLUDED_LINES`, `SECTIONS`, `SUMMARY_COLUMNS`, `SCREEN_COLUMNS`,
`CSV_PLAIN` / `CSV_DERIVED` / `CSV_COLUMNS`.

*Worth knowing.*
- **`render` is a list of sections**, each returning its own lines or nothing.
  A section with nothing to say emits no rule either.
- **A `Column` carries its header, its alignment and how to read its value.**
  These used to be a header list and a positional alignment string
  (`"<<<>>>>><<"`) that had to be counted against each other by hand; a miscount
  gave an `IndexError` or a silent misalignment.
- `_table` renders an empty row set as headers plus rules. It used to raise
  `TypeError` on one — unreachable from the two callers, but it is billed as
  generic.
- **Screen mode ranks a missing `norm` last via the sort key**
  (`(r.norm is None, -(r.norm or 0.0))`), not via a `-9e9` sentinel standing in
  for one.
- **The CSV schema is declared, not derived from `Result`'s field order.**
  Stage 2 reads that file, so an unrelated edit to `Result` must not silently
  reorder or add a column. What the declaration buys is that every value is read
  by name off the dataclass — a renamed field raises instead of writing a blank
  cell. `_check_csv_schema()` runs at import and fails if the two have drifted.

### 4.8 `bscflib/html_report.py` — a self-contained page

*Role.* One `.html` file with the CSS and JS inlined. No build step, no server,
no CDN, no network at open time — it works from `file://`. Vanilla everything.

> **Not on the `logic` branch.** This module lives on `display`. The entry below
> describes it as built there; it has **not** been migrated to `formula.Result`
> or to the `report.py` surface documented in §4.7, and will need both when the
> branches meet.

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

*Constants.* **The `CONFIG` block at the top of the module owns every threshold
and colour** — `BANDS` (five verdict bands, read on `norm` so they mean the same
thing for a $5bn company and a $3tn one), `DOUBT_NOTICE` / `DOUBT_ALARM` /
`DOUBT_METER_FULL`, `AGE_WARN` / `AGE_ALARM`, `NORM_SCALE`, `TREND_FLAT`, and
`PALETTE`. Nothing emphasised or coloured is set anywhere else; these are meant
to be tuned. The block is also serialized into every page as
`<script type="application/json" id="bscf-config">`, so a saved file explains
the thresholds it was drawn with.

*Worth knowing.*
- `page()` takes a **list** at every size. One company is a one-card page; with
  two or more an index rail appears above the cards. That is the seam a ranked
  multi-company view grows from — no rewrite needed.
- `money`, `ratio`, `flags`, `LABELS` and `STALE_AFTER_DAYS` are imported from
  `report.py` rather than reimplemented, so the terminal and the page cannot
  drift apart on what a number looks like. **`flags` now takes a `Result`, and
  the section helpers in `report.py` have the same names as the ones here** —
  the merge should reconcile the two, not duplicate them.
- How the §1.3 formatting rules survive the medium:
  1. the tag is printed under each figure, not hidden behind a hover — a
     screenshot has to stay diagnosable
  2. one CSS grid track sized to the widest cell gives the same shared
     right-aligned money column the fixed-width terminal column does
  3. an absent figure still prints its reason (`not reported`, `EXCLUDED` with
     the date and amount alongside), never a bare zero
  4. a derived figure's bracket note renders as an amber pill, a visibly
     different shape from a grey tag chip, so provenance is never read as
     derivation
  5. flags ride the ticker and carry their meaning next to them, with the legend
     kept at the foot as a reminder rather than a lookup

### 4.9 `bscflib/sec_client.py`

*Role.* Talking to SEC EDGAR: rate limiting, retries, and the ticker -> CIK map.

| function | role |
|---|---|
| `sec_get(url)` | rate-limited, retried GET; `None` on a clean 404 |
| `cached_sec_get(url, refresh)` | `sec_get` through `sec_cache` |
| `fetch_ticker_cik_map(refresh)` | `{TICKER: cik}` from `company_tickers.json` |
| `lookup_cik(ticker, cik_map)` | ticker -> CIK, reconciling share-class separators |
| `fetch_company_facts(cik, refresh)` | the companyfacts payload |

*Constants.* `SEC_USER_AGENT` (from the environment), `SEC_TICKER_MAP_URL`,
`SEC_COMPANY_FACTS_URL`, `SEC_REQUEST_INTERVAL = 0.12`, `SEC_TIMEOUT = 60`,
`SEC_MAX_RETRIES = 3`, `USER_AGENT_HELP`.

*Worth knowing.*
- EDGAR returns 403 to any request without a real contact address. The
  User-Agent here is an **honest identification** — the opposite posture from
  `earnings_calendar`'s browser disguise, which is why the two clients are
  separate modules.
- **404 is the only status that returns `None`**, because it is the only one
  meaning "this filer has nothing published". Every other outcome raises.
  `raise_for_status` fires only on 4xx/5xx, so a 3xx or a non-200 2xx used to
  fall out of the retry loop as `None` and reach the user as *"no XBRL company
  facts published"* — a claim about the company derived from a transport
  oddity, which is what §1.3 rule 6 forbids.
- `SEC_REQUEST_INTERVAL = 0.12` sits under EDGAR's published 10 requests/second
  ceiling. Retries back off as `2**attempt`.
- `lookup_cik` tries `ticker`, then `.`→`-`, then `/`→`-`, because Nasdaq writes
  `BRK.B` or `BRK/B` where SEC's file writes `BRK-B`.
- `_last_call` is module-level mutable state — the only hidden state in the
  project. Fine for a single-threaded CLI; worth knowing it is there.

### 4.10 `bscflib/sec_cache.py`

*Role.* On-disk cache for SEC responses.

| function | role |
|---|---|
| `cache_path(url)` | SHA1-derived filename inside `CACHE_DIR` |
| `load(url, refresh)` | cached payload, or `None` |
| `store(url, payload)` | write one payload, atomically |

*Constants.* `PROJECT_ROOT`, `CACHE_DIR = "<root>/SEC cache"`,
`CACHE_TTL_HOURS = 24`.

*Worth knowing.*
- A single companyfacts payload runs to several megabytes, so a 300-name
  earnings day is gigabytes of repeat download every time a threshold is tuned.
- **Staleness costs almost nothing here:** a company reporting today has not
  filed its new 10-Q yet, so the figures being screened are last quarter's
  either way.
- `store` writes through `jsonio.write_json_atomic` (§4.11). A corrupt cache
  file self-heals into a re-fetch, so it does not strictly need atomicity — but
  two writers with two disciplines is how the careful one gets edited into the
  careless one.
- Renaming the folder is fine, but `CACHE_DIR` has to follow it in the same
  change — see §3.2.

### 4.11 `bscflib/jsonio.py`

*Role.* One atomic JSON write, shared by both on-disk writers.

| function | role |
|---|---|
| `write_json_atomic(path, payload, **dump_kwargs)` | write via a temp file, then `os.replace` |

*Worth knowing.*
- The calendar archive needs this because its files cannot be re-fetched at any
  price. The SEC cache uses it so the project has one discipline rather than
  two.
- Creates the parent directory if it is missing.

---

## 5. Data sources and what is known about them

Empirical claims here carry the date they were checked. They describe services
this project does not control.

### 5.1 SEC EDGAR companyfacts

`https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`

- Requires a contact address in the User-Agent or returns 403.
- Published ceiling of 10 requests/second.
- **companyfacts carries no roll-up hierarchy.** Nothing in the payload says
  which balances are components of which totals, so a swept tag is either
  already inside a total above or a genuine miss, and only the filing settles
  which. This is the reason the doubt gate quantifies an unknown rather than
  correcting a figure.
- One period end can appear several times — original filing, restatement,
  amendment, prior-period comparative. The most recently filed value is live.
- A foreign private issuer commonly carries both `us-gaap` and `ifrs-full`
  facts, with the `us-gaap` set frozen years earlier.

### 5.2 Nasdaq earnings calendar

`https://api.nasdaq.com/api/calendar/earnings?date=YYYY-MM-DD`

Not a published API — it is the endpoint nasdaq.com's own front end calls,
reached with a browser User-Agent plus `Origin` and `Referer` headers. There is
no contract, and it can change or stop without notice.

**Verified Sep 2026:**

- A date with no earnings answers HTTP 200 with a null where the data should be,
  in **either** of two shapes: `{"data": null}` (seen on 2026-12-25) and
  `{"data": {"rows": null}}` (seen on 2026-09-13, a Sunday). Both are real, which
  is why the unwrap in `_get` needs an `or` at each step.
- The row schema **flips** between future and past dates. Forward rows carry
  `lastYearRptDt` and `lastYearEPS`; historical rows replace those with `eps`
  and `surprise`. Seven fields are common to both.
- The `time` field takes exactly three values: `time-pre-market`,
  `time-after-hours`, `time-not-supplied`.

### 5.3 The market cap tripwire

`marketCap` arrives as a display string, not a number. Three outcomes are
distinguished (implementation and rationale in §4.5):

| input | result |
|---|---|
| `"$24,715,929,600"` | a figure |
| `""` / `"N/A"` | unknown — kept, looked up at SEC |
| anything not matching `MARKET_CAP_PATTERN` | unknown — kept, **and warned about** |

**Verified Sep 2026**, across 5,557 rows over 37 dates (Oct 2025 – Nov 2026):

- The format is invariably `$` + comma-grouped whole dollars. Zero decimals,
  zero abbreviations (`"$1.2B"`), zero non-ASCII characters, zero violations of
  `^\$[\d,]+(\.\d+)?$`.
- **Always USD.** Foreign filers are quoted at their US listing, not their home
  market — `TM $254,774,264,000`, `SONY $141,033,570,000`, `MUFG
  $281,383,436,000`, each off by orders of magnitude from its home currency. So
  the `--min-cap` numeric comparison is sound.
- Blanks are real but rare (~0.05%): SPACs and commodity trusts — `LKSP`,
  `GTEN`, `BAR` — file earnings with no market cap on the row.
- Observed range: `$452,551` (FRGT) to `$4,835,635,600,000` (AAPL).

### 5.4 The `calendar/` archive

One JSON file per earnings date, written by every `--date` run. It archives the
raw Nasdaq calendar rows verbatim — all nine fields, not just the two the screen
reads.

It exists because **one field on those rows is perishable.** Nasdaq populates
`time` (before-open vs after-close) only while a date is still in the future,
and replaces it with `time-not-supplied` once the date has passed.

**Verified Sep 2026:** of 5,253 historical rows spanning Oct 2025 to Sep 2026,
**none** carried a timing value. Coverage on a future date also improves as the
date nears and companies confirm — roughly 4% of screened names six weeks out,
~90% inside two weeks. *Caveat:* every well-covered date in that sample was a
quiet day and every busy day was six weeks out, so "busy day, close in" is
promising but not established. The archive itself is the experiment that settles
it.

So a day not recorded before it happens cannot be recovered from this API.
Everything else the tool writes can be produced again from its sources; these
files cannot, which is why they are never safe to delete.

They are nonetheless **gitignored**, like `output/` and `SEC cache/`. This is
the one place in the tree where gitignored does not imply regenerable, so the
usual inference does not hold: git is not this data's backup. Keeping it out of
version control is deliberate — a growing pile of third-party rows would bloat
the history and conflict on every branch — but it means the only copy is the
working directory. Back `calendar/` up somewhere outside the repository.

The archive runs on **every** `--date` run, over a forward window of today
through today+14 (`calendar_archive.CAPTURE_WINDOW_DAYS`), regardless of which date is being
screened — what it preserves is a property of *now*, not of the target. Each
upcoming day is therefore observed on every run between now and the day it
happens, so a slot confirmed after the last screen is still recorded.

Two rules in the merge, both there to protect the record:

- **A confirmed slot is never overwritten by a later `not-supplied`.** Without
  this, one run after the date passed would erase the archive's whole reason
  for existing. A *different* confirmed slot does replace it — companies move —
  and every change is appended to `timing_history` with the time it was seen.
- **An archive file that will not parse is never overwritten.** It is counted
  as a failure and left alone to be looked at.

Capture failures never end a run. Rough volume: ~450 bytes per company-day, so
single-digit MB per year.

---

## 6. Output reference

### 6.1 Detail mode

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

### 6.2 Screen mode

The run preamble, then the table:

```
Fetching Nasdaq earnings calendar for 2026-09-23...
  9 ticker(s) reporting.
  1 below $300M market cap, 8 remain.
  Archived 115 calendar row(s) over 11 upcoming date(s); 39 carry a confirmed before-open/after-close slot.
```

A market cap in an unrecognized format adds a warning between the count and the
filter lines — silent while the format holds (§4.5):

```
  WARNING: 3 market cap value(s) in an unrecognised format (FAKEA "$1.23B", FAKEB "1,234 USD", FAKEC "TWD 9,999").
           Treated as unknown and kept - --min-cap did not filter them.
```

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

### 6.3 Flags

| flag | meaning |
|---|---|
| `*` | balance sheet older than 180 days (20-F filers report annually) |
| `~` | a line item was excluded as stale — see that company's breakdown |
| `!` | unclassified balances outweigh the signal; weak seed however extreme the ratio |

### 6.4 CSV and HTML

`write_csv` carries the **full record**, including fields the terminal tables
omit. HTML is one self-contained page per run, one card per company — see §4.8.

---

## 7. Known rough edges

All of these describe the **terminal** renderer. `--html` was built partly to
dissolve them rather than patch them; where it does, the status line says so.

### Redundant column
*Status:* open.
The summary's last column (`Net Cash` / `Net Debt`) is unheaded and says the
same thing as the sign on `RESULT`.

### Source lines run arbitrarily long
*Status:* open in terminal · resolved in HTML (verified on TSM).
A three-component IFRS sum plus its bracket note is ~250 characters and blows
past any terminal width. No wrapping or truncation strategy exists. In HTML each
tag is its own chip on a row that spans the grid, so TSM's three-tag sums wrap
instead of running off the end.

### Two different rulers
*Status:* open.
`render()` uses a fixed `WIDTH = 78` for its rules, while `summary()` and
`screen()` compute their own width from content, so the separators in one run
don't line up with each other.

### Dangling separator
*Status:* **not reproducible — entry kept as a record.**
The claim was that a company with no excluded lines and no sweep hits ends its
block on a trailing `---` rule with nothing under it. Checked Sep 2026 against
MSFT, AAPL, NVDA, TSM, ARCC and KO, covering every combination of excluded /
sweep / overlap present or absent: no block ends on a rule. `render()` only
ever emits a rule as the *opening* of a section that goes on to emit content,
so there is nothing to leave hanging. It is now structurally impossible as
well: `render()` is a list of section functions (§4.7) and a section with
nothing to say returns no lines at all, rule included.

### Non-USD money columns aren't comparable across rows
*Status:* open in terminal · unchanged in HTML, deliberately.
Screen mode dodges this by ranking on `norm`; detail mode just prints the
currency code. In HTML the currency is printed, the chip is highlighted and the
card says "figures as filed in TWD - not converted". Nothing is converted,
because the data does not support the comparison.

### The sweep note is four lines of prose
*Status:* open in terminal · resolved in HTML.
It sits inside an otherwise tabular block. In HTML it is a collapsed
`<details>` on the caveat panel — present and discoverable, not shouting.

---

## 8. Deliberately absent / deferred

### EBITDA, leverage and the coverage ratios

**EBITDA and leverage (`Total Debt / EBITDA`) were removed at the user's
request** and may be added back later as a separate piece of work. Operating cash flow and interest expense went with them, since
all three read XBRL *duration* facts and shared the same machinery (a
year-to-date differencing quarterizer for TTM windows). If they return, the
display needs a row for EBITDA with its period, a leverage row, and a `#` flag
for "EBITDA unavailable" — in both renderers now, and in `html_report.py` a band
for leverage would go in the `CONFIG` block with the rest.

The quarterizer and the `debt_coverage` / `interest_coverage` implementations
survive only in `archive (storage)/Stage1-BSCF-with-coverage-ratios.py` (§3.3).
Read that before rebuilding any of it from scratch.

### Earnings timing, in the pipeline

`calendar/` records before-open/after-close for every upcoming date, and
`earnings_calendar.Row` carries it as `timing`, but **nothing downstream reads
it** — it is not on `formula.Result`, the screen table, the CSV or the HTML.
That is deliberate. Stage 2 needs it (pre-market volume, VWAP anchored to the
open, pre/post-gap RSI resets), but its requirements are not concrete yet, and
threading one field through `Result`, `write_csv` and the table formats would
fix a schema before there is anything to fix it against. The data is perishable
and the schema is not, so the perishable half was done now and the schema half
left until Stage 2 can specify it. The archive keeps every field, so that
decision stays open.

### Scheduled capture

The archive only preserves days on which a `--date` run actually happened. A
stretch without running is a permanent hole. Running the capture on a schedule,
independently of screening, would close that — not built, not scoped.

---

## 9. How the user likes to work

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
  stale — layout, module surface, flags, rough edges, this list; the routing
  table at the top says where each belongs. Stage it in the same commit, never a
  follow-up. Correct inaccuracies even where a human wrote them, and say so in
  the report rather than silently.
- **If an instruction rests on a premise that turns out to be false, stop and
  say so** instead of executing it. "Delete it if it's the same file" is not
  authorization to delete when the files differ.
- The user is new to Python but not new to the domain — pitch explanations of
  Python mechanics plainly, and financial/XBRL reasoning at expert level.
