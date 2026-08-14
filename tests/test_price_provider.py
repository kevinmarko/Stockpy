"""
tests/test_price_provider.py
=============================
Tests for pilots/price_provider.py. Mocks `data.market_data.get_provider()`
(the CompositeProvider quote path this module now routes through) rather
than `data.fmp_client` directly -- price_provider.py no longer talks to FMP
on its own, per CLAUDE.md's "all quote fetches MUST go through
CompositeProvider" data-layer convention.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from data.market_data import MarketDataError, Quote
from pilots.price_provider import get_current_price, get_stock_quote


def _mock_quote(price: float) -> Quote:
    return Quote(
        symbol="AAPL",
        price=price,
        bid=price - 0.05,
        ask=price + 0.05,
        timestamp=datetime.now(timezone.utc),
        is_stale=False,
        source="fmp",
    )


@patch("data.market_data.get_provider")
def test_get_stock_quote_extracts_live_price(mock_get_provider):
    mock_provider = MagicMock()
    mock_provider.get_latest_quote.return_value = _mock_quote(220.50)
    mock_get_provider.return_value = mock_provider

    quote = get_stock_quote("AAPL")
    assert quote["symbol"] == "AAPL"
    assert quote["price"] == 220.50
    assert quote["previousClose"] == 220.50


@patch("data.market_data.get_provider")
def test_get_stock_quote_degrades_to_zeros_on_provider_failure(mock_get_provider):
    mock_provider = MagicMock()
    mock_provider.get_latest_quote.side_effect = MarketDataError("no quote available")
    mock_get_provider.return_value = mock_provider

    quote = get_stock_quote("UNKNOWN")
    assert quote["symbol"] == "UNKNOWN"
    assert quote["price"] == 0.0
    assert quote["previousClose"] == 0.0


@patch("data.market_data.get_provider")
def test_get_current_price_prefers_live_price(mock_get_provider):
    mock_provider = MagicMock()
    mock_provider.get_latest_quote.return_value = _mock_quote(220.50)
    mock_get_provider.return_value = mock_provider

    price = get_current_price("AAPL")
    assert price == 220.50


@patch("data.market_data.get_provider")
def test_get_current_price_falls_back_to_explicit_fallback(mock_get_provider):
    mock_provider = MagicMock()
    mock_provider.get_latest_quote.side_effect = MarketDataError("no quote available")
    mock_get_provider.return_value = mock_provider

    price = get_current_price("UNKNOWN", fallback_price=50.0)
    assert price == 50.0


@patch("data.market_data.get_provider")
def test_get_current_price_returns_zero_with_no_fallback(mock_get_provider):
    mock_provider = MagicMock()
    mock_provider.get_latest_quote.side_effect = MarketDataError("no quote available")
    mock_get_provider.return_value = mock_provider

    price = get_current_price("UNKNOWN")
    assert price == 0.0
