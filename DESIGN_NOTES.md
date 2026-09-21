# BSCF — design notes (the user's own)

The counterpart to `CONTEXT.md`.

`CONTEXT.md` is the spec: what the tool does, how it is structured, what must
not change. It is written for a fresh Claude Code session to read cold.

**This file is the user's**, not the assistant's. It holds the user's own
working understanding of the code, in the user's own words, built up while
reading through it. A fresh session should read it alongside `CONTEXT.md` at
the start of a session, to know what the user has already worked out and
therefore what does not need re-explaining.

Rules for a session reading this file:

- Read it, don't rewrite it. Sections are filled in by hand, over time.
- Empty or `not yet written` means exactly that — not an invitation to fill it.
- Where it disagrees with the code, the code wins, but say so rather than
  quietly correcting the note.

---
## jsonio.py 

 — One function: write JSON via a temp file then os.replace, so an interrupted run can't leave a half-written file. Used by both sec_cache and calendar_archive.

## bscf.py

not yet written

## bscflib/tags.py

— The XBRL tag ladders, as pure data. Which tag names mean "cash", "short-term investments", "debt (current)" and so on, per namespace, in priority order. Plus the sweep vocabulary. This is the file you edit when a company resolves to a wrong or missing figure.

## bscflib/resolve.py

— Turns a raw SEC payload into numbers. Picks one balance-sheet date, one taxonomy and one currency for the whole company, then walks a ladder from tags.py to produce a Resolution (a value, the tags that produced it, any notes). Enforces the rules that stop a figure being quietly wrong.

## bscflib/formula.py

— The formula itself and the checks on whether to trust it. Net assets minus total debt, the trend over eight quarters, the doubt gate, the double-counting check. Produces Result — the one record every renderer reads.

## bscflib/report.py

— All terminal output: the per-company breakdown, the two tables, the legend, and the CSV. Every figure is printed with the tag it came from.

## bscflib/html_report.py

 — Temporary display section

## bscflib/sec_client.py and sec_cache.py

Talks to SEC EDGAR. Rate limiting (under their 10/sec ceiling), retries with backoff, and the ticker → CIK lookup that reconciles BRK.B against SEC's BRK-B.
— Caches SEC responses to disk for 24 hours. A single companyfacts payload is several megabytes, so without this, tuning a threshold on a 300-name day means re-downloading gigabytes.

## bscflib/earnings_calendar.py

1. You're not calling a real API — you're pretending to be a browser hitting Nasdaq's own website. That can break with zero warning whenever Nasdaq changes anything. Nothing to fix here necessarily, but worth knowingYou're not calling a real API — you're pretending to be a browser hitting Nasdaq's own website. That can break with zero warning whenever Nasdaq changes anything. Nothing to fix here necessarily, but worth knowing.
2. Market cap will be presented in the display in the future, a feature will also be added for when market cap isnt available it will just display N/A.
3. A feature will be added in the future to revise that the most important companies enter the equation and treating coverage gaps.

— Fetches Nasdaq's earnings calendar for a given day: the ticker list a --date screen runs on. The only non-SEC source. Contains the market-cap tripwire that refuses to guess at an unfamiliar format.
---

# Concepts

## The tag ladders

not yet written

## The doubt sweep

not yet written

## Stale exclusion

not yet written

## norm, and why it is the ranking column

not yet written

## The trend window

not yet written

---

# Open questions

- Revise display for improvements (visual,make sure of what will be displayed and how, and that every formula is either being used and displayed)
- display for pre/after market call

# Things I changed my mind about

not yet written

