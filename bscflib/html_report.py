"""A self-contained HTML page for the per-company breakdown.

A second output target for what `report.render()` already prints, not a
replacement for it: a run can emit both and the two are meant to agree figure
for figure. Nothing here reads SEC data or computes anything - it takes the
finished `result` dict and draws it.

Every rule that makes the terminal output trustworthy survives:

  1. every figure shows the exact XBRL tag it came from, on the page rather
     than behind a hover, because a screenshot has to stay diagnosable
  2. money is right-aligned in one shared column - here a CSS grid track sized
     to the widest cell, which is the same guarantee the fixed-width column
     gives the terminal
  3. an absent figure prints its reason, never a bare zero
  4. a derived or fallback figure carries its bracket note, styled differently
     from a tag so provenance and derivation cannot be confused
  5. flags ride the ticker and carry their meaning next to them

The money, ratio and flag formatters are imported from `report` rather than
reimplemented, so the two renderers cannot drift apart on what a number looks
like.

    page(results) -> str      full document, one card per company
    write(results, path)      that document on disk

`page` takes a list and always has: a single company is a one-card page, and a
ranked list later is the same call with more cards.
"""

from __future__ import annotations

import html
import json
import os
from datetime import datetime

from .formula import TREND_DISPLAY
from .report import LABELS, STALE_AFTER_DAYS, flags, money, ratio

# ==========================================================================
# CONFIG - everything tunable. No threshold or colour is set anywhere else.
# ==========================================================================

# Verdict bands, widest first. Read on `norm` (result over total assets), not
# on the dollar result: norm is the only figure comparable across filers, so a
# band set on it means the same thing for a $5bn company and a $3tn one.
# Matching walks top down and takes the first band whose floor `norm` clears.
BANDS = [
    {"key": "strong-cash", "floor": 0.20, "verdict": "NET CASH", "gloss": "strong"},
    {"key": "cash", "floor": 0.05, "verdict": "NET CASH", "gloss": "comfortable"},
    {"key": "balanced", "floor": -0.05, "verdict": "NET CASH", "gloss": "roughly balanced"},
    {"key": "debt", "floor": -0.20, "verdict": "NET DEBT", "gloss": "carried"},
    {"key": "heavy-debt", "floor": float("-inf"), "verdict": "NET DEBT", "gloss": "heavy"},
]

# The unclassified pool over total assets. `doubtful` itself is decided in
# formula.py against the signal; these only drive how loud the meter looks.
DOUBT_NOTICE = 0.02   # above this the meter is drawn in the caution colour
DOUBT_ALARM = 0.10    # above this it is drawn in the alarm colour
DOUBT_METER_FULL = 0.25  # doubt that fills the meter end to end

# Balance sheet age. The first is report.STALE_AFTER_DAYS and drives the `*`
# flag; the second is presentation only - a 20-F filer a year and a half back.
AGE_WARN = STALE_AFTER_DAYS
AGE_ALARM = 400

# Ends of the fixed axis the normalized result is plotted on. Fixed rather than
# data-derived so the marker sits in the same place for the same reading every
# time; a value outside it is clamped and marked as off-scale.
NORM_SCALE = (-0.50, 0.50)

# Change across the displayed trend window below which it reads as flat.
TREND_FLAT = 0.02

# One place for the whole colour scheme; emitted as CSS custom properties.
PALETTE = {
    "ground": "#0d1014",
    "surface": "#151a21",
    "surface-2": "#1b222b",
    "rule": "#252e3a",
    "ink": "#e8eef5",
    "ink-dim": "#94a1b1",
    "ink-faint": "#5d6a79",
    "asset": "#3fbfa8",
    "asset-soft": "rgba(63,191,168,0.16)",
    "debt": "#e0a03c",
    "debt-soft": "rgba(224,160,60,0.16)",
    "alarm": "#f0556f",
    "alarm-soft": "rgba(240,85,111,0.16)",
    "caution": "#e0a03c",
    # The caveat panel is deliberately a different material: warm paper against
    # the instrument's cool ground, so an epistemic note never reads as data.
    "caveat-bg": "#1d1913",
    "caveat-ink": "#d9cdb5",
    "caveat-dim": "#9a8d74",
    "caveat-edge": "#8a7444",
}

# Band -> the accent the verdict, beam overhang and norm marker take.
BAND_ACCENT = {
    "strong-cash": "var(--asset)",
    "cash": "var(--asset)",
    "balanced": "var(--ink-dim)",
    "debt": "var(--debt)",
    "heavy-debt": "var(--alarm)",
}

FLAG_MEANINGS = {
    "*": f"balance sheet older than {STALE_AFTER_DAYS} days",
    "~": "a line item was excluded as stale",
    "!": "unclassified balances outweigh the signal - weak seed however "
         "extreme the ratio looks",
}

ASSET_FIELDS = ("cash", "short_term_investments", "long_term_investments")
DEBT_FIELDS = ("debt_current", "debt_noncurrent")
EXCLUDED_FIELDS = (("other_lt_liabilities", "Other long-term liabilities"),
                   ("lease_liabilities", "Lease liabilities"))


