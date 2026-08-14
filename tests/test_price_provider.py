"""
tests/test_price_provider.py
=============================
Tests for pilots/price_provider.py using real FMP quote schema.
"""

from unittest.mock import patch
from pilots.price_provider import get_stock_quote, get_current_price


@patch("data.fmp_client.batch_quote")
def test_get_stock_quote_extracts_real_fmp_fields(mock_batch):
    mock_batch.return_value = [{
        "symbol": "AAPL",
        "price": 220.50,
        "previousClose": 218.00,
        "dayLow": 217.50,
        "dayHigh": 221.00,
        "volume": 45000000.0,
    }]

    quote = get_stock_quote("AAPL")
    assert quote["symbol"] == "AAPL"
    assert quote["price"] == 220.50
    assert quote["previousClose"] == 218.00
    assert quote["dayLow"] == 217.50
    assert quote["dayHigh"] == 221.00
    assert quote["volume"] == 45000000.0


@patch("data.fmp_client.batch_quote")
def test_get_current_price_prefers_live_price(mock_batch):
    mock_batch.return_value = [{"symbol": "AAPL", "price": 220.50, "previousClose": 218.00}]
    price = get_current_price("AAPL")
    assert price == 220.50


@patch("data.fmp_client.batch_quote")
def test_get_current_price_falls_back_to_previous_close(mock_batch):
    mock_batch.return_value = [{"symbol": "AAPL", "price": 0.0, "previousClose": 218.00}]
    price = get_current_price("AAPL")
    assert price == 218.00


@patch("data.fmp_client.batch_quote")
def test_get_current_price_falls_back_to_explicit_fallback(mock_batch):
    mock_batch.return_value = []
    price = get_current_price("UNKNOWN", fallback_price=50.0)
    assert price == 50.0
