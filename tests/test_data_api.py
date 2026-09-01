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

import math
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from api import data_api
from data.market_data import MarketDataError
from settings import settings

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

    def get_quotes_batch(self, symbols):
        """Mirrors MarketDataProvider's real ABC default (a per-symbol
        get_latest_quote loop, dead-lettering a failure) -- this fake is not
        a real subclass of the ABC, so it doesn't inherit that default and
        needs its own copy to stay a faithful stand-in."""
        out = {}
        for sym in symbols:
            try:
                out[sym] = self.get_latest_quote(sym)
            except Exception:
                continue
        return out


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


def test_bars_lookback_days_is_bounded(monkeypatch):
    """``lookback_days`` must be rejected (422) outside [1, 3650] — matching
    the ``/data/macro/{series_id}`` and ``/data/sentiment/history/{symbol}``
    siblings' existing ``Query(..., ge=1, le=3650)`` bound. An unbounded
    value here would eventually reach a multi-decade FMP fetch, silently
    truncated by the undocumented ~5,000-row-per-request cap (see
    ``docs/FMP_INTEGRATION.md``'s Known Risks section) rather than erroring —
    this bound closes that gap defensively before it ever reaches the fetch
    layer."""
    monkeypatch.setattr(data_api, "HistoricalStore", lambda **k: _FakeStore(bars=_make_bars(3)))
    monkeypatch.setattr(data_api, "get_provider", lambda: _FakeProvider())
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        too_large = client.get("/data/bars/AAPL?lookback_days=7300")
        too_small = client.get("/data/bars/AAPL?lookback_days=0")
        in_bounds = client.get("/data/bars/AAPL?lookback_days=3650")
    assert too_large.status_code == 422
    assert too_small.status_code == 422
    assert in_bounds.status_code == 200


# ---------------------------------------------------------------------------
# POST /data/backfill/{symbol}
# ---------------------------------------------------------------------------


def test_backfill_requires_write_token_even_when_unset(monkeypatch):
    """Unlike GET /data/bars/{symbol}, this WRITES to local storage (a
    write-mode HistoricalStore, no readonly=True) -- require_write_token,
    fail CLOSED when STATE_API_TOKEN is unset, matching PUT /data/universe's
    posture rather than the fail-open GET siblings."""
    monkeypatch.setattr(data_api, "HistoricalStore", lambda **k: _FakeStore(bars=_make_bars(3)))
    monkeypatch.setattr(data_api, "get_provider", lambda: _FakeProvider())
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.post("/data/backfill/AAPL")
    assert resp.status_code == 403


