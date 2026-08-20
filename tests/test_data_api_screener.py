"""
tests/test_data_api_screener.py
=================================
Fully-offline tests for ``GET /data/symbol-search``, ``GET /data/screener``,
and ``GET /data/screener/filters`` (``api/data_api.py``) -- the on-demand
FMP symbol-search and sector/industry-screener endpoints gated by
``settings.FMP_SCREENER_ENABLED`` (default ``True``).

Mirrors ``tests/test_data_api_peers.py``'s conventions exactly:
``mock.patch.object(settings, "STATE_API_TOKEN", None)`` to exercise the
fail-open-on-loopback read path, and ``unittest.mock.patch("data.fmp_screener.
<fn>", ...)`` to substitute the lazily-imported dispatcher without touching
the network.
"""
from __future__ import annotations

from unittest import mock

from fastapi.testclient import TestClient

from settings import settings
import api.data_api as data_api

# Starlette's TestClient defaults request.client.host to the literal string
# "testclient" -- NOT loopback -- which would trip api.auth.require_read_token's
# fail-closed-when-non-loopback branch on every zero-config assertion below.
client = TestClient(data_api.app, client=("127.0.0.1", 54123))


# ---------------------------------------------------------------------------
# Flag attribute sanity
# ---------------------------------------------------------------------------


def test_flag_attribute_exists_with_documented_default():
    assert getattr(settings, "FMP_SCREENER_ENABLED", False) is True


# ---------------------------------------------------------------------------
# GET /data/symbol-search
# ---------------------------------------------------------------------------


def test_symbol_search_flag_off_returns_empty_with_honest_reason_and_never_calls_fetch(monkeypatch):
    monkeypatch.setattr(settings, "FMP_SCREENER_ENABLED", False)
    with mock.patch("data.fmp_screener.search_symbols") as mock_fetch, \
         mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/symbol-search?query=Apple")

    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "Apple"
    assert body["results"] == []
    assert body["reason"] == "Symbol search is disabled (FMP_SCREENER_ENABLED=False)."
    mock_fetch.assert_not_called()


def test_symbol_search_flag_on_returns_the_mocked_results(monkeypatch):
    monkeypatch.setattr(settings, "FMP_SCREENER_ENABLED", True)
    mocked = [{"symbol": "AAPL", "name": "Apple Inc.", "currency": "USD",
               "exchange": "NASDAQ", "exchange_full_name": "NASDAQ Global Select"}]
    with mock.patch("data.fmp_screener.search_symbols", return_value=mocked) as mock_fetch, \
         mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/symbol-search?query=Apple&limit=5")

    assert resp.status_code == 200
    body = resp.json()
    assert body["results"] == mocked
    assert body["reason"] is None
    mock_fetch.assert_called_once_with("Apple", limit=5)


def test_symbol_search_empty_result_gets_an_honest_reason(monkeypatch):
    monkeypatch.setattr(settings, "FMP_SCREENER_ENABLED", True)
    with mock.patch("data.fmp_screener.search_symbols", return_value=[]), \
         mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/symbol-search?query=ZZZZNOTREAL")

    assert resp.status_code == 200
    body = resp.json()
    assert body["results"] == []
    assert body["reason"] == "No matching symbols found."


def test_symbol_search_missing_query_is_a_422():
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/symbol-search")
    assert resp.status_code == 422


def test_symbol_search_unexpected_exception_degrades_to_empty_never_500(monkeypatch):
    monkeypatch.setattr(settings, "FMP_SCREENER_ENABLED", True)
    with mock.patch("data.fmp_screener.search_symbols", side_effect=RuntimeError("boom")), \
         mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/symbol-search?query=Apple")

    assert resp.status_code == 200
    body = resp.json()
    assert body["results"] == []
    assert body["reason"] == "No matching symbols found."


# ---------------------------------------------------------------------------
# GET /data/screener
# ---------------------------------------------------------------------------


def test_screener_flag_off_returns_empty_with_honest_reason_and_never_calls_fetch(monkeypatch):
    monkeypatch.setattr(settings, "FMP_SCREENER_ENABLED", False)
    with mock.patch("data.fmp_screener.screen_companies") as mock_fetch, \
         mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/screener?sector=Technology")

    assert resp.status_code == 200
    body = resp.json()
    assert body["results"] == []
    assert body["reason"] == "The symbol screener is disabled (FMP_SCREENER_ENABLED=False)."
    mock_fetch.assert_not_called()