# ==========================================================================
# Small formatters
# ==========================================================================


def compact(value: float, unit: str) -> str:
    """$72.9B - the glanceable form. Always printed beside the exact figure,
    never instead of it: this one is for reading across the room."""
    prefix = "$" if unit == "USD" else f"{unit} "
    sign = "-" if value < 0 else ""
    size = abs(value)
    for cut, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if size >= cut:
            scaled = size / cut
            places = 0 if scaled >= 100 else (1 if scaled >= 10 else 2)
            return f"{sign}{prefix}{scaled:,.{places}f}{suffix}"
    return f"{sign}{prefix}{size:,.0f}"


def band(result: dict) -> dict:
    """Which verdict band a reading falls in."""
    norm = result["norm"]
    if norm is None:
        # No total assets to normalize against; the sign is all there is.
        return BANDS[1] if result["core"] >= 0 else BANDS[3]
    for entry in BANDS:
        if norm >= entry["floor"]:
            return entry
    return BANDS[-1]


def share(part: float, whole: float) -> float:
    """Percentage width for a bar, guarded against an empty denominator."""
    if not whole:
        return 0.0
    return max(0.0, min(100.0, abs(part) / abs(whole) * 100.0))


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


# ==========================================================================
# Stylesheet. Inlined into every page so the file works from file:// with no
# network, no build step and no CDN.
# ==========================================================================

