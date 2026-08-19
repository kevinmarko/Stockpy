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
from pilots.price_provider import get_current_price, get_latest_prices, get_stock_quote


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


class TestGetLatestPrices:
    """Tests for get_latest_prices -- the batched multi-symbol quote fetch
    added for the residual quote-fetch executor-offload fix (api/ws_api.py's
    ``/ws/risk/portfolio`` handler). Unlike get_stock_quote/get_current_price
    above, this calls `data.fmp_client.batch_quote` directly (the same real
    batch endpoint `PaperAccountStore._resolve_position_prices` already
    uses), so it's mocked at that seam rather than via CompositeProvider --
    matching `tests/test_paper_account_store.py`'s own
    `data.paper_account_store.fmp_client.batch_quote` mocking convention.
    """

    @patch("data.fmp_client.batch_quote")
    def test_returns_dict_for_all_resolvable_symbols(self, mock_batch_quote):
        mock_batch_quote.return_value = [
            {"symbol": "AAPL", "price": 220.50},
            {"symbol": "MSFT", "price": 410.0},
        ]
        prices = get_latest_prices(["AAPL", "MSFT"])
        assert prices == {"AAPL": 220.50, "MSFT": 410.0}
        mock_batch_quote.assert_called_once()

    @patch("data.fmp_client.batch_quote")
    def test_skips_missing_malformed_zero_and_negative_entries(self, mock_batch_quote):
        mock_batch_quote.return_value = [
            {"symbol": "AAPL", "price": 220.50},
            {"symbol": "MSFT"},  # missing price
            {"symbol": "TSLA", "price": 0.0},  # zero -- never a real quote
            {"symbol": "GOOGL", "price": -5.0},  # negative -- malformed
            {"symbol": "BADCO", "price": "not-a-number"},  # unparseable
            "not-a-dict",  # malformed entry shape entirely
            {"price": 100.0},  # missing symbol
        ]
        prices = get_latest_prices(["AAPL", "MSFT", "TSLA", "GOOGL", "BADCO"])
        assert prices == {"AAPL": 220.50}

    @patch("data.fmp_client.batch_quote")
    def test_degrades_to_empty_dict_on_batch_call_exception(self, mock_batch_quote):
        mock_batch_quote.side_effect = RuntimeError("network down")
        prices = get_latest_prices(["AAPL", "MSFT"])
        assert prices == {}

    @patch("data.fmp_client.batch_quote")
    def test_degrades_to_empty_dict_on_malformed_top_level_response(self, mock_batch_quote):
        mock_batch_quote.return_value = {"unexpected": "shape"}
        prices = get_latest_prices(["AAPL"])
        assert prices == {}

    def test_empty_symbol_list_short_circuits_without_calling_batch_quote(self):
        with patch("data.fmp_client.batch_quote") as mock_batch_quote:
            prices = get_latest_prices([])
            assert prices == {}
            mock_batch_quote.assert_not_called()

    @patch("data.fmp_client.batch_quote")
    def test_never_raises_on_unexpected_input(self, mock_batch_quote):
        mock_batch_quote.return_value = [{"symbol": "AAPL", "price": 100.0}]
        # Whitespace/case noise in the input list must still resolve cleanly.
        prices = get_latest_prices([" aapl ", ""])
        assert prices == {"AAPL": 100.0}