def test_screener_flag_on_only_sends_non_none_filters(monkeypatch):
    monkeypatch.setattr(settings, "FMP_SCREENER_ENABLED", True)
    mocked = [{"symbol": "NVDA", "company_name": "NVIDIA Corporation", "sector": "Technology"}]
    with mock.patch("data.fmp_screener.screen_companies", return_value=mocked) as mock_fetch, \
         mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/screener?sector=Technology&market_cap_more_than=10000000000")

    assert resp.status_code == 200
    body = resp.json()
    assert body["results"] == mocked
    assert body["reason"] is None
    mock_fetch.assert_called_once_with(
        sector="Technology",
        industry=None,
        marketCapMoreThan=10000000000.0,
        marketCapLowerThan=None,
        priceMoreThan=None,
        priceLowerThan=None,
        betaMoreThan=None,
        betaLowerThan=None,
        dividendMoreThan=None,
        dividendLowerThan=None,
        volumeMoreThan=None,
        exchange=None,
        country=None,
        isActivelyTrading=None,
        limit=None,
        page=None,
    )


def test_screener_exclude_funds_adds_is_etf_and_is_fund_false(monkeypatch):
    monkeypatch.setattr(settings, "FMP_SCREENER_ENABLED", True)
    with mock.patch("data.fmp_screener.screen_companies", return_value=[]) as mock_fetch, \
         mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/screener?exclude_funds=true")

    assert resp.status_code == 200
    _, kwargs = mock_fetch.call_args
    assert kwargs["isEtf"] is False
    assert kwargs["isFund"] is False


def test_screener_empty_result_gets_an_honest_reason(monkeypatch):
    monkeypatch.setattr(settings, "FMP_SCREENER_ENABLED", True)
    with mock.patch("data.fmp_screener.screen_companies", return_value=[]), \
         mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/screener?sector=Technology&price_more_than=999999")

    assert resp.status_code == 200
    body = resp.json()
    assert body["results"] == []
    assert body["reason"] == "No symbols matched these filters."


def test_screener_unexpected_exception_degrades_to_empty_never_500(monkeypatch):
    monkeypatch.setattr(settings, "FMP_SCREENER_ENABLED", True)
    with mock.patch("data.fmp_screener.screen_companies", side_effect=RuntimeError("boom")), \
         mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/screener?sector=Technology")

    assert resp.status_code == 200
    body = resp.json()
    assert body["results"] == []
    assert body["reason"] == "No symbols matched these filters."


# ---------------------------------------------------------------------------
# GET /data/screener/filters
# ---------------------------------------------------------------------------


def test_screener_filters_flag_off_returns_empty_lists_and_never_calls_fetch(monkeypatch):
    monkeypatch.setattr(settings, "FMP_SCREENER_ENABLED", False)
    with mock.patch("data.fmp_screener.list_sectors") as mock_sectors, \
         mock.patch("data.fmp_screener.list_industries") as mock_industries, \
         mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/screener/filters")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"sectors": [], "industries": []}
    mock_sectors.assert_not_called()
    mock_industries.assert_not_called()


def test_screener_filters_flag_on_returns_the_mocked_enums(monkeypatch):
    monkeypatch.setattr(settings, "FMP_SCREENER_ENABLED", True)
    with mock.patch("data.fmp_screener.list_sectors", return_value=["Technology", "Healthcare"]), \
         mock.patch("data.fmp_screener.list_industries", return_value=["Semiconductors"]), \
         mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/screener/filters")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"sectors": ["Technology", "Healthcare"], "industries": ["Semiconductors"]}


def test_screener_filters_one_side_failing_still_returns_the_other(monkeypatch):
    monkeypatch.setattr(settings, "FMP_SCREENER_ENABLED", True)
    with mock.patch("data.fmp_screener.list_sectors", side_effect=RuntimeError("boom")), \
         mock.patch("data.fmp_screener.list_industries", return_value=["Semiconductors"]), \
         mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/screener/filters")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"sectors": [], "industries": ["Semiconductors"]}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_401_with_wrong_read_token():
    with mock.patch.object(settings, "STATE_API_TOKEN", "secret-token"):
        resp = client.get(
            "/data/symbol-search?query=Apple", headers={"Authorization": "Bearer wrong-token"},
        )
    assert resp.status_code == 401