STYLE = """
*,*::before,*::after{box-sizing:border-box}
:root{
  color-scheme:dark;
  --mono:ui-monospace,"SF Mono","Cascadia Mono",Menlo,Consolas,"Liberation Mono",monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,Helvetica,Arial,sans-serif;
  --serif:Georgia,"Iowan Old Style","Times New Roman",serif;
}
body{margin:0;background:var(--ground);color:var(--ink);font:14px/1.55 var(--sans);
  -webkit-font-smoothing:antialiased;padding:34px 20px 64px}
.wrap{max-width:1080px;margin:0 auto}
h1,h2,h3{margin:0;font-weight:600}

/* ---- page masthead ---------------------------------------------------- */
.masthead{display:flex;align-items:baseline;justify-content:space-between;
  gap:16px;flex-wrap:wrap;margin-bottom:22px;padding-bottom:14px;
  border-bottom:1px solid var(--rule)}
.masthead .mark{font:600 15px/1 var(--sans);letter-spacing:.22em;color:var(--ink)}
.masthead .sub{color:var(--ink-faint);font-size:12px}
.formula{color:var(--ink-faint);font-size:12px;font-family:var(--mono);margin-top:5px}

/* ---- index rail, shown only when the page carries several companies ---- */
.rail{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:22px}
.rail a{display:flex;align-items:center;gap:9px;text-decoration:none;color:var(--ink-dim);
  background:var(--surface);border:1px solid var(--rule);border-radius:999px;
  padding:6px 14px 6px 10px;font-size:12px}
.rail a:hover{border-color:var(--ink-faint);color:var(--ink)}
.rail b{color:var(--ink);font-family:var(--mono);font-size:12.5px}
.rail .val{font-family:var(--mono);font-variant-numeric:tabular-nums}
.rail .dot{width:8px;height:8px;border-radius:50%;flex:none}

/* ---- card ------------------------------------------------------------- */
.card{background:var(--surface);border:1px solid var(--rule);border-radius:14px;
  overflow:hidden;margin-bottom:30px;scroll-margin-top:20px}
.head{padding:20px 28px 16px}
.title{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
.ticker{font:600 25px/1 var(--mono);letter-spacing:.02em}
.company{color:var(--ink-dim);font-size:13.5px;letter-spacing:.02em}
.meta{display:flex;flex-wrap:wrap;gap:7px;margin-top:13px}
.chip{font-size:11px;letter-spacing:.04em;padding:3px 9px;border-radius:5px;
  background:var(--surface-2);color:var(--ink-dim);border:1px solid var(--rule);
  font-family:var(--mono);white-space:nowrap}
.chip b{color:var(--ink);font-weight:500}
.chip.warn{color:var(--caution);border-color:rgba(224,160,60,.45);background:var(--debt-soft)}
.chip.bad{color:var(--alarm);border-color:rgba(240,85,111,.45);background:var(--alarm-soft)}
.chip.good{color:var(--asset);border-color:rgba(63,191,168,.4);background:var(--asset-soft)}
/* a flag carries its meaning beside it, so the legend is a reminder and not a
   lookup */
.flagchip{display:inline-flex;align-items:center;gap:7px;font-family:var(--sans);
  letter-spacing:0;white-space:normal}
.flagchip .g{font-family:var(--mono);font-weight:700;font-size:13px;line-height:1}

/* ---- verdict ---------------------------------------------------------- */
.verdict{position:relative;display:grid;grid-template-columns:minmax(0,1fr) auto;
  gap:30px;align-items:center;padding:24px 28px;border-top:1px solid var(--rule);
  border-bottom:1px solid var(--rule)}
.verdict.strong-cash,.verdict.cash{background:
  radial-gradient(120% 160% at 0% 50%,rgba(63,191,168,.15),transparent 62%),var(--surface-2)}
.verdict.balanced{background:var(--surface-2)}
.verdict.debt{background:
  radial-gradient(120% 160% at 0% 50%,rgba(224,160,60,.15),transparent 62%),var(--surface-2)}
.verdict.heavy-debt{background:
  radial-gradient(120% 160% at 0% 50%,rgba(240,85,111,.18),transparent 62%),var(--surface-2)}
/* a distrusted reading gets a texture, not a footnote */
.verdict.distrusted::after{content:"";position:absolute;inset:0;pointer-events:none;
  background:repeating-linear-gradient(135deg,transparent 0 11px,
    rgba(240,85,111,.10) 11px 22px)}
.verdict>*{position:relative;z-index:1}
.vlabel{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
.vword{font:600 26px/1 var(--sans);letter-spacing:.13em}
.vgloss{font-size:13.5px;color:var(--ink-dim);font-style:italic}
.vbig{font:600 clamp(34px,5.4vw,52px)/1.05 var(--mono);letter-spacing:-.02em;
  margin-top:10px;font-variant-numeric:tabular-nums}
.vexact{font-family:var(--mono);font-size:14px;color:var(--ink-dim);margin-top:7px;
  font-variant-numeric:tabular-nums}
.distrust{display:inline-flex;align-items:center;gap:8px;margin-top:15px;
  font:600 11px/1 var(--sans);letter-spacing:.16em;color:#160b0e;
  background:var(--alarm);padding:8px 12px;border-radius:5px}
.vside{display:flex;flex-direction:column;gap:18px;min-width:300px}
.readout{font-size:10.5px;letter-spacing:.14em;color:var(--ink-faint);margin-bottom:7px}
.normrow{display:flex;align-items:baseline;gap:10px}
.normval{font:600 20px/1 var(--mono);font-variant-numeric:tabular-nums}
.normof{font-size:11.5px;color:var(--ink-faint)}
.axis{position:relative;height:6px;background:var(--surface);border:1px solid var(--rule);
  border-radius:4px;margin:14px 0 6px}
.axis .zero{position:absolute;top:-4px;bottom:-4px;width:1px;background:var(--ink-faint)}
.axis .mark{position:absolute;top:50%;width:12px;height:12px;border-radius:50%;
  transform:translate(-50%,-50%);border:2px solid var(--surface-2)}
.axis .mark.clamped{border-radius:2px;transform:translate(-50%,-50%) rotate(45deg)}
.axisends{display:flex;justify-content:space-between;font-size:10.5px;
  color:var(--ink-faint);font-family:var(--mono)}
.trendhead{display:flex;align-items:baseline;justify-content:space-between;gap:12px}
.tdir{font-size:12px;color:var(--ink-dim)}
.spark{display:block;width:100%;height:60px;overflow:visible;margin-top:4px}
.tnote{font-size:11px;color:var(--caution);margin-top:6px}

/* ---- beam: two bars on one scale, where the overhang is the answer ----- */
.beam{padding:22px 28px 8px}
.beamrow{display:grid;grid-template-columns:8.5rem minmax(0,1fr) max-content;
  align-items:center;gap:16px;margin-bottom:9px}
.beamrow .k{font-size:12px;color:var(--ink-dim);letter-spacing:.03em}
.beamrow .v{font-family:var(--mono);font-size:13px;color:var(--ink-dim);text-align:right;
  font-variant-numeric:tabular-nums;white-space:nowrap}
.track{height:24px;background:var(--surface-2);border:1px solid var(--rule);
  border-radius:4px;overflow:hidden}
.fill{height:100%}
.fill.assets{background:linear-gradient(90deg,rgba(63,191,168,.5),var(--asset))}
.fill.debt{background:linear-gradient(90deg,rgba(224,160,60,.5),var(--debt))}
.gap .span{position:absolute;top:0;bottom:10px;border-left:1px dashed;border-right:1px dashed}
.gap .cap{position:absolute;top:12px;white-space:nowrap;font-family:var(--mono);
  font-size:11.5px;transform:translateX(-50%);padding:0 6px}
.gapwrap{position:relative;height:34px}

/* ---- ledger: one grid, so every figure lands in one right-aligned column */
.ledger{display:grid;grid-template-columns:minmax(9.5rem,1fr) minmax(48px,130px) max-content;
  column-gap:18px;padding:18px 28px 22px}
.group{grid-column:1/-1;font-size:10.5px;letter-spacing:.2em;color:var(--ink-faint);
  margin:14px 0 8px}
.group:first-child{margin-top:0}
.lbl{padding-top:9px;font-size:13.5px}
.bar{align-self:center;margin-top:7px;height:5px;background:rgba(255,255,255,.06);
  border-radius:3px}
.bar i{display:block;height:100%;border-radius:3px}
.bar.assets i{background:var(--asset)}
.bar.debt i{background:var(--debt)}
.amt{padding-top:9px;text-align:right;font-family:var(--mono);font-size:14px;
  font-variant-numeric:tabular-nums;white-space:nowrap}
.amt.absent{color:var(--ink-faint);font-style:italic;font-size:12.5px}
.amt.excluded{color:var(--caution);font-size:11.5px;letter-spacing:.08em}
/* the source row spans the grid, so a three-tag IFRS sum wraps into chips
   instead of running off the end of a line */
.src{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:6px;align-items:center;
  padding:8px 0 11px;border-bottom:1px solid var(--rule)}
.tag{font-family:var(--mono);font-size:10.5px;color:var(--ink-faint);background:var(--surface-2);
  border:1px solid var(--rule);border-radius:4px;padding:3px 7px;cursor:pointer;
  max-width:100%;text-align:left;line-height:1.35;
  /* break-all rather than anywhere: a tag has a hyphen after its namespace,
     and the browser would otherwise break there and orphan "ifrs-" on its
     own line instead of filling the row */
  word-break:break-all}
.tag:hover{color:var(--ink);border-color:var(--ink-faint)}
.tag.copied{color:var(--asset);border-color:var(--asset)}
.tag.copyfail{color:var(--alarm);border-color:var(--alarm)}
/* a derived figure must not look like a tag: different shape, different ink */
.note{font-size:10.5px;font-style:italic;color:var(--debt);background:var(--debt-soft);
  border:1px solid rgba(224,160,60,.3);border-radius:999px;padding:3px 10px;line-height:1.35}
.reason{font-size:11.5px;color:var(--caution);font-family:var(--mono);
  overflow-wrap:anywhere}
.totrow .lbl,.totrow .amt{padding-top:14px;font-weight:600}
.totrow .amt{font-size:15px}
.rule{grid-column:1/-1;height:1px;background:var(--rule);margin-top:12px}

/* ---- caveats: warm paper, deliberately not more rows ------------------- */
.caveats{margin:8px 28px 24px;background:var(--caveat-bg);color:var(--caveat-ink);
  border-left:3px solid var(--caveat-edge);border-radius:0 10px 10px 0;padding:18px 22px}
.caveats h3{font:400 15px/1.3 var(--serif);font-style:italic;color:var(--caveat-ink)}
.caveats .why{font-size:12px;color:var(--caveat-dim);margin-top:4px;font-style:italic;
  font-family:var(--serif)}
.cgrid{display:grid;grid-template-columns:minmax(9rem,1fr) max-content minmax(0,1.1fr);
  column-gap:16px;margin-top:14px}
/* swept tags are far longer than a caption, so this grid gives them the room
   rather than breaking them mid-word */
.cgrid.sweep{grid-template-columns:minmax(0,2.4fr) max-content max-content}
/* the chips inherit the panel's material, not the ledger's */
.caveats .tag{background:rgba(255,255,255,.05);border-color:rgba(255,255,255,.11);
  color:var(--caveat-dim)}
.caveats .tag:hover{color:var(--caveat-ink);border-color:var(--caveat-edge)}
.caveats .tag.copied{color:var(--asset);border-color:var(--asset)}
.cgrid .cl{padding-top:8px;font-size:13px;overflow-wrap:anywhere}
.cgrid .cv{padding-top:8px;text-align:right;font-family:var(--mono);font-size:13px;
  font-variant-numeric:tabular-nums;white-space:nowrap}
.cgrid .cs{padding-top:9px;font-size:10.5px;color:var(--caveat-dim);font-family:var(--mono);
  overflow-wrap:anywhere;padding-left:10px}
.meter{height:5px;background:rgba(255,255,255,.07);border-radius:3px;margin:12px 0 5px;
  max-width:280px}
.meter i{display:block;height:100%;border-radius:3px;background:var(--caveat-edge)}
.meter.warn i{background:var(--caution)}
.meter.bad i{background:var(--alarm)}
.doubt{display:flex;align-items:baseline;gap:10px;font-family:var(--mono);font-size:13px}
.doubt .lvl{font-family:var(--sans);font-size:11.5px;font-style:italic}
details{margin-top:13px}
summary{cursor:pointer;font-size:12px;color:var(--caveat-dim);font-style:italic;
  font-family:var(--serif)}
summary:hover{color:var(--caveat-ink)}
details p{font-size:12.5px;color:var(--caveat-dim);margin:10px 0 0;max-width:66ch;
  line-height:1.6}
.alarmstrip{margin:8px 28px 24px;background:var(--alarm-soft);
  border-left:3px solid var(--alarm);border-radius:0 10px 10px 0;padding:14px 20px;
  color:var(--alarm);font-size:13px}

/* ---- footer ----------------------------------------------------------- */
.legend{border-top:1px solid var(--rule);padding-top:18px;color:var(--ink-faint);
  font-size:12px}
.legend div{margin-bottom:7px}
.legend .g{font-family:var(--mono);color:var(--ink-dim);display:inline-block;width:1.5em}

@media (max-width:820px){
  body{padding:22px 12px 44px}
  .head,.verdict,.beam,.ledger{padding-left:18px;padding-right:18px}
  .caveats,.alarmstrip{margin-left:18px;margin-right:18px}
  .verdict{grid-template-columns:1fr;gap:22px}
  .vside{min-width:0}
  .beamrow{grid-template-columns:6.5rem minmax(0,1fr);row-gap:4px}
  .beamrow .v{grid-column:2;text-align:left}
  .ledger{grid-template-columns:minmax(0,1fr) max-content;column-gap:12px}
  .bar,.spacer{display:none}
  .cgrid{grid-template-columns:minmax(0,1fr) max-content}
  .cgrid .cs{grid-column:1/-1;padding-left:0;padding-bottom:7px}
}
@media print{
  /* keep the ground dark on paper: the type is light, so dropping the
     background would print white on white */
  body{-webkit-print-color-adjust:exact;print-color-adjust:exact;padding:0}
  .card{break-inside:avoid}
}
"""

