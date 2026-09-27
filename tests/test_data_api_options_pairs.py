"""
tests/test_data_api_options_pairs.py
======================================
Fully-offline tests for the on-demand recompute endpoints added to
``api/data_api.py`` (webapp porting backlog items 8a/8b):

  * ``POST /data/pairs/analyze``  — one named pair
  * ``POST /data/pairs/scan``     — cointegration scan over a symbol list

(``POST /data/options/recompute`` was removed with the options desk, 2026-09,
step 3e -- its tests were removed from this file in the same pass.)

``get_provider`` and ``load_snapshot`` are monkeypatched on the
``api.data_api`` module namespace (mirrors ``tests/test_data_api_ai.py``'s
convention) — no real network/provider call, no real ``output/
state_snapshot.json`` read happens here. ``pairs_ondemand`` functions
themselves are unit-tested independently in
``tests/test_pairs_ondemand.py``; these tests focus on the HTTP contract:
auth, request validation (422 stable tags), and correct wiring of the
provider/snapshot into the underlying compute.
"""
from __future__ import annotations

from unittest import mock

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from settings import settings
import api.data_api as data_api

# Starlette's TestClient defaults request.client.host to the literal
# string "testclient" -- NOT loopback -- which would trip
# api.auth.require_read_token's new fail-closed-when-non-loopback branch
# on every one of this file's existing zero-config-behavior assertions.
# An explicit loopback host here is what these tests have always meant.
client = TestClient(data_api.app, client=("127.0.0.1", 54123))


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


class _FakeProvider:
    """Serves the pairs Close-series path from the fixture frame."""

    def __init__(self, frame: pd.DataFrame = None):
        self._frame = frame

    def get_intraday_bars(self, symbol: str, lookback_days: int = 252):
        if self._frame is not None:
            if symbol not in self._frame.columns:
                return pd.DataFrame()
            return pd.DataFrame({"Close": self._frame[symbol].tail(lookback_days)})
        return None


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
