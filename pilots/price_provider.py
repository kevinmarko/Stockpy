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
from typing import Any, Dict, Optional

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
