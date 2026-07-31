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

# Starlette's TestClient defaults request.client.host to the literal
# string "testclient" -- NOT loopback -- which would trip
# api.auth.require_read_token's new fail-closed-when-non-loopback branch
# on every one of this file's existing zero-config-behavior assertions.
# An explicit loopback host here is what these tests have always meant.
client = TestClient(data_api.app, client=("127.0.0.1", 54123))


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


def test_put_universe_requires_token_even_when_unset(monkeypatch):
    """PUT /data/universe actually mutates .env (DEFAULT_TICKERS), unlike the
    GET endpoints on this API — it uses require_write_token, which fails
    CLOSED when STATE_API_TOKEN is unset (the opposite of every read
    endpoint's fail-open default)."""
    monkeypatch.setattr("gui.env_io.write_setting", lambda key, value: ".env")
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.put("/data/universe", json=["aapl", " nvda ", ""])
    assert resp.status_code == 403


def test_put_universe_writes_default_tickers(monkeypatch):
    written = {}

    def _fake_write(key, value):
        written["key"] = key
        written["value"] = value
        return ".env"

    monkeypatch.setattr("gui.env_io.write_setting", _fake_write)
    with mock.patch.object(settings, "STATE_API_TOKEN", "secret"):
        resp = client.put(
            "/data/universe",
            json=["aapl", " nvda ", ""],
            headers={"Authorization": "Bearer secret"},
        )
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