def test_backfill_happy_path(monkeypatch):
    captured = {}

    class _WriteModeStore(_FakeStore):
        def get_bars(self, symbol, lookback_days=252, provider=None):
            captured["symbol"] = symbol
            captured["lookback_days"] = lookback_days
            return super().get_bars(symbol, lookback_days=lookback_days, provider=provider)

    monkeypatch.setattr(data_api, "HistoricalStore", lambda **k: _WriteModeStore(bars=_make_bars(5)))
    monkeypatch.setattr(data_api, "get_provider", lambda: _FakeProvider())
    with mock.patch.object(settings, "STATE_API_TOKEN", "secret"), \
         mock.patch.object(settings, "BARS_BACKFILL_DAYS", 504):
        resp = client.post("/data/backfill/aapl", headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["rows_persisted"] == 5
    assert body["status"] == "ok"
    assert body["last_bar_date"] == "2026-01-05"
    assert captured["symbol"] == "AAPL"
    assert captured["lookback_days"] == 504


def test_backfill_unknown_symbol_returns_no_data_not_500(monkeypatch):
    """CONSTRAINT #4/#6: an unfetchable symbol is an honest 200/no_data, never
    a fabricated success and never a 500."""
    monkeypatch.setattr(data_api, "HistoricalStore", lambda **k: _FakeStore(bars=None))
    monkeypatch.setattr(data_api, "get_provider", lambda: _FakeProvider())
    with mock.patch.object(settings, "STATE_API_TOKEN", "secret"):
        resp = client.post("/data/backfill/ZZZZ", headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 200
    assert resp.json() == {
        "symbol": "ZZZZ", "rows_persisted": 0, "last_bar_date": None, "status": "no_data",
    }


def test_backfill_store_exception_dead_letters_to_no_data(monkeypatch):
    class _BoomStore:
        def get_bars(self, symbol, lookback_days=252, provider=None):
            raise RuntimeError("DB unavailable")

    monkeypatch.setattr(data_api, "HistoricalStore", lambda **k: _BoomStore())
    monkeypatch.setattr(data_api, "get_provider", lambda: _FakeProvider())
    with mock.patch.object(settings, "STATE_API_TOKEN", "secret"):
        resp = client.post("/data/backfill/AAPL", headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_data"


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
    monkeypatch.setattr("env_io.write_setting", lambda key, value: ".env")
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.put("/data/universe", json=["aapl", " nvda ", ""])
    assert resp.status_code == 403


def test_put_universe_writes_default_tickers(monkeypatch):
    written = {}

    def _fake_write(key, value):
        written["key"] = key
        written["value"] = value
        return ".env"

    monkeypatch.setattr("env_io.write_setting", _fake_write)
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


def test_quotes_batch_dead_letters_bad_symbol(monkeypatch):
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


def test_quotes_batch_provider_outage_degrades_to_empty_not_500(monkeypatch):
    """Before F6 (docs/module_efficiency_redundancy_audit.md), this endpoint
    looped per symbol with its own try/except, so a total provider outage
    could never surface past this endpoint as a 500 -- every symbol was
    simply dropped one at a time. Migrating to a single
    get_quotes_batch(sym_list) call collapsed that per-symbol try/except
    into one call site; without an equivalent wrapper around it, a raising
    provider now propagates straight through FastAPI as a 500 instead of
    degrading to {} like every other failure mode this endpoint handles.
    Regression guard for that gap."""

    class _RaisingBatchProvider:
        def get_quotes_batch(self, symbols):
            raise MarketDataError("provider unreachable")

    monkeypatch.setattr(data_api, "get_provider", lambda: _RaisingBatchProvider())
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/quotes?symbols=AAPL,NVDA")
    assert resp.status_code == 200
    assert resp.json() == {}


# ---------------------------------------------------------------------------
# GET /data/sync-report
# ---------------------------------------------------------------------------


def test_sync_report(monkeypatch):
    monkeypatch.setattr(data_api, "fetch_account_snapshot", lambda force=False: object())
    monkeypatch.setattr(
        data_api, "build_sync_report",
        lambda snap, **kwargs: SimpleNamespace(to_dict=lambda: {"symbols": [], "generated_at": "x"}),
    )
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/sync-report")
    assert resp.status_code == 200
    assert resp.json() == {"symbols": [], "generated_at": "x"}


def test_sync_report_tolerates_missing_snapshot(monkeypatch):
    called = {}

    def _fetch(force=False):
        raise RuntimeError("no robinhood creds")

    def _build(snap, **kwargs):
        called["snap"] = snap
        return SimpleNamespace(to_dict=lambda: {"symbols": []})

    monkeypatch.setattr(data_api, "fetch_account_snapshot", _fetch)
    monkeypatch.setattr(data_api, "build_sync_report", _build)
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/sync-report")
    assert resp.status_code == 200
    assert called["snap"] is None  # degraded to None, still built a report


def test_sync_report_forecast_available_reflects_real_forecast_tracker(monkeypatch, tmp_path):
    """End-to-end regression test: real ForecastTracker (temp SQLite DB) +
    real (unmocked) build_sync_report, only the account snapshot and the
    market-data provider are faked. A held symbol with a real, recent
    forecast row must come back forecast_available=True in the actual HTTP
    response; a held symbol with none must come back False.

    This is the regression test for the 2026-08 bug where
    ForecastTracker.get_covered_symbols() queried a nonexistent 'forecasts'
    table and referenced a nonexistent self.readonly attribute in its
    finally block -- both errors were silently swallowed by
    get_sync_report()'s bare except Exception, so forecast_symbols was
    ALWAYS [] and forecast_available was ALWAYS False for every symbol
    regardless of real forecast coverage. A test that only mocks
    build_sync_report (like test_sync_report above) cannot catch this --
    the bug lives entirely in the ForecastTracker call this test does NOT
    mock.
    """
    import forecasting.forecast_tracker as ft_mod
    from forecasting.forecast_tracker import MODEL_ARIMA

    db_path = str(tmp_path / "forecast_test.db")
    seed_tracker = ft_mod.ForecastTracker(db_path=db_path)
    seed_tracker.record_forecasts("AAPL", 30, {MODEL_ARIMA: 150.0}, datetime.now(timezone.utc))
    # MSFT deliberately gets no forecast rows at all.

    class _TempDbTracker(ft_mod.ForecastTracker):
        """Stands in for the real ForecastTracker but always resolves to this
        test's isolated temp DB -- get_sync_report() constructs it with no
        arguments (`ForecastTracker()`), so the default db_path must be
        overridden here rather than passed at the call site."""

        def __init__(self, *args, **kwargs):
            kwargs.setdefault("db_path", db_path)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(ft_mod, "ForecastTracker", _TempDbTracker)

    held = {
        "AAPL": SimpleNamespace(
            symbol="AAPL", quantity=10.0, average_cost=150.0,
            current_price=175.0, market_value=1_750.0, unrealized_pl=250.0,
        ),
        "MSFT": SimpleNamespace(
            symbol="MSFT", quantity=5.0, average_cost=300.0,
            current_price=320.0, market_value=1_600.0, unrealized_pl=100.0,
        ),
    }
    fake_snapshot = SimpleNamespace(positions=held)
    monkeypatch.setattr(data_api, "fetch_account_snapshot", lambda force=False: fake_snapshot)

    # Skip the market-data probe entirely (irrelevant to this test) by making
    # get_provider() fail -- build_sync_report degrades that to
    # CoverageStatus.UNKNOWN for every symbol rather than raising.
    import data.market_data as md
    monkeypatch.setattr(
        md, "get_provider",
        lambda: (_ for _ in ()).throw(RuntimeError("no market-data provider in test")),
    )

    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/sync-report")

    assert resp.status_code == 200
    body = resp.json()["symbols"]
    assert body["AAPL"]["forecast_available"] is True
    assert body["MSFT"]["forecast_available"] is False


# ---------------------------------------------------------------------------
# GET /data/sync-report -- symbol-rating enrichment (rating_consecutive_bad_cycles /
# rating_excluded, sourced from rating.symbol_rating_store.SymbolRatingStore)
# ---------------------------------------------------------------------------


class _FakeRatingStore:
    """Stand-in for rating.symbol_rating_store.SymbolRatingStore -- records
    the readonly flag it was constructed with and returns a scripted
    consecutive-bad-cycle count per symbol."""

    def __init__(self, *args, readonly: bool = False, **kwargs):
        self.readonly = readonly

    def get_consecutive_bad_cycles(self, symbol: str) -> int:
        return {"AAPL": 0, "XOM": 7, "T": 2}.get(symbol.upper(), 0)


def test_sync_report_includes_rating_fields(monkeypatch):
    symbols = {
        "AAPL": {"symbol": "AAPL", "held": True, "coverage": "full"},
        # Not held, streak (7) >= default threshold (5) -> excluded.
        "XOM": {"symbol": "XOM", "held": False, "coverage": "uncovered"},
        # Not held, streak (2) < threshold -> not excluded.
        "T": {"symbol": "T", "held": False, "coverage": "uncovered"},
    }
    monkeypatch.setattr(data_api, "fetch_account_snapshot", lambda force=False: object())
    monkeypatch.setattr(
        data_api, "build_sync_report",
        lambda snap, **kwargs: SimpleNamespace(to_dict=lambda: {"symbols": symbols, "generated_at": "x"}),
    )
    monkeypatch.setattr(
        "rating.symbol_rating_store.SymbolRatingStore", _FakeRatingStore,
    )
    with mock.patch.object(settings, "STATE_API_TOKEN", None), \
         mock.patch.object(settings, "SYMBOL_RATING_DROP_THRESHOLD_CYCLES", 5):
        resp = client.get("/data/sync-report")
    assert resp.status_code == 200
    body = resp.json()["symbols"]

    # Held symbol: never excluded regardless of streak.
    assert body["AAPL"]["rating_consecutive_bad_cycles"] == 0
    assert body["AAPL"]["rating_excluded"] is False

    # Not held, streak >= threshold -> excluded.
    assert body["XOM"]["rating_consecutive_bad_cycles"] == 7
    assert body["XOM"]["rating_excluded"] is True

    # Not held, streak < threshold -> not excluded.
    assert body["T"]["rating_consecutive_bad_cycles"] == 2
    assert body["T"]["rating_excluded"] is False


def test_sync_report_rating_enrichment_degrades_gracefully(monkeypatch):
    """A SymbolRatingStore failure (import error, DB outage, etc.) must never
    500 the whole endpoint (CONSTRAINT #6) -- the base sync-report payload
    still returns, just without the two rating keys on each symbol."""
    symbols = {"AAPL": {"symbol": "AAPL", "held": True, "coverage": "full"}}
    monkeypatch.setattr(data_api, "fetch_account_snapshot", lambda force=False: object())
    monkeypatch.setattr(
        data_api, "build_sync_report",
        lambda snap, **kwargs: SimpleNamespace(to_dict=lambda: {"symbols": symbols, "generated_at": "x"}),
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr("rating.symbol_rating_store.SymbolRatingStore", _boom)
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/sync-report")
    assert resp.status_code == 200
    body = resp.json()["symbols"]
    assert body["AAPL"]["symbol"] == "AAPL"
    assert "rating_consecutive_bad_cycles" not in body["AAPL"]
    assert "rating_excluded" not in body["AAPL"]


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


# TestCORSLanTailscale (the LAN/Tailscale-origin reflection contract) lives
# in tests/test_cors_lan_tailscale_contract.py, shared byte-for-byte with
# control_api/metrics_api/pilots_api/state_api's identical versions of this test.


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
    def test_universe_sync_enabled_is_gui_writable(self):
        """UNIVERSE_SYNC_ENABLED was previously a hand-set-only invariant (like
        the other *_WRITES_ENABLED flags in api/pilots_api.py) but was made
        GUI-writable by operator decision. It must stay a non-secret allowlisted
        key (POST /data/sync remains gated independently by STATE_API_TOKEN via
        require_write_token, so this flag's own writability is not the sole
        safeguard)."""
        from gui import env_io

        assert "UNIVERSE_SYNC_ENABLED" in env_io.ALLOWED_KEYS
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


def _all_route_paths(app) -> set:
    """Recursively collect every route path served by *app* -- see the
    identically-named helper's docstring in test_control_api.py for why a
    plain top-level scan of ``app.routes`` is not sufficient in the FastAPI/
    Starlette version this repo pins."""
    paths: set = set()
    stack = list(app.routes)
    while stack:
        route = stack.pop()
        path = getattr(route, "path", None)
        if path:
            paths.add(path)
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            stack.extend(original_router.routes)
    return paths


def test_mounts_tick_ws_route_but_not_the_unrelated_training_status_route():
    """Route-bleed regression guard, mirroring
    test_control_api.py::test_mounts_training_status_ws_route_but_not_the_unrelated_tick_route.
    This app correctly serves /ws/ticks/{symbol} (its own live-market-tick
    capability) but must NOT also serve /ws/training/status -- that route's
    broadcast singletons (training_status_manager/_MAIN_LOOP) are only ever
    populated by api/control_api.py's own startup hook and create_job/
    stream_job_logs call sites, so a copy mounted here could never broadcast
    anything. Both routers used to be one shared ``ws_router`` that any
    mounting process pulled in whole; see api/ws_api.py's docstring."""
    paths = _all_route_paths(data_api.app)
    assert "/ws/ticks/{symbol}" in paths
    assert "/ws/training/status" not in paths


class TestCircuitBreakerStatus:
    """Tests for GET /risk/circuit-breaker/status."""

    def test_degrades_to_normal_when_uninitialized(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        resp = client.get("/risk/circuit-breaker/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "NORMAL"
        assert data["volatility_zscore"] == 0.0
        assert data["vpin"] == 0.0
        assert data["ofi"] == 0.0
        assert data["loss_velocity_per_min"] == 0.0
        assert data["reason"] is None
        assert "updated_at" in data

    def test_reads_persisted_circuit_breaker_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        cb_file = tmp_path / "circuit_breaker_state.json"
        import json
        cb_file.write_text(
            json.dumps({
                "state": "SOFT_HALT",
                "volatility_zscore": 3.82,
                "vpin": 0.46,
                "ofi": -1250.0,
                "loss_velocity_per_min": -210.0,
                "reason": "VOLATILITY_BURST_HALT: 5m EWMA realized vol Z-score 3.82 > 3.50",
                "updated_at": "2026-08-17T12:00:00Z",
            }),
            encoding="utf-8",
        )

        resp = client.get("/risk/circuit-breaker/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "SOFT_HALT"
        assert data["volatility_zscore"] == 3.82
        assert data["vpin"] == 0.46
        assert data["ofi"] == -1250.0
        assert data["loss_velocity_per_min"] == -210.0
        assert data["reason"] == "VOLATILITY_BURST_HALT: 5m EWMA realized vol Z-score 3.82 > 3.50"
        assert data["updated_at"] == "2026-08-17T12:00:00Z"

    def test_auth_read_token(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        with mock.patch.object(settings, "STATE_API_TOKEN", "secret-tok"):
            resp = client.get(
                "/risk/circuit-breaker/status",
                headers={"Authorization": "Bearer wrong-tok"},
            )
            assert resp.status_code == 401

            resp_ok = client.get(
                "/risk/circuit-breaker/status",
                headers={"Authorization": "Bearer secret-tok"},
            )
            assert resp_ok.status_code == 200
            assert resp_ok.json()["state"] == "NORMAL"


# ---------------------------------------------------------------------------
# GET /data/trends/stitch-demo
# ---------------------------------------------------------------------------


def _make_stitch_demo_bars(n: int = 260) -> pd.DataFrame:
    """Realistic-enough SPY-bar fixture for the stitch-demo endpoint: a real
    tz-naive DatetimeIndex (the endpoint relies on this for epoch-ms
    conversion) and a Volume column with varying, non-degenerate values so
    the ``sum_b <= 1e-9`` degenerate-scaling guard in
    ``GoogleTrendsStitcher.get_scaling_metadata`` never trips and the real
    scaling math is actually exercised."""
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    rng = np.random.default_rng(7)
    # Trend + noise, all strictly positive -- never a flat/constant series.
    volume = 1_000_000.0 + np.arange(n) * 500.0 + rng.normal(0, 50_000, size=n)
    volume = np.clip(volume, 100_000.0, None)
    return pd.DataFrame(
        {
            "Open": np.linspace(400, 450, n),
            "High": np.linspace(401, 451, n),
            "Low": np.linspace(399, 449, n),
            "Close": np.linspace(400.5, 450.5, n),
            "Volume": volume,
        },
        index=idx,
    )


class _FakeStoreBars:
    """Minimal HistoricalStore stand-in exposing only ``get_bars`` -- the one
    method the stitch-demo endpoint calls."""

    def __init__(self, bars: pd.DataFrame):
        self._bars = bars

    def get_bars(self, symbol, lookback_days=252, provider=None):
        return self._bars


def test_get_trends_stitch_demo_happy_path(monkeypatch):
    bars = _make_stitch_demo_bars(260)
    monkeypatch.setattr(data_api, "HistoricalStore", lambda **k: _FakeStoreBars(bars))
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/trends/stitch-demo")
    assert resp.status_code == 200
    body = resp.json()

    assert set(body.keys()) == {"raw_curves", "stitched_curve"}

    raw_curves = body["raw_curves"]
    assert isinstance(raw_curves, list)
    assert len(raw_curves) == 3
    for curve in raw_curves:
        assert set(curve.keys()) == {"name", "data"}
        # Honest labeling: never claims to be real Google Trends data.
        assert "SPY Volume Proxy" in curve["name"]
        assert "Google Trends" not in curve["name"]
        assert isinstance(curve["data"], list)
        assert len(curve["data"]) > 0
        for point in curve["data"]:
            assert len(point) == 2
            ts_ms, value = point
            assert isinstance(ts_ms, int)
            assert ts_ms > 0
            assert value is not None
            assert not (isinstance(value, float) and math.isnan(value))

    stitched = body["stitched_curve"]
    assert set(stitched.keys()) == {"name", "data"}
    assert "SPY Volume Proxy" in stitched["name"]
    assert isinstance(stitched["data"], list)
    assert len(stitched["data"]) > 0
    for point in stitched["data"]:
        ts_ms, value = point
        assert isinstance(ts_ms, int)
        assert value is not None
        assert not (isinstance(value, float) and math.isnan(value))

    # Stitched series should span (roughly) the union of periods A/B/C, i.e.
    # materially more points than any single one of the three raw curves.
    assert len(stitched["data"]) > len(raw_curves[0]["data"])


def test_get_trends_stitch_demo_insufficient_history_degrades_to_503_not_fabricated(monkeypatch):
    # Fewer than the required 240 bars -- the endpoint must refuse to build
    # the demo rather than proceed on a too-short window.
    bars = _make_stitch_demo_bars(50)
    monkeypatch.setattr(data_api, "HistoricalStore", lambda **k: _FakeStoreBars(bars))
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/trends/stitch-demo")
    assert resp.status_code == 503
    body = resp.json()
    assert "detail" in body
    # No fabricated/placeholder series is ever returned alongside the error.
    assert "raw_curves" not in body
    assert "stitched_curve" not in body


def test_get_trends_stitch_demo_empty_bars_degrades_to_503(monkeypatch):
    monkeypatch.setattr(data_api, "HistoricalStore", lambda **k: _FakeStoreBars(pd.DataFrame()))
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/trends/stitch-demo")
    assert resp.status_code == 503
    body = resp.json()
    assert "raw_curves" not in body
    assert "stitched_curve" not in body


def test_get_trends_stitch_demo_generic_exception_degrades_to_503_not_500(monkeypatch):
    class _BoomStore:
        def get_bars(self, *a, **k):
            raise RuntimeError("db locked")

    monkeypatch.setattr(data_api, "HistoricalStore", lambda **k: _BoomStore())
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/trends/stitch-demo")
    assert resp.status_code == 503  # dead-letter, never a raw 500
    body = resp.json()
    assert "raw_curves" not in body
    assert "stitched_curve" not in body
