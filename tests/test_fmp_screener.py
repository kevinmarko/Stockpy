"""
tests/test_fmp_screener.py
===========================
Unit tests for ``data/fmp_screener.py`` -- the FMP-backed symbol-search and
sector/industry-screener dispatcher. The underlying HTTP wrapper machinery
(``_fmp_get``'s throttle/retry/cooldown/dead-endpoint state) is already
exhaustively covered by ``tests/test_fmp_client.py``; this file only proves
``data/fmp_screener.py`` routes through ``data/fmp_client.py``'s wrappers
correctly, gates on ``FMP_SCREENER_ENABLED``, and degrades to ``[]`` on every
failure shape (CONSTRAINT #6 -- never raises).

Everything here is offline: ``data.fmp_client``'s wrapper functions are
monkeypatched directly; no real network request occurs.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from data.fmp_client import FMPUnavailable
from data.fmp_screener import (
    list_industries,
    list_sectors,
    screen_companies,
    search_symbols,
)
from settings import settings


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "FMP_SCREENER_ENABLED", True)
    yield


@pytest.fixture
def disabled(monkeypatch):
    monkeypatch.setattr(settings, "FMP_SCREENER_ENABLED", False)
    yield


# ---------------------------------------------------------------------------
# search_symbols
# ---------------------------------------------------------------------------

class TestSearchSymbols:
    def test_disabled_returns_empty_no_network(self, disabled):
        with patch("data.fmp_client.search_name") as mock_name:
            result = search_symbols("Apple")
        assert result == []
        mock_name.assert_not_called()

    def test_blank_query_returns_empty_no_network(self, enabled):
        with patch("data.fmp_client.search_name") as mock_name:
            result = search_symbols("   ")
        assert result == []
        mock_name.assert_not_called()

    def test_happy_path_via_search_name(self, enabled):
        raw = [
            {"symbol": "aapl", "name": "Apple Inc.", "currency": "USD",
             "exchange": "NASDAQ", "exchangeFullName": "NASDAQ Global Select"},
        ]
        with patch("data.fmp_client.search_name", return_value=raw) as mock_name, \
             patch("data.fmp_client.search_symbol") as mock_symbol:
            result = search_symbols("Apple", limit=5)
        mock_name.assert_called_once_with("Apple", limit=5)
        mock_symbol.assert_not_called()
        assert result == [{
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "currency": "USD",
            "exchange": "NASDAQ",
            "exchange_full_name": "NASDAQ Global Select",
        }]

    def test_falls_back_to_search_symbol_when_name_search_is_empty(self, enabled):
        with patch("data.fmp_client.search_name", return_value=[]) as mock_name, \
             patch("data.fmp_client.search_symbol", return_value=[{"symbol": "AAPL", "name": "Apple Inc."}]) as mock_symbol:
            result = search_symbols("AAPL")
        mock_name.assert_called_once()
        mock_symbol.assert_called_once()
        assert result[0]["symbol"] == "AAPL"

    def test_fmpunavailable_degrades_to_empty_not_raised(self, enabled):
        with patch("data.fmp_client.search_name", side_effect=FMPUnavailable("cooldown open")):
            result = search_symbols("Apple")
        assert result == []

    def test_unexpected_exception_degrades_to_empty_not_raised(self, enabled):
        with patch("data.fmp_client.search_name", side_effect=RuntimeError("unexpected")):
            result = search_symbols("Apple")
        assert result == []

    def test_non_list_response_returns_empty(self, enabled):
        with patch("data.fmp_client.search_name", return_value={"error": "bad"}):
            result = search_symbols("Apple")
        assert result == []

    def test_row_missing_symbol_is_skipped_not_raised(self, enabled):
        raw = [{"name": "No Symbol Co."}, {"symbol": "good", "name": "Good Co."}]
        with patch("data.fmp_client.search_name", return_value=raw):
            result = search_symbols("Co")
        assert len(result) == 1
        assert result[0]["symbol"] == "GOOD"

    def test_non_dict_row_is_skipped_not_raised(self, enabled):
        raw = ["not-a-dict", {"symbol": "good"}]
        with patch("data.fmp_client.search_name", return_value=raw):
            result = search_symbols("Co")
        assert len(result) == 1
        assert result[0]["symbol"] == "GOOD"


# ---------------------------------------------------------------------------
# screen_companies
# ---------------------------------------------------------------------------

class TestScreenCompanies:
    def test_disabled_returns_empty_no_network(self, disabled):
        with patch("data.fmp_client.company_screener") as mock_screener:
            result = screen_companies(sector="Technology")
        assert result == []
        mock_screener.assert_not_called()

    def test_happy_path_reshapes_rows(self, enabled):
        raw = [{
            "symbol": "nvda", "companyName": "NVIDIA Corporation", "marketCap": 5271460862100,
            "sector": "Technology", "industry": "Semiconductors", "beta": 2.215, "price": 217.64,
            "lastAnnualDividend": 0.28, "volume": 43873026, "exchange": "NASDAQ Global Select",
            "exchangeShortName": "NASDAQ", "country": "US", "isEtf": False, "isFund": False,
            "isActivelyTrading": True,
        }]
        with patch("data.fmp_client.company_screener", return_value=raw) as mock_screener:
            result = screen_companies(sector="Technology", marketCapMoreThan=1e10)
        mock_screener.assert_called_once_with(sector="Technology", marketCapMoreThan=1e10)
        assert result[0]["symbol"] == "NVDA"
        assert result[0]["company_name"] == "NVIDIA Corporation"
        assert result[0]["market_cap"] == 5271460862100
        assert result[0]["is_actively_trading"] is True

    def test_fmpunavailable_degrades_to_empty_not_raised(self, enabled):
        with patch("data.fmp_client.company_screener", side_effect=FMPUnavailable("cooldown open")):
            result = screen_companies(sector="Technology")
        assert result == []

    def test_unexpected_exception_degrades_to_empty_not_raised(self, enabled):
        with patch("data.fmp_client.company_screener", side_effect=RuntimeError("unexpected")):
            result = screen_companies(sector="Technology")
        assert result == []

    def test_non_list_response_returns_empty(self, enabled):
        with patch("data.fmp_client.company_screener", return_value={"error": "bad"}):
            result = screen_companies(sector="Technology")
        assert result == []

    def test_row_missing_symbol_is_skipped_not_raised(self, enabled):
        raw = [{"companyName": "No Symbol Co."}, {"symbol": "good"}]
        with patch("data.fmp_client.company_screener", return_value=raw):
            result = screen_companies()
        assert len(result) == 1
        assert result[0]["symbol"] == "GOOD"


# ---------------------------------------------------------------------------
# list_sectors / list_industries
# ---------------------------------------------------------------------------

class TestListSectors:
    def test_disabled_returns_empty_no_network(self, disabled):
        with patch("data.fmp_client.available_sectors") as mock_sectors:
            result = list_sectors()
        assert result == []
        mock_sectors.assert_not_called()

    def test_happy_path(self, enabled):
        raw = [{"sector": "Technology"}, {"sector": "Healthcare"}]
        with patch("data.fmp_client.available_sectors", return_value=raw):
            result = list_sectors()
        assert result == ["Technology", "Healthcare"]

    def test_fmpunavailable_degrades_to_empty_not_raised(self, enabled):
        with patch("data.fmp_client.available_sectors", side_effect=FMPUnavailable("cooldown open")):
            result = list_sectors()
        assert result == []

    def test_non_dict_row_is_skipped_not_raised(self, enabled):
        raw = ["not-a-dict", {"sector": "Technology"}]
        with patch("data.fmp_client.available_sectors", return_value=raw):
            result = list_sectors()
        assert result == ["Technology"]


class TestListIndustries:
    def test_disabled_returns_empty_no_network(self, disabled):
        with patch("data.fmp_client.available_industries") as mock_industries:
            result = list_industries()
        assert result == []
        mock_industries.assert_not_called()

    def test_happy_path(self, enabled):
        raw = [{"industry": "Semiconductors"}, {"industry": "Software - Infrastructure"}]
        with patch("data.fmp_client.available_industries", return_value=raw):
            result = list_industries()
        assert result == ["Semiconductors", "Software - Infrastructure"]

    def test_unexpected_exception_degrades_to_empty_not_raised(self, enabled):
        with patch("data.fmp_client.available_industries", side_effect=RuntimeError("unexpected")):
            result = list_industries()
        assert result == []
