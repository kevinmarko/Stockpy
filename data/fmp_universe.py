"""FMP-backed S&P 500 constituent-changes feed — the primary source for
``universe_engine.py``'s point-in-time survivorship-bias reconstruction, with
the legacy Wikipedia "Selected changes" table scrape demoted to a fallback.

Why this module exists
-----------------------
Wikipedia removed the "Selected changes to the list of S&P 500 components"
table from ``List_of_S%26P_500_companies`` entirely (confirmed live,
2026-08 — not a markup/selector shift, the content is gone), which broke
``universe_engine.fetch_and_cache_universe()`` unconditionally on any fresh
clone with no pre-existing local cache. FMP's historical S&P 500
constituent-changes endpoint carries a similar date/added/removed schema and
is used here as a drop-in replacement, gated behind
``settings.FMP_UNIVERSE_ENABLED`` + ``settings.FMP_API_KEY`` (both required —
the "explicit dual check in the consumer" convention this codebase already
uses for provider-*replacement* feeds, matching
``signals/news_catalyst.py::_fetch_company_headlines_fmp``).

Gated, never raises
--------------------
:func:`fetch_sp500_changes_via_fmp` returns ``[]`` on every failure path —
flag off, no key, ``FMPUnavailable`` (429/5xx/403/breaker-open/etc, see
``data/fmp_client.py``'s status-code matrix), or a malformed/unexpected
response shape. ``universe_engine.py`` treats an empty list as "fall through
to the Wikipedia changes-table scrape", so an FMP outage or an
implementation-time schema surprise fails safe into the existing behavior
rather than corrupting the cached universe or crashing the caller.

Verification status
--------------------
NOT verified against a live FMP account in this sandbox (no live-market
network access here, and FMP's own docs site blocked automated fetches while
this module was written) — the endpoint path and field names below are best-
effort from public documentation, not a confirmed schema. Per-row parsing is
deliberately defensive (skip-and-log, never raise) for exactly this reason.
See ``docs/FMP_INTEGRATION.md`` §8 for what has and has not actually been
confirmed against a real key.
"""

import logging
from typing import Any, Dict, List

import pandas as pd

from universe_engine import clean_ticker

logger = logging.getLogger("FMP_Universe")


def fetch_sp500_changes_via_fmp() -> List[Dict[str, Any]]:
    """Fetch and reshape FMP's historical S&P 500 constituent-changes feed
    into ``universe_engine.py``'s internal change-record schema
    (``type``/``date``/``added_ticker``/``removed_ticker``). Returns ``[]``
    (never raises) when the feed is disabled, unconfigured, unavailable, or
    yields nothing usable — the caller falls back to the Wikipedia scrape in
    every one of those cases."""
    from settings import settings as _settings

    if not getattr(_settings, "FMP_UNIVERSE_ENABLED", False):
        return []
    if not getattr(_settings, "FMP_API_KEY", None):
        return []

    from data.fmp_client import FMPUnavailable, historical_sp500_changes

    try:
        raw = historical_sp500_changes()
    except FMPUnavailable as exc:
        logger.debug("fetch_sp500_changes_via_fmp: FMP dispatch failed: %s", exc)
        return []
    except Exception as exc:  # pragma: no cover -- defensive, FMP path already guards itself
        logger.debug("fetch_sp500_changes_via_fmp: unexpected FMP failure: %s", exc)
        return []

    if not isinstance(raw, list) or not raw:
        return []

    out: List[Dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        try:
            raw_date = row.get("date")
            if not raw_date:
                continue
            parsed_date = pd.to_datetime(raw_date).strftime("%Y-%m-%d")
            # Field-name candidates: FMP's publicly documented shape uses
            # "symbol" for the added ticker and "removedTicker" for the
            # removed one, with "addedSecurity"/"removedSecurity" company
            # names alongside -- not independently confirmed here (see the
            # module docstring), so both plausible added-ticker keys are
            # tried before giving up on the row.
            added = clean_ticker(row.get("symbol") or row.get("addedTicker"))
            removed = clean_ticker(row.get("removedTicker"))
            if not added and not removed:
                continue
            out.append({
                "type": "change",
                "date": parsed_date,
                "added_ticker": added,
                "removed_ticker": removed,
                "_provider": "fmp",
            })
        except Exception as ex:
            logger.warning("Skipping malformed FMP change row: %r due to: %s", row, ex)

    return out
