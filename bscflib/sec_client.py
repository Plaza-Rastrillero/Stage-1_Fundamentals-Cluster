"""Talking to SEC EDGAR: rate limiting, retries, and the ticker -> CIK map."""

from __future__ import annotations

import os
import time

import requests

from . import sec_cache

# EDGAR returns 403 to any request without a real contact address.
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "")

SEC_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SEC_REQUEST_INTERVAL = 0.12  # EDGAR's published ceiling is 10 requests/second
SEC_TIMEOUT = 60
SEC_MAX_RETRIES = 3

USER_AGENT_HELP = (
    'SEC_USER_AGENT is not set. EDGAR rejects requests without a contact\n'
    'address. Set it first, e.g.\n'
    '  $env:SEC_USER_AGENT = "Your Name your.email@example.com"'
)

_last_call = 0.0


def sec_get(url: str) -> dict | None:
    """GET a SEC endpoint, rate-limited and retried. None on a clean 404.

    404 is the *only* status that returns None, because it is the only one that
    means "this filer has nothing published". Every other outcome raises.
    Returning None for, say, a redirect or a 204 would surface to the user as
    "no XBRL company facts published" - a claim about the company derived from
    a transport oddity, which is exactly what CONTEXT.md 1.3 rule 6 forbids.
    """
    global _last_call
    headers = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    for attempt in range(SEC_MAX_RETRIES):
        wait = SEC_REQUEST_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
        response = requests.get(url, headers=headers, timeout=SEC_TIMEOUT)
        if response.status_code == 404:
            return None
        if response.status_code == 200:
            return response.json()
        if attempt == SEC_MAX_RETRIES - 1:
            # raise_for_status only raises on 4xx/5xx; a 3xx or a 2xx that is
            # not 200 would otherwise fall out of the loop silently.
            response.raise_for_status()
            raise RuntimeError(f"SEC returned HTTP {response.status_code} for {url}")
        time.sleep(2**attempt)
    raise RuntimeError(f"SEC request exhausted {SEC_MAX_RETRIES} retries: {url}")


def cached_sec_get(url: str, refresh: bool = False) -> dict | None:
    payload = sec_cache.load(url, refresh)
    if payload is not None:
        return payload
    payload = sec_get(url)
    if payload is not None:
        sec_cache.store(url, payload)
    return payload


def fetch_ticker_cik_map(refresh: bool = False) -> dict[str, int]:
    payload = cached_sec_get(SEC_TICKER_MAP_URL, refresh) or {}
    return {
        entry["ticker"].strip().upper(): int(entry["cik_str"])
        for entry in payload.values()
    }


def lookup_cik(ticker: str, cik_map: dict[str, int]) -> int | None:
    """Resolve a ticker to a CIK, reconciling share-class separators."""
    for candidate in (ticker, ticker.replace(".", "-"), ticker.replace("/", "-")):
        if candidate in cik_map:
            return cik_map[candidate]
    return None


def fetch_company_facts(cik: int, refresh: bool = False) -> dict | None:
    return cached_sec_get(SEC_COMPANY_FACTS_URL.format(cik=cik), refresh)
