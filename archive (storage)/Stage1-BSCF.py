"""Stage 1 fundamentals cluster: BSCF and debt-coverage signals for an earnings day.

Usage:
    python Stage1-BSCF.py 2026-09-15
    python Stage1-BSCF.py 2026-09-15 --tickers AAPL,MSFT,NVDA
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

import pandas as pd
import requests

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

# SEC requires a real contact address in the User-Agent or it returns 403.
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "")

SEC_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SEC_REQUEST_INTERVAL = 0.12  # seconds between SEC calls; their limit is 10/sec
SEC_TIMEOUT = 30
SEC_MAX_RETRIES = 3

NASDAQ_CALENDAR_URL = "https://api.nasdaq.com/api/calendar/earnings"
NASDAQ_TIMEOUT = 20
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

TREND_QUARTERS = 8  # how many quarters of BSCF history to compute
TREND_DISPLAY_QUARTERS = 4  # how many of those to show in the table

# Day-count windows used to classify XBRL duration facts. The upper bound must
# clear 112 days: retailers on a 4-4-5 calendar open the year with a 16-week
# quarter, and a tighter bound drops it, leaving a hole that costs them a TTM.
QUARTER_MIN_DAYS, QUARTER_MAX_DAYS = 80, 120
ANNUAL_MIN_DAYS, ANNUAL_MAX_DAYS = 330, 400
TTM_MIN_DAYS, TTM_MAX_DAYS = 350, 380

OUTPUT_SCALE = 1_000_000  # dollar columns are printed in millions
CSV_DIR = os.path.dirname(os.path.abspath(__file__))

# Company facts run to megabytes each, so a 300-name day is gigabytes of repeat
# download when tuning thresholds. Staleness is near harmless here: a company
# reporting today has not filed the new 10-Q yet, so the figures being screened
# are last quarter's either way. --refresh forces a re-fetch.
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sec_cache")
CACHE_TTL_HOURS = 24

# Microcaps produce untradeable rows and wild ratios (a near-zero interest
# expense gives interest coverage in the hundreds), so they are dropped before
# any SEC call. Names passed explicitly with --tickers bypass this.
MIN_MARKET_CAP = 300_000_000

# XBRL tag candidates, highest priority first. Companies tag the same economic
# concept differently, so each figure gets a fallback chain.
INSTANT_TAGS = {
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "Cash",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsIncludingDisposalGroupAndDiscontinuedOperations",
        "CashAndCashEquivalentsIncludingDiscontinuedOperations",
        "CashAndDueFromBanks",
    ],
    "short_term_investments": [
        "ShortTermInvestments",
        "MarketableSecuritiesCurrent",
        "DebtSecuritiesCurrent",
        "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
        "AvailableForSaleSecuritiesCurrent",
        "OtherShortTermInvestments",
    ],
    "long_term_investments": [
        "LongTermInvestments",
        "MarketableSecuritiesNoncurrent",
        "DebtSecuritiesNoncurrent",
        "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent",
        "AvailableForSaleSecuritiesNoncurrent",
        "OtherLongTermInvestments",
    ],
    "convertible_current": [
        "ConvertibleNotesPayableCurrent",
        "ConvertibleDebtCurrent",
    ],
    "convertible_noncurrent": [
        "ConvertibleNotesPayableNoncurrent",
        "ConvertibleDebtNoncurrent",
        "ConvertibleLongTermNotesPayable",
    ],
    "other_lt_liabilities": [
        "OtherLiabilitiesNoncurrent",
        "OtherLongTermLiabilities",
    ],
    "total_assets": [
        "Assets",
    ],
    "current_lt_debt": [
        "LongTermDebtCurrent",
        "LongTermDebtAndCapitalLeaseObligationsCurrent",
        "DebtCurrent",
    ],
}

DURATION_TAGS = {
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "operating_income": [
        "OperatingIncomeLoss",
    ],
    "interest_expense": [
        "InterestExpense",
        "InterestExpenseNonoperating",
        "InterestExpenseDebt",
        "InterestAndDebtExpense",
    ],
}

# BSCF components that are legitimately absent for many companies (no convertible
# notes, no separate investments line) and so are read as zero when missing. The
# short code is what the table's `gaps` column prints when one is absent: a zero
# can mean "genuinely none" or "we failed to find their tag", and only the filing
# itself settles which, so the run reports rather than hides it.
BSCF_COMPONENT_CODES = {
    "short_term_investments": "sti",
    "long_term_investments": "lti",
    "convertible_current": "cvt_cur",
    "convertible_noncurrent": "cvt_nc",
    "other_lt_liabilities": "other_ltl",
}

# Every tag any named chain knows about. The sweep below skips these so it only
# ever reports balances the named chains did not already consider.
NAMED_INSTANT_TAGS = {tag for chain in INSTANT_TAGS.values() for tag in chain}

# The named chains match exact tag names, so a filer using anything outside the
# list reads as zero and the money vanishes silently. The sweep is the backstop:
# it matches on vocabulary instead of exact names, against whatever tags that
# filer actually used, so an unrecognized tag becomes a quantified unknown. It
# needs no per-ticker maintenance as the universe grows.
SWEEP_ASSET_PATTERNS = ("securities", "investment")

# Deliberately narrower than "any debt": BSCF's liability half is convertible
# instruments and other long-term liabilities, so plain long-term debt is out of
# scope by design rather than missing, and sweeping it would flag every
# leveraged company.
SWEEP_LIABILITY_PATTERNS = ("convertible", "otherliabilit", "otherlongterm")

# Disclosure-note artifacts that restate a balance counted elsewhere: maturity
# ladders, unrealized gain/loss tables, cost-basis alternates. Including these
# would multiply the same pool several times over.
SWEEP_EXCLUDE_PATTERNS = (
    "maturit",
    "unrealized",
    "realized",
    "impairment",
    "pledged",
    "amortizedcost",
    "fairvaluedisclosure",
    "restricted",
    "continuous",
    "accumulated",
    "allowance",
    "creditloss",
    "weightedaverage",
    # Current operating accruals: BSCF's liability half is long-term only, so
    # these are out of scope rather than missed.
    "liabilitiescurrent",
    # Combined captions that restate cash and investments already counted.
    "cashcashequivalentsand",
    # For a REIT or a BDC "investment" is the operating business - buildings and
    # loan books - not a liquid balance. Without these a property trust flags its
    # whole balance sheet and buries the real findings.
    "realestateinvestment",
    "investmentproperty",
    "investmentbuilding",
    "investmentowned",
    "netinvestmentinlease",
    "financialinstrumentsowned",
    "taxbasisofinvestments",
    "derivative",
    # Repo funding is a liability despite the "securities" in its name, so it
    # would otherwise be added to the asset side.
    "soldunderagreements",
    # Undrawn future obligations, not a balance on the sheet.
    "commitment",
)

# The sweep answers "a gap was flagged - how much might be sitting in it", so it
# only runs for a side that actually has a gap. Without this it fires on
# companies whose components all resolved, where any hit is an overlapping
# restatement rather than a miss.
ASSET_GAP_FIELDS = {"short_term_investments", "long_term_investments"}
LIABILITY_GAP_FIELDS = {
    "convertible_current",
    "convertible_noncurrent",
    "other_lt_liabilities",
}

SWEEP_DETAIL_TAGS = 3  # source tags recorded per side in the CSV

# Data-quality gate. `doubt` is the unspecified pool over total assets - roughly
# how far bscf_norm could move if the sweep's findings belong in the formula.
# Judged against the signal rather than as a flat percentage: 5% doubt is noise
# against a -0.30 reading and fatal against a +0.001 one, and on a 236-name day
# 29% of rows carry more doubt than signal. The absolute cap catches the cases
# where a strong-looking ratio rests on a mostly unclassified balance sheet.
DOUBT_VS_SIGNAL_MAX = 1.0
DOUBT_ABSOLUTE_MAX = 0.25

# Marks a skip as "outside coverage by design" rather than a data problem, so a
# run over 200 names separates the two in its summary.
OUT_OF_SCOPE = "not covered: "
DOUBTFUL = "doubtful data: "

# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

_last_sec_call = 0.0


def sec_get(url: str) -> dict | None:
    """GET a SEC endpoint, rate-limited and retried. None on a clean 404."""
    global _last_sec_call
    headers = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    for attempt in range(SEC_MAX_RETRIES):
        wait = SEC_REQUEST_INTERVAL - (time.monotonic() - _last_sec_call)
        if wait > 0:
            time.sleep(wait)
        _last_sec_call = time.monotonic()
        response = requests.get(url, headers=headers, timeout=SEC_TIMEOUT)
        if response.status_code == 404:
            return None
        if response.status_code == 200:
            return response.json()
        if attempt == SEC_MAX_RETRIES - 1:
            response.raise_for_status()
        time.sleep(2**attempt)
    return None


def cached_sec_get(url: str, refresh: bool = False) -> dict | None:
    """sec_get with an on-disk cache, so re-runs cost no network."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, hashlib.sha1(url.encode()).hexdigest()[:16] + ".json")

    if not refresh and os.path.exists(path):
        if time.time() - os.path.getmtime(path) < CACHE_TTL_HOURS * 3600:
            try:
                with open(path, encoding="utf-8") as handle:
                    return json.load(handle)
            except (OSError, ValueError):
                pass  # unreadable or half-written; fall through and re-fetch

    payload = sec_get(url)
    if payload is not None:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
    return payload