# Clicking a tag copies it, because the only reason a tag is on the page at all
# is that a wrong figure has to be chased back into EDGAR.
SCRIPT = """
document.addEventListener('click', function (event) {
  var el = event.target.closest('[data-copy]');
  if (!el) { return; }
  var text = el.getAttribute('data-copy');

  var mark = function (ok) {
    el.classList.add(ok ? 'copied' : 'copyfail');
    setTimeout(function () { el.classList.remove('copied', 'copyfail'); }, 900);
  };

  // execCommand is deprecated but is the only path that works when the async
  // clipboard is refused - a file:// page, or a browser that wants a gesture
  // it did not see.
  var viaTextarea = function () {
    var pad = document.createElement('textarea');
    pad.value = text;
    pad.setAttribute('readonly', '');
    pad.style.position = 'fixed';
    pad.style.opacity = '0';
    document.body.appendChild(pad);
    pad.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
    document.body.removeChild(pad);
    mark(ok);
  };

  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(function () { mark(true); }, viaTextarea);
  } else {
    viaTextarea();
  }
});
"""


# ==========================================================================
# Card sections
# ==========================================================================


def _meta(result: dict) -> str:
    """As-of date, taxonomy, currency, filed date, age - plus the flags, each
    carrying its meaning rather than deferring to the legend."""
    age = result["age_days"]
    age_class = "bad" if age > AGE_ALARM else ("warn" if age > AGE_WARN else "")
    chips = [
        f'<span class="chip">as of <b>{esc(result["as_of"])}</b></span>',
        f'<span class="chip">{esc(result["namespace"])}</span>',
        f'<span class="chip{" good" if result["unit"] != "USD" else ""}">'
        f'<b>{esc(result["unit"])}</b></span>',
    ]
    if result["filed"]:
        chips.append(f'<span class="chip">filed <b>{esc(result["filed"])}</b></span>')
    chips.append(f'<span class="chip {age_class}">{age:,} days old</span>')

    for glyph in flags(result):
        tone = "bad" if glyph == "!" else "warn"
        chips.append(f'<span class="chip {tone} flagchip"><span class="g">{esc(glyph)}</span>'
                     f'{esc(FLAG_MEANINGS[glyph])}</span>')
    if result["gaps"]:
        chips.append(f'<span class="chip warn flagchip">no tag matched: '
                     f'<b>{esc(result["gaps"])}</b></span>')
    if result["unit"] != "USD":
        chips.append(f'<span class="chip good flagchip">figures as filed in '
                     f'{esc(result["unit"])} - not converted</span>')
    return f'<div class="meta">{"".join(chips)}</div>'


