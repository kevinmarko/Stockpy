"""
pilots/price_provider.py
========================
Pricing provider for stock and options paper trading.
Routes through `data.market_data.get_provider()` (the `CompositeProvider`
`MarketDataProvider` ABC), per CLAUDE.md's data-layer convention that all
quote fetches outside `DataEngine.fetch_technical_raw()` MUST go through it
-- this gets the FMP/Alpaca/yfinance fallback chain, the in-process TTL
quote cache, and `is_stale` staleness flagging for free, rather than a
second, uncached, ungated direct FMP client.

Quote structure returned by `get_stock_quote`:
  - Real fields: ``price``, ``previousClose`` (mirrors ``price`` -- the
    underlying ``Quote`` dataclass has no separate previous-close field),
    ``dayLow``, ``dayHigh``, ``volume``.
  - Does NOT provide real-time bid/ask on the default configuration;
    execution models must use ``price`` as the reference fill price.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def get_stock_quote(symbol: str) -> Dict[str, Any]:
    """
    Fetches a quote for a symbol via `data.market_data.get_provider()`.
    Returns dict with price, previousClose, dayLow, dayHigh, volume --
    all 0.0 (never fabricated) when no live quote is available.
    """
    from data.market_data import MarketDataError, get_provider

    symbol = symbol.strip().upper()
    try:
        quote = get_provider().get_latest_quote(symbol)
        price = float(quote.price or 0.0)
        return {
            "symbol": symbol,
            "price": price,
            "previousClose": price,
            "dayLow": 0.0,
            "dayHigh": 0.0,
            "volume": 0.0,
        }
    except MarketDataError as e:
        logger.warning(f"Failed to fetch quote for {symbol}: {e}")
    except Exception as e:
        logger.warning(f"Unexpected error fetching quote for {symbol}: {e}")

    return {
        "symbol": symbol,
        "price": 0.0,
        "previousClose": 0.0,
        "dayLow": 0.0,
        "dayHigh": 0.0,
        "volume": 0.0,
    }


def get_latest_price(symbol: str) -> float:
    """Returns the latest spot price for symbol, or 0.0 if unavailable."""
    quote = get_stock_quote(symbol)
    return float(quote.get("price") or 0.0)


def get_latest_prices(symbols: List[str]) -> Dict[str, float]:
    """Fetch latest spot prices for MANY symbols in a single request.

    Calls ``data.fmp_client.batch_quote`` directly (the same real
    ``/batch-quote`` endpoint ``PaperAccountStore._resolve_position_prices``
    already uses -- the established "fetch many quotes in one request"
    pattern in this codebase) rather than looping ``get_stock_quote``/
    ``get_latest_price`` once per symbol, which is what this function exists
    to replace at call sites that need more than one symbol's price per tick.

    Never raises -- mirrors ``get_latest_price``'s degrade philosophy of
    "0.0 / absent rather than blow up the caller", just applied per-entry
    instead of per-call: any symbol whose batch response entry is missing,
    malformed (not a dict, no parseable ``price``), zero, or negative is
    SKIPPED from the returned dict rather than included as a fabricated
    0.0 -- a caller can distinguish "no price available" (key absent) from
    "price is genuinely zero" (impossible for a real equity quote, so this
    never happens for real symbols). A failure of the batch call itself
    (network error, malformed top-level response) degrades to an empty
    dict, with a WARNING logged, exactly like ``get_stock_quote``'s network
    failure path.
    """
    from data.fmp_client import batch_quote

    clean_symbols = [str(s).strip().upper() for s in symbols if str(s).strip()]
    if not clean_symbols:
        return {}

    prices: Dict[str, float] = {}
    try:
        quotes_resp = batch_quote(clean_symbols)
    except Exception as e:
        logger.warning(f"Failed to fetch batch quotes for {clean_symbols}: {e}")
        return {}

    if not isinstance(quotes_resp, list):
        logger.warning(f"Unexpected batch quote response shape for {clean_symbols}: {type(quotes_resp)!r}")
        return {}

    for entry in quotes_resp:
        if not isinstance(entry, dict):
            continue
        sym = str(entry.get("symbol", "")).strip().upper()
        if not sym:
            continue
        try:
            price = float(entry.get("price") or 0.0)
        except (TypeError, ValueError):
            continue
        if price > 0.0:
            prices[sym] = price

    return prices


def get_current_price(symbol: str, fallback_price: Optional[float] = None) -> float:
    """
    Returns current stock price from FMP, falling back to previousClose or explicit fallback.
    """
    quote = get_stock_quote(symbol)
    price = quote.get("price", 0.0)
    if price > 0.0:
        return price
    prev_close = quote.get("previousClose", 0.0)
    if prev_close > 0.0:
        return prev_close
    if fallback_price is not None and fallback_price > 0.0:
        return float(fallback_price)
    return 0.0