def parse_market_cap(raw: object) -> float | None:
    """Nasdaq reports market cap as a display string like '$1,234,567,890'."""
    digits = re.sub(r"[^0-9.]", "", str(raw or ""))
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


def fetch_earnings_calendar(day: date) -> list[tuple[str, float | None]]:
    headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.nasdaq.com",
        "Referer": "https://www.nasdaq.com/",
    }
    response = requests.get(
        NASDAQ_CALENDAR_URL,
        params={"date": day.isoformat()},
        headers=headers,
        timeout=NASDAQ_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") or {}
    rows = data.get("rows") or []
    return [
        (row["symbol"].strip().upper(), parse_market_cap(row.get("marketCap")))
        for row in rows
        if row.get("symbol")
    ]


def fetch_ticker_cik_map(refresh: bool = False) -> dict[str, int]:
    payload = cached_sec_get(SEC_TICKER_MAP_URL, refresh) or {}
    mapping = {}
    for entry in payload.values():
        mapping[entry["ticker"].strip().upper()] = int(entry["cik_str"])
    return mapping


def lookup_cik(ticker: str, cik_map: dict[str, int]) -> int | None:
    """Resolve a ticker to a CIK, reconciling share-class separators."""
    for candidate in (ticker, ticker.replace(".", "-"), ticker.replace("/", "-")):
        if candidate in cik_map:
            return cik_map[candidate]
    return None


# --------------------------------------------------------------------------
# XBRL fact extraction
# --------------------------------------------------------------------------


def _parse(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def collect_usd_facts(company_facts: dict, tag: str) -> list[dict]:
    """All USD observations for a tag, across every taxonomy that defines it."""
    out = []
    for taxonomy in company_facts.get("facts", {}).values():
        entry = taxonomy.get(tag)
        if not entry:
            continue
        for observation in entry.get("units", {}).get("USD", []):
            if observation.get("val") is None or not observation.get("filed"):
                continue
            out.append(
                {
                    "start": _parse(observation["start"]) if observation.get("start") else None,
                    "end": _parse(observation["end"]),
                    "val": float(observation["val"]),
                    "filed": observation["filed"],
                }
            )
    return out


def build_instant_index(company_facts: dict) -> dict[str, dict[date, float]]:
    """tag -> {balance sheet date: value}, keeping the most recently filed value."""
    index: dict[str, dict[date, float]] = {}
    for tags in INSTANT_TAGS.values():
        for tag in tags:
            if tag in index:
                continue
            best: dict[date, dict] = {}
            for fact in collect_usd_facts(company_facts, tag):
                if fact["start"] is not None:
                    continue
                current = best.get(fact["end"])
                if current is None or fact["filed"] > current["filed"]:
                    best[fact["end"]] = fact
            if best:
                index[tag] = {d: f["val"] for d, f in best.items()}
    return index


def resolve_instant(
    index: dict[str, dict[date, float]], field: str, as_of: date
) -> float | None:
    for tag in INSTANT_TAGS[field]:
        value = index.get(tag, {}).get(as_of)
        if value is not None:
            return value
    return None


def sweep_unclassified(
    company_facts: dict,
    as_of: date,
    include_patterns: tuple[str, ...],
    counted: set[float],
) -> list[tuple[str, float]]:
    """Balances in a category that no named tag chain captured, largest first.

    `counted` holds amounts the named chains already used, which filters out the
    alternate presentations of the same balance that most filers publish.
    """
    candidates = []
    for taxonomy in company_facts.get("facts", {}).values():
        for tag, entry in taxonomy.items():
            if tag in NAMED_INSTANT_TAGS:
                continue
            lowered = tag.lower()
            if not any(p in lowered for p in include_patterns):
                continue
            if any(p in lowered for p in SWEEP_EXCLUDE_PATTERNS):
                continue
            for observation in entry.get("units", {}).get("USD", []):
                if (
                    observation.get("start") is None
                    and observation.get("end") == as_of.isoformat()
                    and observation.get("val") is not None
                ):
                    value = float(observation["val"])
                    if value > 0 and value not in counted:
                        candidates.append((tag, value))
                    break

    # Filers often publish one balance under several tag spellings; keep the
    # first spelling of each distinct amount so they are not reported twice.
    seen: set[float] = set()
    unique = []
    for tag, value in sorted(candidates, key=lambda c: -c[1]):
        if value in seen:
            continue
        seen.add(value)
        unique.append((tag, value))
    return unique


def dedupe_durations(facts: list[dict]) -> list[dict]:
    best: dict[tuple, dict] = {}
    for fact in facts:
        if fact["start"] is None:
            continue
        key = (fact["start"], fact["end"])
        current = best.get(key)
        if current is None or fact["filed"] > current["filed"]:
            best[key] = fact
    return sorted(best.values(), key=lambda f: (f["end"], f["start"]))


def derive_quarterly(facts: list[dict]) -> list[dict]:
    """Turn SEC's year-to-date duration facts into discrete quarters.

    10-Q income and cash flow figures are cumulative from the fiscal year start,
    so a quarter is the YTD value minus the previous YTD value with the same start.
    """
    facts = dedupe_durations(facts)
    by_start = defaultdict(list)
    for fact in facts:
        by_start[fact["start"]].append(fact)

    quarters: dict[date, dict] = {}
    for fact in facts:
        span = (fact["end"] - fact["start"]).days
        if QUARTER_MIN_DAYS <= span <= QUARTER_MAX_DAYS:
            quarters[fact["end"]] = dict(fact)

    for fact in facts:
        if (fact["end"] - fact["start"]).days <= QUARTER_MAX_DAYS or fact["end"] in quarters:
            continue
        earlier = [f for f in by_start[fact["start"]] if f["end"] < fact["end"]]
        if not earlier:
            continue
        prior = max(earlier, key=lambda f: f["end"])
        start = prior["end"] + timedelta(days=1)
        if QUARTER_MIN_DAYS <= (fact["end"] - start).days <= QUARTER_MAX_DAYS:
            quarters[fact["end"]] = {
                "start": start,
                "end": fact["end"],
                "val": fact["val"] - prior["val"],
                "filed": fact["filed"],
            }

    return sorted(quarters.values(), key=lambda q: q["end"])


def trailing_twelve_months(company_facts: dict, field: str) -> tuple[float | None, str]:
    """TTM value for a flow figure, falling back to the latest annual filing."""
    facts: list[dict] = []
    for tag in DURATION_TAGS[field]:
        facts = collect_usd_facts(company_facts, tag)
        if facts:
            break
    if not facts:
        return None, "missing"

    quarters = derive_quarterly(facts)
    if len(quarters) >= 4:
        window = quarters[-4:]
        span = (window[-1]["end"] - window[0]["start"]).days
        if TTM_MIN_DAYS <= span <= TTM_MAX_DAYS:
            return sum(q["val"] for q in window), "ttm"

    annual = [
        f
        for f in dedupe_durations(facts)
        if ANNUAL_MIN_DAYS <= (f["end"] - f["start"]).days <= ANNUAL_MAX_DAYS
    ]
    if annual:
        return annual[-1]["val"], "annual"
    return None, "missing"


# --------------------------------------------------------------------------
# Signals
# --------------------------------------------------------------------------


def bscf_at(index: dict[str, dict[date, float]], as_of: date) -> dict | None:
    """The three BSCF components as of one balance sheet date."""
    assets = resolve_instant(index, "total_assets", as_of)
    cash = resolve_instant(index, "cash", as_of)
    if assets is None or not assets or cash is None:
        return None

    resolved: set[str] = set()
    counted: set[float] = {cash, assets}

    def optional(field: str) -> float:
        value = resolve_instant(index, field, as_of)
        if value is None:
            return 0.0
        resolved.add(field)
        counted.add(value)
        return value

    liquid = cash + optional("short_term_investments") + optional("long_term_investments")
    liabilities = (
        optional("convertible_current")
        + optional("convertible_noncurrent")
        + optional("other_lt_liabilities")
    )
    # A combined caption ("investments and cash") restates a total rather than a
    # component, so the totals have to be excluded from the sweep as well - the
    # per-component values alone would not match it.
    counted.update({liquid, liabilities})

    return {
        "as_of": as_of,
        "liquid_assets": liquid,
        "liabilities": liabilities,
        "total_assets": assets,
        "bscf_net": liquid - liabilities,
        "bscf_norm": (liquid - liabilities) / assets,
        "resolved": frozenset(resolved),
        "counted": counted,
    }


def coverage_reason(company_facts: dict) -> str | None:
    """Skip reason when a filer sits outside us-gaap USD coverage.

    Foreign private issuers file under IFRS, often in their home currency, so the
    named chains find nothing and the ticker would otherwise be reported as a
    missing figure - indistinguishable from a real data problem. Judged on the
    most recent Assets fact rather than which taxonomies exist, because some
    filers carry a us-gaap history from years before they moved to IFRS.
    """
    taxonomy = unit = None
    latest = ""
    for name, tags in company_facts.get("facts", {}).items():
        entry = tags.get("Assets")
        if not entry:
            continue
        for unit_name, observations in entry.get("units", {}).items():
            for observation in observations:
                if observation.get("start") is None and observation.get("end", "") > latest:
                    latest, taxonomy, unit = observation["end"], name, unit_name

    if taxonomy is None:
        # No Assets fact under any taxonomy. A filer publishing IFRS and no
        # us-gaap is a foreign issuer rather than a broken domestic filing.
        facts = company_facts.get("facts", {})
        if "ifrs-full" in facts and "us-gaap" not in facts:
            return f"{OUT_OF_SCOPE}foreign private issuer (ifrs-full)"
        return None
    if taxonomy != "us-gaap":
        return f"{OUT_OF_SCOPE}foreign private issuer ({taxonomy}, {unit})"
    if unit != "USD":
        return f"{OUT_OF_SCOPE}reports in {unit}, not USD"
    return None


def analyze_ticker(
    ticker: str, cik: int, refresh: bool = False
) -> tuple[dict | None, str | None]:
    company_facts = cached_sec_get(SEC_COMPANY_FACTS_URL.format(cik=cik), refresh)
    if not company_facts:
        return None, "no XBRL company facts published"

    out_of_scope = coverage_reason(company_facts)
    if out_of_scope:
        return None, out_of_scope

    index = build_instant_index(company_facts)
    report_dates = sorted(index.get("Assets", {}))
    if not report_dates:
        return None, "no total assets reported"

    history = []
    for as_of in report_dates[-TREND_QUARTERS:]:
        snapshot = bscf_at(index, as_of)
        if snapshot:
            history.append(snapshot)
    if not history:
        return None, "no period with both total assets and cash"

    latest = history[-1]
    ocf, ocf_basis = trailing_twelve_months(company_facts, "operating_cash_flow")
    operating_income, _ = trailing_twelve_months(company_facts, "operating_income")
    interest_expense, _ = trailing_twelve_months(company_facts, "interest_expense")
    # Tracked separately from the BSCF components so a missing tag is reported
    # rather than read as "no near-term debt" - this is the one pulled figure
    # that otherwise had no flag and no sweep behind it.
    current_debt_raw = resolve_instant(index, "current_lt_debt", latest["as_of"])
    current_debt = current_debt_raw or 0.0

    # A company that changes how it tags a component mid-history produces a trend
    # step that is a tagging artifact rather than a real balance sheet move, so
    # compare each displayed quarter's resolved components against the latest.
    window = history[-TREND_DISPLAY_QUARTERS:]
    trend_consistent = all(h["resolved"] == latest["resolved"] for h in window)

    # Overlapping disclosure tags make a sum meaningless, so the headline is the
    # largest single unclassified balance: a floor on what the named chains
    # missed, and a real reported figure rather than a synthetic total.
    gap_fields = {f for f in BSCF_COMPONENT_CODES if f not in latest["resolved"]}
    asset_hits = (
        sweep_unclassified(
            company_facts, latest["as_of"], SWEEP_ASSET_PATTERNS, latest["counted"]
        )
        if gap_fields & ASSET_GAP_FIELDS
        else []
    )
    liability_hits = (
        sweep_unclassified(
            company_facts, latest["as_of"], SWEEP_LIABILITY_PATTERNS, latest["counted"]
        )
        if gap_fields & LIABILITY_GAP_FIELDS
        else []
    )

    unspecified_assets = asset_hits[0][1] if asset_hits else 0.0
    unspecified_liabilities = liability_hits[0][1] if liability_hits else 0.0
    doubt = (unspecified_assets + unspecified_liabilities) / latest["total_assets"]
    doubtful = (
        doubt > DOUBT_ABSOLUTE_MAX
        or doubt > DOUBT_VS_SIGNAL_MAX * abs(latest["bscf_norm"])
    )

    return {
        "ticker": ticker,
        "as_of": latest["as_of"],
        "doubt": doubt,
        "doubtful": doubtful,
        "gaps": ",".join(
            [
                code
                for field, code in BSCF_COMPONENT_CODES.items()
                if field not in latest["resolved"]
            ]
            + ([] if current_debt_raw is not None else ["cur_debt"])
        ),
        "trend_consistent": trend_consistent,
        "unspecified_assets": unspecified_assets,
        "unspecified_liabilities": unspecified_liabilities,
        "unspecified_asset_tags": fmt_sweep_tags(asset_hits),
        "unspecified_liability_tags": fmt_sweep_tags(liability_hits),
        "liquid_assets": latest["liquid_assets"],
        "liabilities": latest["liabilities"],
        "total_assets": latest["total_assets"],
        "bscf_net": latest["bscf_net"],
        "bscf_norm": latest["bscf_norm"],
        "bscf_trend": [h["bscf_norm"] for h in history],
        "current_lt_debt": current_debt,
        "operating_cash_flow": ocf,
        "ocf_basis": ocf_basis,
        # Kept as raw figures for the CSV only. The interest coverage ratio they
        # used to feed was removed: its magnitude tracked how little interest a
        # company owed rather than how much trouble it was in.
        "operating_income": operating_income,
        "interest_expense": interest_expense,
    }, None


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def fmt_millions(value: float | None) -> str:
    return "n/a" if value is None else f"{value / OUTPUT_SCALE:,.1f}"


def fmt_ratio(value: float | None, places: int = 3) -> str:
    return "n/a" if value is None else f"{value:,.{places}f}"


def fmt_sweep_tags(hits: list[tuple[str, float]]) -> str:
    return " | ".join(
        f"{tag}:{value / OUTPUT_SCALE:,.0f}M"
        for tag, value in hits[:SWEEP_DETAIL_TAGS]
    )


def fmt_trend(values: list[float], consistent: bool = True) -> str:
    body = " ".join(f"{v:+.3f}" for v in values[-TREND_DISPLAY_QUARTERS:])
    return body if consistent else body + " *"


def build_table(results: list[dict]) -> pd.DataFrame:
    rows = [
        {
            "ticker": r["ticker"],
            "as_of": r["as_of"].isoformat(),
            "liq_assets($M)": fmt_millions(r["liquid_assets"]),
            "liabs($M)": fmt_millions(r["liabilities"]),
            "bscf_norm": fmt_ratio(r["bscf_norm"]),
            "doubt": f"{r['doubt']:.1%}" + ("!" if r["doubtful"] else ""),
            f"bscf_trend(last {TREND_DISPLAY_QUARTERS}, old->new)": fmt_trend(
                r["bscf_trend"], r["trend_consistent"]
            ),
            "unspec_assets($M)": fmt_millions(r["unspecified_assets"]),
            "unspec_liabs($M)": fmt_millions(r["unspecified_liabilities"]),
            "cur_lt_debt($M)": fmt_millions(r["current_lt_debt"]),
            "ocf($M)": fmt_millions(r["operating_cash_flow"]),
            "gaps": r["gaps"] or "-",
        }
        for r in results
    ]
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date", help="earnings date, YYYY-MM-DD")
    parser.add_argument("--tickers", help="comma-separated tickers, bypasses the calendar")
    parser.add_argument("--limit", type=int, help="process at most N tickers")
    parser.add_argument(
        "--min-cap",
        type=float,
        default=MIN_MARKET_CAP,
        help=f"minimum market cap in dollars (default {MIN_MARKET_CAP:,.0f}); 0 disables",
    )
    parser.add_argument(
        "--refresh", action="store_true", help="ignore cached SEC data and re-fetch"
    )
    parser.add_argument(
        "--drop-doubtful",
        action="store_true",
        help="exclude rows whose unspecified pool outweighs the signal",
    )
    args = parser.parse_args()

    if not SEC_USER_AGENT:
        print(
            "SEC_USER_AGENT is not set. SEC EDGAR rejects requests without a contact\n"
            "address. Set it before running, e.g.\n"
            '  $env:SEC_USER_AGENT = "Your Name your.email@example.com"\n'
            "or edit the SEC_USER_AGENT constant at the top of this script.",
            file=sys.stderr,
        )
        return 1

    try:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    except ValueError:
        print(f"Could not parse date {args.date!r}; expected YYYY-MM-DD.", file=sys.stderr)
        return 1

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        print(f"Using {len(tickers)} ticker(s) supplied on the command line.")
    else:
        print(f"Fetching Nasdaq earnings calendar for {target_date}...")
        rows = fetch_earnings_calendar(target_date)
        print(f"  {len(tickers := [t for t, _ in rows])} ticker(s) reporting.")
        if args.min_cap > 0:
            # Unknown cap is kept: missing calendar metadata is not evidence of a
            # small company, and the SEC data will speak for itself.
            tickers = [t for t, cap in rows if cap is None or cap >= args.min_cap]
            print(
                f"  {len(rows) - len(tickers)} below ${args.min_cap / OUTPUT_SCALE:,.0f}M "
                f"market cap, {len(tickers)} remain."
            )
    if not tickers:
        print("Nothing to process.")
        return 0
    if args.limit:
        tickers = tickers[: args.limit]

    print("Fetching SEC ticker->CIK map...")
    cik_map = fetch_ticker_cik_map(args.refresh)

    results: list[dict] = []
    skipped: list[tuple[str, str]] = []
    seen_ciks: dict[int, str] = {}
    for position, ticker in enumerate(tickers, start=1):
        prefix = f"[{position}/{len(tickers)}] {ticker}"
        cik = lookup_cik(ticker, cik_map)
        if cik is None:
            print(f"{prefix}: skipped - not a SEC filer in company_tickers.json", flush=True)
            skipped.append((ticker, "not found in SEC ticker->CIK map"))
            continue
        # Share classes (DGICA/DGICB) are one filer with one balance sheet, so a
        # second ticker would put the same company in the ranking twice.
        if cik in seen_ciks:
            print(f"{prefix}: skipped - share class of {seen_ciks[cik]}", flush=True)
            skipped.append((ticker, f"{OUT_OF_SCOPE}share class of {seen_ciks[cik]}"))
            continue
        seen_ciks[cik] = ticker
        try:
            result, reason = analyze_ticker(ticker, cik, args.refresh)
        except Exception as exc:  # noqa: BLE001 - one bad ticker shouldn't end the run
            print(f"{prefix}: skipped - {type(exc).__name__}: {exc}", flush=True)
            skipped.append((ticker, f"{type(exc).__name__}: {exc}"))
            continue
        if result is None:
            print(f"{prefix}: skipped - {reason}", flush=True)
            skipped.append((ticker, reason))
            continue
        if result["doubtful"] and args.drop_doubtful:
            print(f"{prefix}: skipped - doubt {result['doubt']:.1%} outweighs signal", flush=True)
            skipped.append(
                (
                    ticker,
                    f"{DOUBTFUL}doubt {result['doubt']:.1%} vs bscf_norm "
                    f"{result['bscf_norm']:+.3f}",
                )
            )
            continue
        results.append(result)
        print(
            f"{prefix}: bscf_norm {result['bscf_norm']:+.3f} "
            f"as of {result['as_of']} (ocf: {result['ocf_basis']})",
            flush=True,
        )

    if not results:
        print("\nNo ticker produced a complete set of figures.")
    else:
        results.sort(key=lambda r: r["bscf_norm"])
        table = build_table(results)
        pd.set_option("display.width", 250)
        pd.set_option("display.max_columns", None)
        pd.set_option("display.max_rows", None)
        print(f"\n=== Fundamentals cluster - {target_date} ({len(results)} tickers) ===")
        print(table.to_string(index=False))
        print(
            "\ngaps: figures with no matching XBRL tag, counted as zero "
            "(sti/lti = short/long-term investments, cvt_cur/cvt_nc = convertible "
            "notes, other_ltl = other long-term liabilities, cur_debt = current "
            "portion of long-term debt)."
            "\n   *: the company changed which components it tags during the trend "
            "window, so part of the trend step is a tagging artifact."
            "\ndoubt: unspecified pool as a share of total assets - roughly how far "
            "bscf_norm could move if those balances belong in the formula. A "
            "trailing ! means the doubt outweighs the signal, so the row is a weak "
            "seed however extreme its ratio looks. --drop-doubtful excludes them."
            "\nunspec_assets / unspec_liabs: largest balance found under a tag no "
            "named chain captured - a floor on what was missed, NOT included in "
            "bscf_norm. Source tags are in the CSV; a large figure next to a gap "
            "flag is worth opening the filing for."
        )

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        csv_path = os.path.join(CSV_DIR, f"bscf_{target_date.isoformat()}_{stamp}.csv")
        # The table shows the last TREND_DISPLAY_QUARTERS, but all TREND_QUARTERS
        # are computed, so the CSV keeps the full series rather than discarding it.
        pd.DataFrame(
            [
                {**r, "bscf_trend": " ".join(f"{v:+.4f}" for v in r["bscf_trend"])}
                for r in results
            ]
        ).to_csv(csv_path, index=False)
        print(f"\nWrote {csv_path}")

    if skipped:
        out_of_scope = [(t, r) for t, r in skipped if r.startswith(OUT_OF_SCOPE)]
        doubtful = [(t, r) for t, r in skipped if r.startswith(DOUBTFUL)]
        problems = [
            (t, r)
            for t, r in skipped
            if not r.startswith((OUT_OF_SCOPE, DOUBTFUL))
        ]
        print(f"\n=== Skipped ({len(skipped)}) ===")
        if out_of_scope:
            print(f"\n  Outside coverage by design ({len(out_of_scope)}):")
            for ticker, reason in out_of_scope:
                print(f"    {ticker}: {reason[len(OUT_OF_SCOPE):]}")
        if doubtful:
            print(f"\n  Dropped as doubtful data ({len(doubtful)}):")
            for ticker, reason in doubtful:
                print(f"    {ticker}: {reason[len(DOUBTFUL):]}")
        if problems:
            print(f"\n  Data problems worth a look ({len(problems)}):")
            for ticker, reason in problems:
                print(f"    {ticker}: {reason}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