def _sparkline(result: dict, accent: str) -> str:
    """Four normalized readings are a direction before they are four numbers."""
    window = result["trend"][-TREND_DISPLAY:]
    if len(window) < 2:
        return '<div class="tdir">not enough history</div>'

    low, high = min(window), max(window)
    if low == high:  # a flat line still needs a box to sit in
        low, high = low - 0.01, high + 0.01
    pad = (high - low) * 0.18
    low, high = low - pad, high + pad

    width, height, inset = 300.0, 60.0, 9.0
    step = (width - 2 * inset) / (len(window) - 1)

    def y_of(value: float) -> float:
        return height - inset - (value - low) / (high - low) * (height - 2 * inset)

    points = [(inset + i * step, y_of(v)) for i, v in enumerate(window)]
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    dashed = ' stroke-dasharray="5 4"' if not result["trend_consistent"] else ""

    parts = [f'<svg class="spark" viewBox="0 0 {width:.0f} {height:.0f}" '
             f'preserveAspectRatio="none" aria-hidden="true">']
    if low <= 0 <= high:
        zero = y_of(0.0)
        parts.append(f'<line x1="0" y1="{zero:.1f}" x2="{width:.0f}" y2="{zero:.1f}" '
                     f'stroke="var(--ink-faint)" stroke-width="1" stroke-dasharray="2 3"/>')
    parts.append(f'<polyline points="{path}" fill="none" stroke="{accent}" '
                 f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"{dashed}/>')
    for index, (x, y) in enumerate(points):
        last = index == len(points) - 1
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{3.5 if last else 2.5:.1f}" '
                     f'fill="{accent if last else "var(--surface-2)"}" '
                     f'stroke="{accent}" stroke-width="1.5"/>')
    parts.append("</svg>")

    change = window[-1] - window[0]
    if abs(change) < TREND_FLAT:
        direction = "holding steady"
    else:
        direction = "improving" if change > 0 else "deteriorating"
    head = (f'<div class="trendhead"><div class="readout">TREND - LAST '
            f'{len(window)} BALANCE SHEETS</div>'
            f'<div class="tdir">{direction} <span class="mono">{change:+.3f}</span></div></div>')
    tail = ""
    if not result["trend_consistent"]:
        tail = ('<div class="tnote">dashed: a component changed tag mid-history, so part '
                'of this move is a tagging artefact</div>')
    ends = (f'<div class="axisends"><span>{window[0]:+.3f}</span>'
            f'<span>{window[-1]:+.3f}</span></div>')
    return head + "".join(parts) + ends + tail


