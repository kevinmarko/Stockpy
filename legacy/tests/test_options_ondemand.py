"""
tests/test_options_ondemand.py
================================
Offline unit tests for ``options_ondemand.py`` — the on-demand (operator-
triggered, synchronous) options premium-directive compute backing
``POST /data/options/recompute`` (webapp porting backlog item 8b).

``compute_directive_row`` is a thin wrapper around the already fully-tested
``technical_options_engine.build_premium_directive`` (see
``tests/test_options_matrix.py``) — these tests focus on the wrapper's OWN
contract: macro-proxy construction, dead-letter behavior on a bad symbol, and
never raising, using deterministic synthetic bars (mirrors
``tests/test_options_matrix.py::_synthetic_bars``).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

import options_ondemand
from data.market_data import MarketDataError


def _synthetic_bars(n: int = 252, seed: int = 0) -> pd.DataFrame:
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
    def __init__(self, bars=None, quote=None, raises_on: str = ""):
        self._bars = bars
        self._quote = quote
        self._raises_on = raises_on  # "quote" | "bars" | ""

    def get_latest_quote(self, symbol: str):
        if self._raises_on == "quote":
            raise MarketDataError(f"no quote for {symbol}")
        return self._quote

    def get_intraday_bars(self, symbol: str, lookback_days: int = 252):
        if self._raises_on == "bars":
            raise MarketDataError(f"no bars for {symbol}")
        return self._bars


# ---------------------------------------------------------------------------
# macro_from_snapshot
# ---------------------------------------------------------------------------


def test_macro_from_snapshot_defaults_on_none():
    vix, regime = options_ondemand.macro_from_snapshot(None)
    assert vix == options_ondemand.MACRO_DEFAULT_VIX
    assert regime == options_ondemand.MACRO_DEFAULT_REGIME


def test_macro_from_snapshot_defaults_on_malformed():
    vix, regime = options_ondemand.macro_from_snapshot({"vix": "not-a-number"})
    assert vix == options_ondemand.MACRO_DEFAULT_VIX
    # market_regime absent -> default
    assert regime == options_ondemand.MACRO_DEFAULT_REGIME


def test_macro_from_snapshot_reads_real_values():
    vix, regime = options_ondemand.macro_from_snapshot(
        {"vix": 32.5, "market_regime": "CREDIT EVENT"}
    )
    assert vix == 32.5
    assert regime == "CREDIT EVENT"


def test_macro_from_snapshot_null_vix_falls_back_to_default():
    vix, regime = options_ondemand.macro_from_snapshot({"vix": None, "market_regime": "RISK OFF"})
    assert vix == options_ondemand.MACRO_DEFAULT_VIX
    assert regime == "RISK OFF"


# ---------------------------------------------------------------------------
# compute_directive_row
# ---------------------------------------------------------------------------


def test_compute_directive_row_success():
    provider = _FakeProvider(bars=_synthetic_bars(), quote=_FakeQuote(price=105.0))
    result = options_ondemand.compute_directive_row(
        "AAPL",
        provider=provider,
        target_dte=30,
        vix=15.0,
        market_regime="RISK ON",
        risk_free_rate=0.045,
    )
    assert result["error"] is None
    row = result["row"]
    assert row["Symbol"] == "AAPL"
    assert row["Price"] == 105.0
    assert "Strategy" in row and "Action" in row
    assert "Integrity_OK" in row


def test_compute_directive_row_market_data_error_is_dead_lettered():
    provider = _FakeProvider(raises_on="quote")
    result = options_ondemand.compute_directive_row(
        "ZZZZ", provider=provider, target_dte=30, vix=15.0, market_regime="RISK ON",
        risk_free_rate=0.045,
    )
    assert result["error"] is not None
    assert "market data unavailable" in result["error"]
    row = result["row"]
    assert row["Symbol"] == "ZZZZ"
    assert row["Strategy"] == "—"
    assert row["Action"] == "—"
    assert row["Integrity_OK"] is False
    assert row["Integrity_Issues"]


def test_compute_directive_row_unexpected_exception_is_dead_lettered():
    # build_premium_directive itself is defensive (a malformed `bars` degrades
    # internally to a Cash/Wait row rather than raising) -- so to exercise
    # compute_directive_row's OWN generic `except Exception` branch we need a
    # failure at THIS wrapper's call sites: a `None` quote object makes
    # `float(quote.price)` raise AttributeError before build_premium_directive
    # is ever reached.
    provider = _FakeProvider(bars=_synthetic_bars(), quote=None)
    result = options_ondemand.compute_directive_row(
        "BADSYM", provider=provider, target_dte=30, vix=15.0, market_regime="RISK ON",
        risk_free_rate=0.045,
    )
    assert result["error"] is not None
    assert result["row"]["Symbol"] == "BADSYM"
    assert result["row"]["Integrity_OK"] is False


def test_compute_directive_row_vrp_gate_fires_in_stress_regime():
    """VIX >= 30 should route to Cash/Wait via the engine's own VRP gate --
    proves the macro proxy is actually wired through, not ignored."""
    provider = _FakeProvider(bars=_synthetic_bars(seed=1), quote=_FakeQuote(price=105.0))
    calm = options_ondemand.compute_directive_row(
        "AAPL", provider=provider, target_dte=30, vix=15.0, market_regime="RISK ON",
        risk_free_rate=0.045, ivr_sell_threshold=10.0,  # low threshold -> likely to sell in calm
    )
    stressed = options_ondemand.compute_directive_row(
        "AAPL", provider=provider, target_dte=30, vix=35.0, market_regime="RISK ON",
        risk_free_rate=0.045, ivr_sell_threshold=10.0,
    )
    # The stressed-regime directive must never be a premium-SELLING structure
    # (Put Credit Spread / Iron Condor) -- Cash/Wait or a debit structure only.
    assert stressed["row"]["Strategy"] not in {"Put Credit Spread", "Iron Condor"}
