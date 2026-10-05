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
    """Tests for get_latest_prices -- the batched multi-symbol quote fetch.
    Routes through ``CompositeProvider.get_quotes_batch`` (one batch request
    per cache miss, the in-process quote TTL cache, and the FMP ->
    yfinance fallback chain) rather than calling ``data.fmp_client.
    batch_quote`` uncached -- the old path cost one live FMP request per tick
    per client on the 1 Hz ``/ws/risk/portfolio`` stream.
    """

    @staticmethod
    def _q(sym, price):
        return Quote(
            symbol=sym, price=price, bid=None, ask=None,
            timestamp=datetime.now(timezone.utc), source="fmp", is_stale=False,
        )

    @patch("data.market_data.get_provider")
    def test_returns_dict_for_all_resolvable_symbols(self, mock_get_provider):
        mock_get_provider.return_value.get_quotes_batch.return_value = {
            "AAPL": self._q("AAPL", 220.50), "MSFT": self._q("MSFT", 410.0),
        }
        prices = get_latest_prices(["AAPL", "MSFT"])
        assert prices == {"AAPL": 220.50, "MSFT": 410.0}
        mock_get_provider.return_value.get_quotes_batch.assert_called_once_with(["AAPL", "MSFT"])

    @patch("data.market_data.get_provider")
    def test_skips_missing_zero_negative_and_nan_prices(self, mock_get_provider):
        mock_get_provider.return_value.get_quotes_batch.return_value = {
            "AAPL": self._q("AAPL", 220.50),
            "TSLA": self._q("TSLA", 0.0),
            "GOOGL": self._q("GOOGL", -5.0),
            "NANCO": self._q("NANCO", float("nan")),
            "NONECO": self._q("NONECO", None),
        }
        prices = get_latest_prices(["AAPL", "MSFT", "TSLA", "GOOGL", "NANCO", "NONECO"])
        assert prices == {"AAPL": 220.50}

    @patch("data.market_data.get_provider")
    def test_degrades_to_empty_dict_on_batch_call_exception(self, mock_get_provider):
        mock_get_provider.return_value.get_quotes_batch.side_effect = RuntimeError("network down")
        assert get_latest_prices(["AAPL", "MSFT"]) == {}

    def test_empty_symbol_list_short_circuits_without_calling_provider(self):
        with patch("data.market_data.get_provider") as mock_get_provider:
            assert get_latest_prices([]) == {}
            mock_get_provider.assert_not_called()

    @patch("data.market_data.get_provider")
    def test_normalizes_whitespace_and_case(self, mock_get_provider):
        mock_get_provider.return_value.get_quotes_batch.return_value = {"AAPL": self._q("AAPL", 100.0)}
        assert get_latest_prices([" aapl ", ""]) == {"AAPL": 100.0}
        mock_get_provider.return_value.get_quotes_batch.assert_called_once_with(["AAPL"])