def _verdict(result: dict) -> str:
    """The answer, readable before anything else on the card."""
    unit, core, entry = result["unit"], result["core"], band(result)
    accent = BAND_ACCENT[entry["key"]]
    classes = f'verdict {entry["key"]}' + (" distrusted" if result["doubtful"] else "")

    left = [f'<div class="vlabel"><span class="vword" style="color:{accent}">'
            f'{entry["verdict"]}</span>'
            f'<span class="vgloss">{esc(entry["gloss"])}</span></div>',
            f'<div class="vbig" style="color:{accent}">{esc(compact(core, unit))}</div>',
            f'<div class="vexact">{esc(money(core, unit))}</div>']
    if result["doubtful"]:
        left.append('<div class="distrust">! DISTRUST THIS READING - unclassified '
                    f'balances outweigh the signal</div>')

    low, high = NORM_SCALE
    norm = result["norm"]
    if norm is None:
        side = ('<div><div class="readout">NORMALIZED - RESULT / TOTAL ASSETS</div>'
                '<div class="normrow"><span class="normval">n/a</span>'
                '<span class="normof">no total assets reported</span></div></div>')
    else:
        clamped = max(low, min(high, norm))
        position = (clamped - low) / (high - low) * 100.0
        zero = (0.0 - low) / (high - low) * 100.0
        off = " clamped" if clamped != norm else ""
        total = money(result["total_assets"], unit) if result["total_assets"] else "n/a"
        side = (
            '<div><div class="readout">NORMALIZED - RESULT / TOTAL ASSETS</div>'
            f'<div class="normrow"><span class="normval" style="color:{accent}">'
            f'{esc(ratio(norm))}</span>'
            f'<span class="normof">of {esc(total)} total assets</span></div>'
            f'<div class="axis"><span class="zero" style="left:{zero:.1f}%"></span>'
            f'<span class="mark{off}" style="left:{position:.1f}%;background:{accent}"></span>'
            '</div>'
            f'<div class="axisends"><span>{low:+.2f}</span><span>0</span>'
            f'<span>{high:+.2f}</span></div></div>'
        )

    return (f'<div class="{classes}"><div>{"".join(left)}</div>'
            f'<div class="vside">{side}<div>{_sparkline(result, accent)}</div></div></div>')


def _beam(result: dict) -> str:
    """Net Assets and Total Debt on one scale from one origin. The overhang is
    the result - the comparison and its resolution in a single picture."""
    unit, assets, debt = result["unit"], result["net_assets"], result["total_debt"]
    entry = band(result)
    accent = BAND_ACCENT[entry["key"]]
    scale = max(abs(assets), abs(debt))
    a_width, d_width = share(assets, scale), share(debt, scale)
    start, span = min(a_width, d_width), abs(a_width - d_width)
    centre = start + span / 2

    return (
        '<div class="beam">'
        '<div class="beamrow"><span class="k">Net Assets</span>'
        f'<div class="track"><div class="fill assets" style="width:{a_width:.2f}%"></div></div>'
        f'<span class="v">{esc(money(assets, unit))}</span></div>'
        '<div class="beamrow"><span class="k">Total Debt</span>'
        f'<div class="track"><div class="fill debt" style="width:{d_width:.2f}%"></div></div>'
        f'<span class="v">{esc(money(debt, unit))}</span></div>'
        '<div class="beamrow"><span class="k"></span>'
        f'<div class="gapwrap gap"><div class="span" style="left:{start:.2f}%;'
        f'width:{span:.2f}%;border-color:{accent}"></div>'
        f'<div class="cap" style="left:{centre:.2f}%;color:{accent}">'
        f'{entry["verdict"]} {esc(compact(result["core"], unit))}</div></div>'
        '<span class="v"></span></div>'
        '</div>'
    )


def _tag_chip(namespace: str, tag: str) -> str:
    """One XBRL tag, visible on the page and copyable in a click - the page has
    to answer "which tag produced this" without a hover and without EDGAR."""
    full = f"{namespace}:{tag}"
    return (f'<button type="button" class="tag" data-copy="{esc(full)}" '
            f'aria-label="{esc(full)} - click to copy" title="click to copy">'
            f"{esc(full)}</button>")


