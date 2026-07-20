"""
tests/test_data_api_options_pairs.py
======================================
Fully-offline tests for the three on-demand recompute endpoints added to
``api/data_api.py`` (webapp porting backlog items 8a/8b):

  * ``POST /data/pairs/analyze``  — one named pair
  * ``POST /data/pairs/scan``     — cointegration scan over a symbol list
  * ``POST /data/options/recompute`` — premium-directive recompute

``get_provider`` and ``load_snapshot`` are monkeypatched on the
``api.data_api`` module namespace (mirrors ``tests/test_data_api_ai.py``'s
convention) — no real network/provider call, no real ``output/
state_snapshot.json`` read happens here. ``pairs_ondemand``/``options_ondemand``
functions themselves are unit-tested independently in
``tests/test_pairs_ondemand.py``/``tests/test_options_ondemand.py``; these
tests focus on the HTTP contract: auth, request validation (422 stable tags),
and correct wiring of the provider/snapshot into the underlying compute.
"""
from __future__ import annotations

from dataclasses import dataclass
from unittest import mock

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from settings import settings
import api.data_api as data_api

client = TestClient(data_api.app)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _cointegrated_frame(n: int = 252, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    x = np.cumsum(rng.normal(0, 1, n)) + 100
    spread = [0.0]
    for _ in range(n - 1):
        spread.append(0.9 * spread[-1] + rng.normal(0, 0.5))
    spread = np.array(spread)
    y = 0.5 * x + 10.0 + spread
    return pd.DataFrame({"Y": y, "X": x}, index=idx)


def _synthetic_option_bars(n: int = 252, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0005, 0.012, size=n)
    close = 100 * np.exp(np.cumsum(returns))
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {
            "Open": close * (1 - 0.001),
            "High": close * (1 + 0.005),
            "Low": close * (1 - 0.005),
            "Close": close,
            "Volume": rng.integers(1_000_000, 5_000_000, size=n),
        },
        index=idx,
    )


@dataclass
class _FakeQuote:
    price: float
    is_stale: bool = False


class _FakeProvider:
    """Serves both the pairs Close-series path and the options
    quote+bars path from the same fixture object."""

    def __init__(self, frame: pd.DataFrame = None, bars=None, quote=None):
        self._frame = frame
        self._bars = bars
        self._quote = quote

    def get_intraday_bars(self, symbol: str, lookback_days: int = 252):
        if self._frame is not None:
            if symbol not in self._frame.columns:
                return pd.DataFrame()
            return pd.DataFrame({"Close": self._frame[symbol].tail(lookback_days)})
        return self._bars

    def get_latest_quote(self, symbol: str):
        return self._quote


def _no_token():
    return mock.patch.object(settings, "STATE_API_TOKEN", None)


# ---------------------------------------------------------------------------
# Auth (require_token) — mirrors the existing fail-open/fail-closed posture
# ---------------------------------------------------------------------------


def test_pairs_analyze_401_with_wrong_token():
    with mock.patch.object(settings, "STATE_API_TOKEN", "secret"):
        resp = client.post(
            "/data/pairs/analyze",
            json={"symbol_y": "Y", "symbol_x": "X"},
            headers={"Authorization": "Bearer nope"},
        )
    assert resp.status_code == 401


def test_pairs_scan_401_missing_token():
    with mock.patch.object(settings, "STATE_API_TOKEN", "secret"):
        resp = client.post("/data/pairs/scan", json={"symbols": ["Y", "X"]})
    assert resp.status_code == 401


def test_options_recompute_401_with_wrong_token():
    with mock.patch.object(settings, "STATE_API_TOKEN", "secret"):
        resp = client.post(
            "/data/options/recompute",
            json={"symbols": ["AAPL"]},
            headers={"Authorization": "Bearer nope"},
        )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /data/pairs/analyze
# ---------------------------------------------------------------------------