class TestCORSLanTailscale:
    """LAN/Tailscale origins are allowed via api.cors.LAN_TAILSCALE_ORIGIN_REGEX
    (additive to the explicit CORS_ALLOWED_ORIGINS list), scoped to the Pilots
    PWA dev server's port (5173, per webapp/vite.config.ts's
    ``server: { host: true, port: 5173 }``)."""

    def test_lan_origin_is_reflected(self):
        resp = client.get("/health", headers={"Origin": "http://192.168.1.42:5173"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "http://192.168.1.42:5173"

    def test_tailscale_range_origin_is_reflected(self):
        resp = client.get("/health", headers={"Origin": "http://100.101.102.5:5173"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "http://100.101.102.5:5173"

    def test_lan_origin_wrong_port_not_reflected(self):
        resp = client.get("/health", headers={"Origin": "http://192.168.1.42:5174"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") != "http://192.168.1.42:5174"

    def test_public_ip_not_reflected(self):
        resp = client.get("/health", headers={"Origin": "http://8.8.8.8:5173"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") != "http://8.8.8.8:5173"


# ===========================================================================
# POST /data/sync + GET /data/provider-status (webapp parity gaps G8/G9).
# Appended at the end of the file per this repo's multi-agent collision
# protocol (other agents append their own new test classes elsewhere in this
# same file concurrently on separate branches).
# ===========================================================================


class TestDataSyncWrite:
    """POST /data/sync — fail-closed require_write_token STACKED with the
    dedicated UNIVERSE_SYNC_ENABLED master flag."""

    async def _fake_async_sync_now(self, snapshot, **kwargs):
        return SimpleNamespace(
            symbols={"AAPL": object(), "NVDA": object()},
            to_dict=lambda: {"symbols": ["AAPL", "NVDA"], "generated_at": "x"},
        )

    def test_fails_closed_when_universe_sync_disabled(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", "secret"):
            with mock.patch.object(settings, "UNIVERSE_SYNC_ENABLED", False):
                resp = client.post(
                    "/data/sync", headers={"Authorization": "Bearer secret"}
                )
        assert resp.status_code == 403

    def test_fails_closed_when_state_api_token_unset(self):
        """Unlike a fail-open GET, POST /data/sync uses require_write_token,
        which fails CLOSED when STATE_API_TOKEN is unset -- mirrors
        PUT /data/universe's existing posture."""
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(settings, "UNIVERSE_SYNC_ENABLED", True):
                resp = client.post(
                    "/data/sync", headers={"Authorization": "Bearer anything"}
                )
        assert resp.status_code == 403

    def test_401_on_wrong_token(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", "secret"):
            with mock.patch.object(settings, "UNIVERSE_SYNC_ENABLED", True):
                resp = client.post(
                    "/data/sync", headers={"Authorization": "Bearer WRONG"}
                )
        assert resp.status_code == 401

    def test_happy_path_calls_async_sync_now_and_echoes(self, monkeypatch):
        monkeypatch.setattr(data_api, "fetch_account_snapshot", lambda force=False: object())
        monkeypatch.setattr(data_api, "load_snapshot", lambda: {"signals": []})
        monkeypatch.setattr(data_api, "async_sync_now", self._fake_async_sync_now)
        with mock.patch.object(settings, "STATE_API_TOKEN", "secret"):
            with mock.patch.object(settings, "UNIVERSE_SYNC_ENABLED", True):
                resp = client.post(
                    "/data/sync", headers={"Authorization": "Bearer secret"}
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["default_tickers"] == ["AAPL", "NVDA"]
        assert body["report"] == {"symbols": ["AAPL", "NVDA"], "generated_at": "x"}
        assert body["applies"] == "next_daemon_restart"

    def test_never_forces_a_live_login(self, monkeypatch):
        """fetch_account_snapshot must always be called with force=False —
        force=True can block on interactive MFA stdin, unsafe inside a
        headless HTTP request handler."""
        captured = {}

        def _fetch(force=False):
            captured["force"] = force
            return object()

        monkeypatch.setattr(data_api, "fetch_account_snapshot", _fetch)
        monkeypatch.setattr(data_api, "load_snapshot", lambda: {"signals": []})
        monkeypatch.setattr(data_api, "async_sync_now", self._fake_async_sync_now)
        with mock.patch.object(settings, "STATE_API_TOKEN", "secret"):
            with mock.patch.object(settings, "UNIVERSE_SYNC_ENABLED", True):
                client.post("/data/sync", headers={"Authorization": "Bearer secret"})
        assert captured["force"] is False

    def test_tolerates_missing_account_snapshot(self, monkeypatch):
        def _fetch(force=False):
            raise RuntimeError("no robinhood creds")

        called = {}

        async def _sync(snapshot, **kwargs):
            called["snapshot"] = snapshot
            return SimpleNamespace(symbols={}, to_dict=lambda: {"symbols": []})

        monkeypatch.setattr(data_api, "fetch_account_snapshot", _fetch)
        monkeypatch.setattr(data_api, "load_snapshot", lambda: {"signals": []})
        monkeypatch.setattr(data_api, "async_sync_now", _sync)
        with mock.patch.object(settings, "STATE_API_TOKEN", "secret"):
            with mock.patch.object(settings, "UNIVERSE_SYNC_ENABLED", True):
                resp = client.post(
                    "/data/sync", headers={"Authorization": "Bearer secret"}
                )
        assert resp.status_code == 200
        assert called["snapshot"] is None  # degraded to None, never raised

    def test_sync_failure_returns_503_never_500(self, monkeypatch):
        async def _boom(snapshot, **kwargs):
            raise RuntimeError("provider outage")

        monkeypatch.setattr(data_api, "fetch_account_snapshot", lambda force=False: object())
        monkeypatch.setattr(data_api, "load_snapshot", lambda: {"signals": []})
        monkeypatch.setattr(data_api, "async_sync_now", _boom)
        with mock.patch.object(settings, "STATE_API_TOKEN", "secret"):
            with mock.patch.object(settings, "UNIVERSE_SYNC_ENABLED", True):
                resp = client.post(
                    "/data/sync", headers={"Authorization": "Bearer secret"}
                )
        assert resp.status_code == 503

    def test_write_never_logs_token(self, monkeypatch, caplog):
        monkeypatch.setattr(data_api, "fetch_account_snapshot", lambda force=False: object())
        monkeypatch.setattr(data_api, "load_snapshot", lambda: {"signals": []})
        monkeypatch.setattr(data_api, "async_sync_now", self._fake_async_sync_now)
        with caplog.at_level("DEBUG"):
            with mock.patch.object(settings, "STATE_API_TOKEN", "secret"):
                with mock.patch.object(settings, "UNIVERSE_SYNC_ENABLED", True):
                    client.post("/data/sync", headers={"Authorization": "Bearer secret"})
        assert "secret" not in caplog.text


class TestUniverseSyncInvariants:
    def test_universe_sync_enabled_is_not_gui_writable(self):
        """Mirrors the other *_WRITES_ENABLED invariants in api/pilots_api.py:
        a GUI bug must never flip this on. Neither allowlisted nor secret —
        hand-set only."""
        import gui.env_io as env_io

        assert "UNIVERSE_SYNC_ENABLED" not in env_io.ALLOWED_KEYS
        assert "UNIVERSE_SYNC_ENABLED" not in env_io.SECRET_KEYS


class TestProviderStatus:
    """GET /data/provider-status — fail-open read."""

    def test_shape_and_values(self, monkeypatch):
        provider = SimpleNamespace(
            quote_source="alpaca", is_realtime=True, source_name="yahoo_computed",
        )
        monkeypatch.setattr(data_api, "get_provider", lambda: provider)
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(settings, "MARKET_DATA_QUOTE_TTL_SECONDS", 45):
                resp = client.get("/data/provider-status")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "provider": "alpaca",
            "is_realtime": True,
            "mode": "real_time",
            "quote_ttl_seconds": 45,
            "fundamentals_source": "yahoo_computed",
        }

    def test_delayed_mode_for_non_realtime_provider(self, monkeypatch):
        provider = SimpleNamespace(
            quote_source="yfinance", is_realtime=False, source_name="yahoo_computed",
        )
        monkeypatch.setattr(data_api, "get_provider", lambda: provider)
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/data/provider-status")
        body = resp.json()
        assert body["mode"] == "delayed"
        assert body["is_realtime"] is False

    def test_fail_open_read_with_no_token(self, monkeypatch):
        monkeypatch.setattr(data_api, "get_provider", lambda: SimpleNamespace(
            quote_source="yfinance", is_realtime=False, source_name="yahoo_computed",
        ))
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/data/provider-status")
        assert resp.status_code == 200

    def test_401_on_wrong_read_token(self, monkeypatch):
        monkeypatch.setattr(data_api, "get_provider", lambda: SimpleNamespace(
            quote_source="yfinance", is_realtime=False, source_name="yahoo_computed",
        ))
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            resp = client.get(
                "/data/provider-status", headers={"Authorization": "Bearer wrong"}
            )
        assert resp.status_code == 401