def _source(namespace: str, line, unit: str) -> str:
    """Tag chips first, then any derived-figure note - two visibly different
    shapes, so provenance is never mistaken for derivation."""
    parts = []
    if line is not None and line.tags:
        for tag in line.tags:
            parts.append(_tag_chip(namespace, tag))
        for note in line.notes:
            parts.append(f'<span class="note">{esc(note)}</span>')
    elif line is not None and line.stale:
        when, was = line.stale
        parts.append(f'<span class="reason">as stale - last reported {esc(when)} '
                     f'({esc(money(was, unit))})</span>')
    return "".join(parts)


def _line_row(result: dict, name: str, kind: str, scale: float) -> str:
    """One formula component: label, share bar, figure, sources."""
    line, unit = result["lines"].get(name), result["unit"]
    if line is None:
        amount = '<div class="amt absent">not reported</div>'
        bar = '<div class="spacer"></div>'
    elif not line.tags:
        amount = '<div class="amt excluded">EXCLUDED</div>'
        bar = '<div class="spacer"></div>'
    else:
        amount = f'<div class="amt">{esc(money(line.value, unit))}</div>'
        bar = (f'<div class="bar {kind}"><i style="width:'
               f'{share(line.value, scale):.2f}%"></i></div>')
    return (f'<div class="lbl">{esc(LABELS[name])}</div>{bar}{amount}'
            f'<div class="src">{_source(result["namespace"], line, unit)}</div>')


def _ledger(result: dict) -> str:
    """The five components, grouped into the two sides they belong to."""
    unit = result["unit"]
    out = ['<div class="ledger">', '<div class="group">HELD - CASH AND INVESTMENTS</div>']
    for name in ASSET_FIELDS:
        out.append(_line_row(result, name, "assets", result["net_assets"]))
    out.append('<div class="rule"></div>')
    out.append(f'<div class="lbl tot totrow">Net Assets</div><div class="spacer"></div>'
               f'<div class="amt tot totrow" style="color:var(--asset)">'
               f'{esc(money(result["net_assets"], unit))}</div>')
    out.append('<div class="group">OWED - INTEREST-BEARING BORROWINGS ONLY</div>')
    for name in DEBT_FIELDS:
        out.append(_line_row(result, name, "debt", result["total_debt"]))
    out.append('<div class="rule"></div>')
    out.append(f'<div class="lbl tot totrow">Total Debt</div><div class="spacer"></div>'
               f'<div class="amt tot totrow" style="color:var(--debt)">'
               f'{esc(money(result["total_debt"], unit))}</div>')
    out.append("</div>")
    return "".join(out)


def _excluded(result: dict) -> str:
    """Liabilities that are real money and deliberately outside the debt total.
    Keeping this distinction visible is the reason the total is trustworthy, so
    it gets its own material rather than two more rows."""
    unit, namespace, lines = result["unit"], result["namespace"], result["lines"]
    present = [(name, label) for name, label in EXCLUDED_FIELDS
               if lines.get(name) and lines[name].tags]
    if not present:
        return ""
    rows = []
    for name, label in present:
        line = lines[name]
        sources = "".join(_tag_chip(namespace, tag) for tag in line.tags)
        rows.append(f'<div class="cl">{esc(label)}</div>'
                    f'<div class="cv">{esc(money(line.value, unit))}</div>'
                    f'<div class="cs">{sources}</div>')
    return (
        '<section class="caveats"><h3>Outside the debt total - by design</h3>'
        '<div class="why">Operating leases, payables, accruals and deferred tax are not '
        'interest-bearing borrowings. These are printed because they are large, not '
        'because they belong in the formula - no figure below is in Total Debt.</div>'
        f'<div class="cgrid">{"".join(rows)}</div></section>'
    )


def _sweep(result: dict) -> str:
    """The unclassified balances and what they do to confidence."""
    if not (result["asset_hits"] or result["liability_hits"]):
        return ""
    unit, doubt = result["unit"], result["doubt"]
    if result["doubtful"]:
        level, tone = "outweighs the signal - distrust this reading", "bad"
    elif doubt >= DOUBT_ALARM:
        level, tone = "large against total assets", "bad"
    elif doubt >= DOUBT_NOTICE:
        level, tone = "worth a look", "warn"
    else:
        level, tone = "immaterial", ""

    rows = []
    for side, hits in (("asset", result["asset_hits"]),
                       ("liability", result["liability_hits"])):
        for tag, value in hits:
            rows.append(
                f'<div class="cl">{_tag_chip(result["namespace"], tag)}</div>'
                f'<div class="cv">{esc(money(value, unit))}</div>'
                f'<div class="cs">[{side}]</div>'
            )
    fill = min(100.0, doubt / DOUBT_METER_FULL * 100.0) if DOUBT_METER_FULL else 0.0
    return (
        '<section class="caveats"><h3>Unclassified balances at this date</h3>'
        '<div class="why">Matched on vocabulary rather than on an exact tag name, and '
        'included in no figure above.</div>'
        f'<div class="doubt" style="margin-top:14px"><span>doubt {doubt:.1%}</span>'
        f'<span class="lvl">{esc(level)}</span></div>'
        f'<div class="meter {tone}"><i style="width:{fill:.1f}%"></i></div>'
        f'<div class="cgrid sweep">{"".join(rows)}</div>'
        '<details><summary>why this cannot be settled from companyfacts</summary>'
        '<p>companyfacts carries no roll-up hierarchy, so each balance above is either '
        'already inside a total on this page or a genuine miss by the tag ladders - only '
        'the filing itself settles which. Doubt is the largest unclassified balance on '
        'each side over total assets: roughly how far the normalized result could move '
        'if these belong in the formula.</p></details></section>'
    )