def test_pairs_analyze_missing_symbol_is_422_with_stable_tag():
    with _no_token():
        resp = client.post("/data/pairs/analyze", json={"symbol_y": "  ", "symbol_x": "X"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "missing_symbol"


def test_pairs_analyze_identical_symbols_is_422_with_stable_tag():
    with _no_token():
        resp = client.post("/data/pairs/analyze", json={"symbol_y": "aapl", "symbol_x": "AAPL"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "identical_symbols"


def test_pairs_analyze_success(monkeypatch):
    frame = _cointegrated_frame()
    monkeypatch.setattr(data_api, "get_provider", lambda: _FakeProvider(frame=frame))
    with _no_token():
        resp = client.post("/data/pairs/analyze", json={"symbol_y": "y", "symbol_x": "x"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert body["ticker1"] == "Y"
    assert body["ticker2"] == "X"
    assert body["z_score_series"]


def test_pairs_analyze_insufficient_history_is_honest_200_not_error(monkeypatch):
    idx = pd.date_range("2024-01-01", periods=10, freq="D")
    frame = pd.DataFrame(
        {"Y": np.linspace(100, 101, 10), "X": np.linspace(50, 50.5, 10)}, index=idx
    )
    monkeypatch.setattr(data_api, "get_provider", lambda: _FakeProvider(frame=frame))
    with _no_token():
        resp = client.post("/data/pairs/analyze", json={"symbol_y": "Y", "symbol_x": "X"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is False
    assert body["reason"] is not None


# ---------------------------------------------------------------------------
# POST /data/pairs/scan
# ---------------------------------------------------------------------------


def test_pairs_scan_too_few_symbols_is_422_with_stable_tag():
    with _no_token():
        resp = client.post("/data/pairs/scan", json={"symbols": ["Y"]})
    assert resp.status_code == 422
    body = resp.json()["detail"]
    assert body["error"] == "too_few_symbols"
    assert body["min"] == 2


def test_pairs_scan_too_many_symbols_is_422_with_stable_tag():
    symbols = [f"SYM{i}" for i in range(20)]
    with _no_token():
        resp = client.post("/data/pairs/scan", json={"symbols": symbols})
    assert resp.status_code == 422
    body = resp.json()["detail"]
    assert body["error"] == "too_many_symbols"
    assert body["max"] == 15


def test_pairs_scan_dedup_can_drop_below_minimum():
    # "Y","y","Y" dedupes to a single symbol -- below the 2-minimum.
    with _no_token():
        resp = client.post("/data/pairs/scan", json={"symbols": ["Y", "y", "Y"]})
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "too_few_symbols"


def test_pairs_scan_success(monkeypatch):
    frame = _cointegrated_frame()
    monkeypatch.setattr(data_api, "get_provider", lambda: _FakeProvider(frame=frame))
    with _no_token():
        resp = client.post(
            "/data/pairs/scan", json={"symbols": ["Y", "X", "GHOST"], "max_pairs": 10}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["missing"] == ["GHOST"]
    assert body["pairs"]


# ---------------------------------------------------------------------------
# POST /data/options/recompute
# ---------------------------------------------------------------------------


def test_options_recompute_too_few_symbols_is_422():
    with _no_token():
        resp = client.post("/data/options/recompute", json={"symbols": []})
    assert resp.status_code == 422


def test_options_recompute_too_many_symbols_is_422_with_stable_tag():
    symbols = [f"SYM{i}" for i in range(10)]
    with _no_token():
        resp = client.post("/data/options/recompute", json={"symbols": symbols})
    assert resp.status_code == 422
    body = resp.json()["detail"]
    assert body["error"] == "too_many_symbols"
    assert body["max"] == 8


def test_options_recompute_out_of_range_field_is_422():
    with _no_token():
        resp = client.post(
            "/data/options/recompute",
            json={"symbols": ["AAPL"], "target_dte": 999},
        )
    assert resp.status_code == 422


def test_options_recompute_success(monkeypatch):
    bars = _synthetic_option_bars()
    provider = _FakeProvider(bars=bars, quote=_FakeQuote(price=105.0))
    monkeypatch.setattr(data_api, "get_provider", lambda: provider)
    monkeypatch.setattr(data_api, "load_snapshot", lambda: {"vix": 15.0, "market_regime": "RISK ON"})
    with _no_token():
        resp = client.post("/data/options/recompute", json={"symbols": ["aapl", "AAPL"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["errors"] == []
    assert len(body["directives"]) == 1  # de-duped
    assert body["directives"][0]["Symbol"] == "AAPL"
    assert body["vix"] == 15.0
    assert body["market_regime"] == "RISK ON"
    assert body["target_dte"] == 30


def test_options_recompute_bad_symbol_is_dead_lettered_not_500(monkeypatch):
    bars = _synthetic_option_bars()
    good_provider = _FakeProvider(bars=bars, quote=_FakeQuote(price=105.0))

    class _MixedProvider:
        def get_latest_quote(self, symbol):
            if symbol == "BADSYM":
                raise data_api.MarketDataError("no quote")
            return good_provider.get_latest_quote(symbol)

        def get_intraday_bars(self, symbol, lookback_days=252):
            return good_provider.get_intraday_bars(symbol, lookback_days)

    monkeypatch.setattr(data_api, "get_provider", lambda: _MixedProvider())
    monkeypatch.setattr(data_api, "load_snapshot", lambda: None)
    with _no_token():
        resp = client.post("/data/options/recompute", json={"symbols": ["AAPL", "BADSYM"]})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["directives"]) == 2
    assert len(body["errors"]) == 1
    bad_row = next(r for r in body["directives"] if r["Symbol"] == "BADSYM")
    assert bad_row["Integrity_OK"] is False
    # No snapshot -> honest neutral macro defaults, not a fabricated value.
    assert body["vix"] == options_ondemand_default_vix()


def options_ondemand_default_vix() -> float:
    import options_ondemand

    return options_ondemand.MACRO_DEFAULT_VIX


def test_options_recompute_risk_free_rate_override_is_honored(monkeypatch):
    bars = _synthetic_option_bars()
    provider = _FakeProvider(bars=bars, quote=_FakeQuote(price=105.0))
    captured = {}

    import options_ondemand

    real_compute = options_ondemand.compute_directive_row

    def _spy(symbol, **kwargs):
        captured["risk_free_rate"] = kwargs.get("risk_free_rate")
        return real_compute(symbol, **kwargs)

    monkeypatch.setattr(data_api, "get_provider", lambda: provider)
    monkeypatch.setattr(data_api, "load_snapshot", lambda: None)
    monkeypatch.setattr(options_ondemand, "compute_directive_row", _spy)
    with _no_token():
        resp = client.post(
            "/data/options/recompute",
            json={"symbols": ["AAPL"], "risk_free_rate_pct": 3.0},
        )
    assert resp.status_code == 200
    assert captured["risk_free_rate"] == pytest.approx(0.03)
