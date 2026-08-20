"""FMP-backed symbol search & sector/industry screener — a universe-BROWSE
capability, deliberately kept separate from ``data/market_data.py``'s
``CompositeProvider``/``FMPProvider``.

Why this module exists, and why it is NOT on ``CompositeProvider``
--------------------------------------------------------------------
``MarketDataProvider``'s three abstract methods (``get_latest_quote``,
``get_intraday_bars``, ``get_fundamentals``) all take a KNOWN single
``symbol`` and return a fixed per-symbol shape, so every concrete provider
can implement all three and ``CompositeProvider`` can pick a primary/fallback
per symbol. A symbol search or sector/industry screener returns an
ARBITRARY LIST of symbols matching a filter — it has no per-symbol fallback
chain (there is no Alpaca/yfinance screener equivalent to fall back to), so
it doesn't fit that ABC. This mirrors the existing precedent: ``peers()``
(also list-returning, also FMP-only, also no fallback chain) lives as a
standalone function in ``data/fmp_feeds_market.py``, not on
``CompositeProvider``, and ``historical_sp500_changes()`` lives in this
module's own sibling, ``data/fmp_universe.py`` — not in ``market_data.py``
either.

Not a ``SignalModule``, no point-in-time history
--------------------------------------------------
These are read-only, on-demand, request-scoped endpoints (like the other FMP
diagnostic feeds documented in ``docs/FMP_INTEGRATION.md`` §1a) — FMP serves
only the CURRENT screener/search result, so there is nothing to backtest
against and no lookahead-perturbation-test obligation.

Gated, never raises (CONSTRAINT #6)
--------------------------------------
Every function here returns ``[]`` on every failure path — flag off,
``FMPUnavailable`` (429/5xx/403/breaker-open/no-key/etc., see
``data/fmp_client.py``'s status-code matrix), or a malformed/unexpected
response shape. A malformed individual ROW is skipped (logged), never lets
one bad row crash the whole list.

Verification status
--------------------
Verified live 2026-08 via an external FMP MCP connector (a real, working FMP
account) — NOT yet exercised through this repo's own ``_fmp_get``
throttle/retry/cooldown path or the operator's own ``FMP_API_KEY``/tier.
See ``docs/FMP_INTEGRATION.md`` §9 for what has and has not actually been
confirmed against the operator's own key.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("FMP_Screener")


def _screener_enabled() -> bool:
    from settings import settings as _settings

    return bool(getattr(_settings, "FMP_SCREENER_ENABLED", False))


def search_symbols(query: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Company name/ticker search — tries ``search-name`` first (matches on
    company name, e.g. "Apple" -> AAPL and its foreign-exchange listings),
    falling back to ``search-symbol`` (matches on ticker) only if the name
    search yields nothing, so a bare ticker query like "AAPL" still resolves.
    Returns ``[]`` (never raises) when disabled, unavailable, or the query is
    blank."""
    q = (query or "").strip()
    if not q or not _screener_enabled():
        return []

    from data.fmp_client import FMPUnavailable, search_name, search_symbol

    try:
        raw = search_name(q, limit=limit)
        if not raw:
            raw = search_symbol(q, limit=limit)
    except FMPUnavailable as exc:
        logger.debug("search_symbols: FMP dispatch failed for %r: %s", q, exc)
        return []
    except Exception as exc:  # pragma: no cover -- defensive, FMP path already guards itself
        logger.debug("search_symbols: unexpected FMP failure for %r: %s", q, exc)
        return []

    if not isinstance(raw, list):
        return []

    out: List[Dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict) or not row.get("symbol"):
            continue
        try:
            out.append({
                "symbol": str(row["symbol"]).strip().upper(),
                "name": row.get("name"),
                "currency": row.get("currency"),
                "exchange": row.get("exchange"),
                "exchange_full_name": row.get("exchangeFullName"),
            })
        except Exception as ex:
            logger.warning("search_symbols: skipping malformed row: %r due to: %s", row, ex)

    return out


def screen_companies(**filters: Any) -> List[Dict[str, Any]]:
    """Sector/industry/market-cap/price/beta/dividend/volume screener. Pass
    any subset of ``sector``, ``industry``, ``marketCapMoreThan``,
    ``marketCapLowerThan``, ``priceMoreThan``, ``priceLowerThan``,
    ``betaMoreThan``, ``betaLowerThan``, ``dividendMoreThan``,
    ``dividendLowerThan``, ``volumeMoreThan``, ``volumeLowerThan``,
    ``country``, ``exchange``, ``isEtf``, ``isFund``, ``isActivelyTrading``,
    ``limit``, ``page`` as keyword args — only non-``None`` values are sent
    to FMP. Returns ``[]`` (never raises) when disabled or unavailable."""
    if not _screener_enabled():
        return []

    from data.fmp_client import FMPUnavailable, company_screener

    try:
        raw = company_screener(**filters)
    except FMPUnavailable as exc:
        logger.debug("screen_companies: FMP dispatch failed: %s", exc)
        return []
    except Exception as exc:  # pragma: no cover -- defensive, FMP path already guards itself
        logger.debug("screen_companies: unexpected FMP failure: %s", exc)
        return []

    if not isinstance(raw, list):
        return []

    out: List[Dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict) or not row.get("symbol"):
            continue
        try:
            out.append({
                "symbol": str(row["symbol"]).strip().upper(),
                "company_name": row.get("companyName"),
                "market_cap": row.get("marketCap"),
                "sector": row.get("sector"),
                "industry": row.get("industry"),
                "beta": row.get("beta"),
                "price": row.get("price"),
                "last_annual_dividend": row.get("lastAnnualDividend"),
                "volume": row.get("volume"),
                "exchange": row.get("exchange"),
                "exchange_short_name": row.get("exchangeShortName"),
                "country": row.get("country"),
                "is_etf": row.get("isEtf"),
                "is_fund": row.get("isFund"),
                "is_actively_trading": row.get("isActivelyTrading"),
            })
        except Exception as ex:
            logger.warning("screen_companies: skipping malformed row: %r due to: %s", row, ex)

    return out


def list_sectors() -> List[str]:
    """Sector enum for screener filter dropdowns. Returns ``[]`` (never
    raises) when disabled or unavailable."""
    return _list_enum("available_sectors", "sector")


def list_industries() -> List[str]:
    """Industry enum for screener filter dropdowns. Returns ``[]`` (never
    raises) when disabled or unavailable."""
    return _list_enum("available_industries", "industry")


def _list_enum(fn_name: str, key: str) -> List[str]:
    if not _screener_enabled():
        return []

    import data.fmp_client as fmp_client
    from data.fmp_client import FMPUnavailable

    try:
        raw = getattr(fmp_client, fn_name)()
    except FMPUnavailable as exc:
        logger.debug("%s: FMP dispatch failed: %s", fn_name, exc)
        return []
    except Exception as exc:  # pragma: no cover -- defensive, FMP path already guards itself
        logger.debug("%s: unexpected FMP failure: %s", fn_name, exc)
        return []

    if not isinstance(raw, list):
        return []

    out: List[str] = []
    for row in raw:
        if isinstance(row, dict) and row.get(key):
            out.append(str(row[key]))
    return out