def _overlap(result: dict) -> str:
    if not result["overlap"]:
        return ""
    names = ", ".join(esc(name) for name in result["overlap"])
    return (f'<div class="alarmstrip"><b>Double counting.</b> {names} exceeds its own '
            f'balance sheet subtotal, so the same borrowing is being counted twice.</div>')


def card(result: dict) -> str:
    """One company, self-contained. A page is one or many of these."""
    glyphs = flags(result)
    return (
        f'<article class="card" id="c-{esc(result["ticker"])}">'
        f'<div class="head"><div class="title">'
        f'<span class="ticker">{esc(result["ticker"])}{esc(glyphs)}</span>'
        f'<span class="company">{esc(result["name"].upper())}</span></div>'
        f'{_meta(result)}</div>'
        f'{_verdict(result)}{_beam(result)}{_ledger(result)}'
        f'{_overlap(result)}{_excluded(result)}{_sweep(result)}'
        '</article>'
    )


# ==========================================================================
# Page
# ==========================================================================


def _rail(results: list[dict]) -> str:
    """Index across several companies. Absent on a one-company page, and the
    seam a ranked list grows from."""
    if len(results) < 2:
        return ""
    links = []
    for result in sorted(results, key=lambda r: -r["core"]):
        accent = BAND_ACCENT[band(result)["key"]]
        value = ratio(result["norm"]) if result["norm"] is not None else "n/a"
        links.append(
            f'<a href="#c-{esc(result["ticker"])}">'
            f'<span class="dot" style="background:{accent}"></span>'
            f'<b>{esc(result["ticker"])}{esc(flags(result))}</b>'
            f'<span class="val" style="color:{accent}">{esc(value)}</span></a>'
        )
    return f'<nav class="rail">{"".join(links)}</nav>'


def _legend(results: list[dict]) -> str:
    rows = [f'<div><span class="g">{esc(glyph)}</span>{esc(meaning)}</div>'
            for glyph, meaning in FLAG_MEANINGS.items()]
    if len({r["unit"] for r in results}) > 1:
        rows.append('<div><span class="g"></span>figures are in each filer\'s own '
                    'currency and are not converted; only the normalized result '
                    'compares across them</div>')
    return f'<div class="legend">{"".join(rows)}</div>'


def _config_json() -> str:
    """The tuning constants, carried in the page so a saved file explains the
    colours it was drawn with."""
    bands = [{**entry, "floor": None if entry["floor"] == float("-inf") else entry["floor"]}
             for entry in BANDS]
    return json.dumps({
        "bands": bands,
        "doubt_notice": DOUBT_NOTICE,
        "doubt_alarm": DOUBT_ALARM,
        "doubt_meter_full": DOUBT_METER_FULL,
        "age_warn": AGE_WARN,
        "age_alarm": AGE_ALARM,
        "norm_scale": list(NORM_SCALE),
        "trend_flat": TREND_FLAT,
        "palette": PALETTE,
    }, indent=2)


def page(results: list[dict], title: str | None = None) -> str:
    """A complete document. Takes a list at every size: one company is a
    one-card page, and a ranked list later is the same call with more cards."""
    if title is None:
        title = (results[0]["ticker"] if len(results) == 1
                 else f"{len(results)} companies")
    variables = "".join(f"--{key}:{value};" for key, value in PALETTE.items())
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    cards = "".join(card(result) for result in results)
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"<title>BSCF - {esc(title)}</title>\n"
        f"<style>:root{{{variables}}}{STYLE}</style>\n</head>\n<body>\n"
        '<div class="wrap">'
        '<div class="masthead"><div><div class="mark">B S C F</div>'
        '<div class="formula">net assets (cash + short and long-term investments) '
        '- interest-bearing debt</div></div>'
        f'<div class="sub">{esc(title)} &middot; generated {esc(stamp)}</div></div>'
        f"{_rail(results)}{cards}{_legend(results)}"
        "</div>\n"
        f'<script type="application/json" id="bscf-config">{_config_json()}</script>\n'
        f"<script>{SCRIPT}</script>\n</body>\n</html>\n"
    )


def suggest_path(results: list[dict], directory: str) -> str:
    """Default filename for a run: the tickers, then a timestamp."""
    tickers = [result["ticker"] for result in results]
    stem = "-".join(tickers[:3]) + (f"+{len(tickers) - 3}" if len(tickers) > 3 else "")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return os.path.join(directory, f"bscf_{stem}_{stamp}.html")


def write(results: list[dict], path: str) -> str:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(page(results))
    return path
