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

## bscf.py

not yet written

## bscflib/tags.py

not yet written

## bscflib/resolve.py

not yet written

## bscflib/formula.py

not yet written

## bscflib/report.py

not yet written

## bscflib/html_report.py

not yet written

## bscflib/sec_client.py and sec_cache.py

not yet written

## bscflib/earnings_calendar.py

1. You're not calling a real API — you're pretending to be a browser hitting Nasdaq's own website. That can break with zero warning whenever Nasdaq changes anything. Nothing to fix here necessarily, but worth knowingYou're not calling a real API — you're pretending to be a browser hitting Nasdaq's own website. That can break with zero warning whenever Nasdaq changes anything. Nothing to fix here necessarily, but worth knowing.
2. Market cap will be presented in the display in the future, a feature will also be added for when market cap isnt available it will just display N/A.
3. A feature will be added in the future to revise that the most important companies enter the equation and treating coverage gaps.


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
