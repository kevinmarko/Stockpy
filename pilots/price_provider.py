"""
pilots/price_provider.py
========================
Pricing provider for stock and options paper trading.
Extracts real quotes from Financial Modeling Prep (FMP).

FMP Quote Structure:
  - Real fields returned: ``price``, ``previousClose``, ``dayLow``, ``dayHigh``, ``volume``.
  - Explicitly does NOT provide real-time bid/ask on standard endpoints; execution models
    must use ``price`` as the reference fill price.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def get_stock_quote(symbol: str) -> Dict[str, Any]:
    """
    Fetches quote data for a symbol from FMP via `data.fmp_client`.
    Returns dict with price, previousClose, dayLow, dayHigh, etc.
    """
    from data import fmp_client

    symbol = symbol.strip().upper()
    try:
        quotes = fmp_client.batch_quote([symbol])
        for q in quotes:
            if isinstance(q, dict) and q.get("symbol", "").upper() == symbol:
                return {
                    "symbol": symbol,
                    "price": float(q.get("price", 0.0) or 0.0),
                    "previousClose": float(q.get("previousClose", 0.0) or 0.0),
                    "dayLow": float(q.get("dayLow", 0.0) or 0.0),
                    "dayHigh": float(q.get("dayHigh", 0.0) or 0.0),
                    "volume": float(q.get("volume", 0.0) or 0.0),
                }
    except Exception as e:
        logger.warning(f"Failed to fetch FMP quote for {symbol}: {e}")

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
