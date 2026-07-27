"""
tests/test_data_api.py
======================
Fully-offline tests for the standalone ``api/data_api.py`` FastAPI service
(port 8603). Every network/data call is monkeypatched on the ``api.data_api``
module namespace (it imports its dependencies by name), so no live Yahoo/FRED/
Robinhood access ever happens.

Proves each endpoint returns the frozen contract shape and honours the honesty
rule (NaN/missing → ``null``, never a fabricated ``0.0``).
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from settings import settings
import api.data_api as data_api
from data.market_data import MarketDataError

client = TestClient(data_api.app)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _make_bars(n: int = 5) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {
            "Open": np.linspace(100, 104, n),
            "High": np.linspace(101, 105, n),
            "Low": np.linspace(99, 103, n),
            "Close": np.linspace(100.5, 104.5, n),
            "Volume": [1_000_000] * n,
        },
        index=idx,
    )


class _FakeStore:
    def __init__(self, bars=None, fund_history=None, macro_series=None, sentiment_series=None):
        self._bars = bars
        self._fund_history = fund_history
        # {series_id: pd.Series} / {symbol: pd.Series} -- None means "raise",
        # matching the real HistoricalStore methods' own dead-letter contract
        # (an empty Series, not an exception, is the honest "no history" case).
        self._macro_series = macro_series or {}
        self._sentiment_series = sentiment_series or {}

    def get_bars(self, symbol, lookback_days=252, provider=None):
        if self._bars is None:
            return pd.DataFrame()
        return self._bars

    def get_fundamentals_history(self, symbol, since=None):
        if self._fund_history is None:
            return pd.DataFrame()
        return self._fund_history

    def get_macro(self, series_id, lookback_days=None, data_engine=None):
        return self._macro_series.get(series_id, pd.Series(dtype=float, name=series_id))

    def get_news_sentiment_history(self, symbol, lookback_days=None):
        return self._sentiment_series.get(symbol.upper(), pd.Series(dtype=float, name=symbol))


class _FakeProvider:
    def __init__(self, fundamentals=None, quotes=None):
        self._fundamentals = fundamentals if fundamentals is not None else {}
        self._quotes = quotes or {}

    def get_fundamentals(self, symbol):
        return self._fundamentals

    def get_latest_quote(self, symbol):
        q = self._quotes.get(symbol)
        if q is None:
            raise MarketDataError(f"no quote for {symbol}")
        return q


def _quote(symbol="AAPL", price=190.0, bid=189.9, ask=190.1, stale=True):
    return SimpleNamespace(
        symbol=symbol,
        price=price,
        bid=bid,
        ask=ask,
        timestamp=datetime(2026, 1, 5, 16, 0, tzinfo=timezone.utc),
        is_stale=stale,
        source="yfinance",
    )


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


def test_health_open_no_auth():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "data_api"}


def test_health_open_even_when_token_set():
    with mock.patch.object(settings, "STATE_API_TOKEN", "tok"):
        resp = client.get("/health")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Auth (fail-open)
# ---------------------------------------------------------------------------


def test_fail_open_when_token_unset(monkeypatch):
    monkeypatch.setattr(data_api, "HistoricalStore", lambda **k: _FakeStore(bars=_make_bars()))
    monkeypatch.setattr(data_api, "get_provider", lambda: _FakeProvider())
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/bars/AAPL")
    assert resp.status_code == 200


def test_401_with_wrong_token(monkeypatch):
    with mock.patch.object(settings, "STATE_API_TOKEN", "secret"):
        resp = client.get("/data/bars/AAPL", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /data/bars/{symbol}
# ---------------------------------------------------------------------------


def test_bars_shape(monkeypatch):
    monkeypatch.setattr(data_api, "HistoricalStore", lambda **k: _FakeStore(bars=_make_bars(3)))
    monkeypatch.setattr(data_api, "get_provider", lambda: _FakeProvider())
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/bars/AAPL?lookback_days=30")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list) and len(body) == 3
    first = body[0]
    for key in ("date", "Open", "High", "Low", "Close", "Volume"):
        assert key in first
    # ISO date string, not a raw timestamp object.
    assert isinstance(first["date"], str) and first["date"].startswith("2026-01-01")


def test_bars_empty_returns_empty_list(monkeypatch):
    monkeypatch.setattr(data_api, "HistoricalStore", lambda **k: _FakeStore(bars=None))
    monkeypatch.setattr(data_api, "get_provider", lambda: _FakeProvider())
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/bars/ZZZZ")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /data/fundamentals/{symbol}
# ---------------------------------------------------------------------------


def test_fundamentals_plain_dict_no_to_dict(monkeypatch):
    fund = {"trailingPE": 28.5, "returnOnEquity": 0.31, "debtToEquity": 150.0}
    monkeypatch.setattr(data_api, "get_provider", lambda: _FakeProvider(fundamentals=fund))
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/fundamentals/AAPL")
    assert resp.status_code == 200
    assert resp.json()["trailingPE"] == 28.5


def test_fundamentals_nan_becomes_null(monkeypatch):
    fund = {"trailingPE": float("nan"), "returnOnEquity": 0.31}
    monkeypatch.setattr(data_api, "get_provider", lambda: _FakeProvider(fundamentals=fund))
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/fundamentals/AAPL")
    assert resp.status_code == 200
    assert resp.json()["trailingPE"] is None


def test_fundamentals_empty_is_404(monkeypatch):
    monkeypatch.setattr(data_api, "get_provider", lambda: _FakeProvider(fundamentals={}))
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/fundamentals/ZZZZ")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /data/fundamentals/{symbol}/history
# ---------------------------------------------------------------------------


def test_fundamentals_history_dataframe_to_index_dict(monkeypatch):
    hist = pd.DataFrame(
        {
            "as_of": ["2026-01-01", "2026-02-01"],
            "pe_ratio": [25.0, float("nan")],
            "market_cap": [3.0e12, 3.1e12],
            "raw_json": ["{}", "{}"],  # opaque blob must be dropped
        }
    )
    monkeypatch.setattr(data_api, "HistoricalStore", lambda **k: _FakeStore(fund_history=hist))
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/fundamentals/AAPL/history")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"2026-01-01", "2026-02-01"}
    assert body["2026-01-01"]["pe_ratio"] == 25.0
    assert body["2026-02-01"]["pe_ratio"] is None  # NaN → null
    assert "raw_json" not in body["2026-01-01"]


def test_fundamentals_history_empty(monkeypatch):
    monkeypatch.setattr(data_api, "HistoricalStore", lambda **k: _FakeStore(fund_history=None))
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/fundamentals/ZZZZ/history")
    assert resp.status_code == 200
    assert resp.json() == {}


# ---------------------------------------------------------------------------
# GET /data/macro
# ---------------------------------------------------------------------------


def test_macro_raw(monkeypatch):
    monkeypatch.setattr(
        data_api, "DataEngine",
        lambda key: SimpleNamespace(fetch_macro_raw=lambda: {"vix": 18.0, "sahm": float("nan")}),
    )
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/macro")
    assert resp.status_code == 200
    body = resp.json()
    assert body["vix"] == 18.0
    assert body["sahm"] is None  # NaN → null


# ---------------------------------------------------------------------------
# GET /data/macro/history
# ---------------------------------------------------------------------------


def test_macro_history_shape_and_default_series(monkeypatch):
    idx = pd.DatetimeIndex(["2026-06-01", "2026-06-02", "2026-06-03"])
    series = pd.Series([16.5, 17.2, float("nan")], index=idx, name="VIXCLS")
    monkeypatch.setattr(
        data_api, "HistoricalStore",
        lambda **k: _FakeStore(macro_series={"VIXCLS": series}),
    )
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/macro/history")  # no ?series= -> defaults to VIXCLS
    assert resp.status_code == 200
    body = resp.json()
    assert body["series_id"] == "VIXCLS"
    assert body["reason"] is None
    assert len(body["points"]) == 3
    assert body["points"][0] == {"date": "2026-06-01", "value": 16.5}
    # A real gap day (FRED didn't publish) is null, never a carried-forward value.
    assert body["points"][2]["value"] is None


def test_macro_history_series_param_selects_series(monkeypatch):
    idx = pd.DatetimeIndex(["2026-06-01"])
    monkeypatch.setattr(
        data_api, "HistoricalStore",
        lambda **k: _FakeStore(macro_series={
            "VIXCLS": pd.Series([16.0], index=idx, name="VIXCLS"),
            "T10Y2Y": pd.Series([0.4], index=idx, name="T10Y2Y"),
        }),
    )
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/macro/history?series=t10y2y")  # lowercase input
    assert resp.status_code == 200
    body = resp.json()
    assert body["series_id"] == "T10Y2Y"  # normalized uppercase
    assert body["points"][0]["value"] == 0.4


def test_macro_history_empty_returns_honest_reason(monkeypatch):
    monkeypatch.setattr(data_api, "HistoricalStore", lambda **k: _FakeStore())
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/macro/history?series=VIXCLS")
    assert resp.status_code == 200
    body = resp.json()
    assert body["points"] == []
    assert body["reason"] is not None


def test_macro_history_store_error_degrades_to_empty_not_500(monkeypatch):
    class _BoomStore:
        def get_macro(self, *a, **k):
            raise RuntimeError("db unavailable")

    monkeypatch.setattr(data_api, "HistoricalStore", lambda **k: _BoomStore())
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/macro/history")
    assert resp.status_code == 200  # dead-letter, never a 500
    assert resp.json()["points"] == []


# ---------------------------------------------------------------------------
# GET /data/sentiment/{symbol}/history
# ---------------------------------------------------------------------------


def test_sentiment_history_shape(monkeypatch):
    idx = pd.DatetimeIndex(["2026-07-01", "2026-07-02"])
    series = pd.Series([0.3, float("nan")], index=idx, name="AAPL")
    monkeypatch.setattr(
        data_api, "HistoricalStore",
        lambda **k: _FakeStore(sentiment_series={"AAPL": series}),
    )
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/sentiment/aapl/history")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["reason"] is None
    assert body["points"][0] == {"date": "2026-07-01", "score": 0.3}
    # A fetch-failure/no-headlines day is null, never a fabricated 0.0.
    assert body["points"][1]["score"] is None


def test_sentiment_history_empty_returns_honest_reason(monkeypatch):
    monkeypatch.setattr(data_api, "HistoricalStore", lambda **k: _FakeStore())
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/sentiment/ZZZZ/history")
    assert resp.status_code == 200
    body = resp.json()
    assert body["points"] == []
    assert "ZZZZ" in body["reason"]


def test_sentiment_history_store_error_degrades_to_empty_not_500(monkeypatch):
    class _BoomStore:
        def get_news_sentiment_history(self, *a, **k):
            raise RuntimeError("db unavailable")

    monkeypatch.setattr(data_api, "HistoricalStore", lambda **k: _BoomStore())
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/sentiment/AAPL/history")
    assert resp.status_code == 200
    assert resp.json()["points"] == []


# ---------------------------------------------------------------------------
# GET / PUT /data/universe
# ---------------------------------------------------------------------------


def test_get_universe_reads_default_tickers():
    with mock.patch.object(settings, "STATE_API_TOKEN", None), \
         mock.patch.object(settings, "DEFAULT_TICKERS", ["AAPL", "MSFT"]):
        resp = client.get("/data/universe")
    assert resp.status_code == 200
    assert resp.json() == {"symbols": ["AAPL", "MSFT"], "count": 2}


def test_put_universe_writes_default_tickers(monkeypatch):
    written = {}

    def _fake_write(key, value):
        written["key"] = key
        written["value"] = value
        return ".env"

    monkeypatch.setattr("gui.env_io.write_setting", _fake_write)
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.put("/data/universe", json=["aapl", " nvda ", ""])
    assert resp.status_code == 200
    assert resp.json() == {"status": "updated", "symbols": ["AAPL", "NVDA"]}
    assert written["key"] == "DEFAULT_TICKERS"
    assert written["value"] == ["AAPL", "NVDA"]


# ---------------------------------------------------------------------------
# GET /data/quotes
# ---------------------------------------------------------------------------


def test_quotes_loops_per_symbol_dead_letters_bad(monkeypatch):
    provider = _FakeProvider(quotes={"AAPL": _quote("AAPL", price=190.0)})
    monkeypatch.setattr(data_api, "get_provider", lambda: provider)
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/quotes?symbols=AAPL,BADSYM")
    assert resp.status_code == 200
    body = resp.json()
    assert "AAPL" in body and "BADSYM" not in body  # bad symbol dropped, not fatal
    assert body["AAPL"]["price"] == 190.0
    assert body["AAPL"]["is_stale"] is True
    assert body["AAPL"]["source"] == "yfinance"


def test_quotes_empty_symbols(monkeypatch):
    monkeypatch.setattr(data_api, "get_provider", lambda: _FakeProvider())
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/quotes?symbols=")
    assert resp.status_code == 200
    assert resp.json() == {}


# ---------------------------------------------------------------------------
# GET /data/sync-report
# ---------------------------------------------------------------------------


def test_sync_report(monkeypatch):
    monkeypatch.setattr(data_api, "fetch_account_snapshot", lambda force=False: object())
    monkeypatch.setattr(
        data_api, "build_sync_report",
        lambda snap: SimpleNamespace(to_dict=lambda: {"symbols": [], "generated_at": "x"}),
    )
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/sync-report")
    assert resp.status_code == 200
    assert resp.json() == {"symbols": [], "generated_at": "x"}


def test_sync_report_tolerates_missing_snapshot(monkeypatch):
    called = {}

    def _fetch(force=False):
        raise RuntimeError("no robinhood creds")

    def _build(snap):
        called["snap"] = snap
        return SimpleNamespace(to_dict=lambda: {"symbols": []})

    monkeypatch.setattr(data_api, "fetch_account_snapshot", _fetch)
    monkeypatch.setattr(data_api, "build_sync_report", _build)
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/sync-report")
    assert resp.status_code == 200
    assert called["snap"] is None  # degraded to None, still built a report


# ---------------------------------------------------------------------------
# GET /data/account
# ---------------------------------------------------------------------------


def test_account_snapshot(monkeypatch):
    snap = SimpleNamespace(to_dict=lambda: {"total_equity": 12345.0, "positions": {}})
    monkeypatch.setattr(data_api, "fetch_account_snapshot", lambda force=False: snap)
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/account")
    assert resp.status_code == 200
    assert resp.json()["total_equity"] == 12345.0


def test_account_404_on_cold_state(monkeypatch):
    monkeypatch.setattr(data_api, "fetch_account_snapshot", lambda force=False: None)
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/account")
    assert resp.status_code == 404
