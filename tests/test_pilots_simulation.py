"""
tests/test_pilots_simulation.py
================================
Unit tests for ``pilots/simulation.py::simulate_pilot_allocation`` -- the
real, honest "What-If" allocation simulator behind
``POST /pilots/{pilot_id}/simulate``.

All network/engine dependencies are monkeypatched at their SOURCE module
(``data.historical_store.HistoricalStore``) or at the ``pilots.*`` module
attribute the simulator calls through (``pilots.catalog.get_pilot``,
``pilots.scoring.load_snapshot``/``pilot_holdings``) -- mirroring
``tests/test_pilots_observability.py``'s convention. ``portfolio_risk_metrics``/
``portfolio_heat_metric`` are imported by NAME into ``pilots.simulation`` (a
direct ``from pilots.observability import ...``), so those two are patched on
``pilots.simulation`` itself, not on ``pilots.observability``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest import mock

import pandas as pd

from pilots import simulation as sim
from pilots.catalog import Pilot


def _bars(n: int, start_price: float, rates) -> pd.DataFrame:
    """Build a minimal OHLCV-shaped DataFrame with a real (non-degenerate)
    Close price walk, matching HistoricalStore.get_bars's shape contract."""
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    closes = [start_price]
    for i in range(n - 1):
        closes.append(closes[-1] * (1 + rates[i % len(rates)]))
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes,
            "Low": closes,
            "Close": closes,
            "Volume": [1000] * n,
        },
        index=dates,
    )


class _Position:
    def __init__(self, symbol: str, market_value: float):
        self.symbol = symbol
        self.market_value = market_value
        self.unrealized_pl = 0.0


class _Snapshot:
    def __init__(self, total_equity: float, positions: dict):
        self.total_equity = total_equity
        self.positions = positions
        self.buying_power = 0.0
        self.total_dividends = 0.0
        self.fetched_at = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _FakeStore:
    def __init__(self, snapshot=None, bars: dict | None = None):
        self._snapshot = snapshot
        self._bars = bars or {}

    def latest_account_snapshot(self):
        return self._snapshot

    def get_bars(self, symbol, lookback_days=252, **kwargs):
        return self._bars.get(symbol, pd.DataFrame())


_RATES = [0.01, -0.005, 0.008, -0.002, 0.004, -0.006, 0.003]
_TEST_PILOT = Pilot(
    id="test-pilot",
    name="Test Pilot",
    category="Momentum",
    description="A test pilot.",
    weights={"trend": 1.0},
)


# ---------------------------------------------------------------------------
# Honest-degradation paths
# ---------------------------------------------------------------------------


class TestHonestDegradation:
    def test_unknown_pilot(self):
        with mock.patch("pilots.catalog.get_pilot", return_value=None):
            out = sim.simulate_pilot_allocation("nonexistent", 1000.0)

        assert out["pilot_id"] == "nonexistent"
        assert out["reason"] == "unknown pilot"
        assert out["current"] == {"sharpe_ratio": None, "max_drawdown": None}
        assert out["projected"] == {"sharpe_ratio": None, "max_drawdown": None}
        assert out["heat_pct_current"] is None
        assert out["heat_pct_projected"] is None
        assert out["coverage"] == {"symbols_covered": 0, "symbols_total": 0}

    def test_no_snapshot(self):
        fake_store = _FakeStore(snapshot=None)
        with mock.patch("pilots.catalog.get_pilot", return_value=_TEST_PILOT), \
             mock.patch("data.historical_store.HistoricalStore", return_value=fake_store):
            out = sim.simulate_pilot_allocation("test-pilot", 1000.0)

        assert out["reason"] == "no portfolio snapshot available"
        assert out["heat_pct_projected"] is None

    def test_non_positive_total_equity_degrades_honestly(self):
        fake_store = _FakeStore(snapshot=_Snapshot(total_equity=0.0, positions={}))
        with mock.patch("pilots.catalog.get_pilot", return_value=_TEST_PILOT), \
             mock.patch("data.historical_store.HistoricalStore", return_value=fake_store):
            out = sim.simulate_pilot_allocation("test-pilot", 1000.0)

        assert out["reason"] == "no portfolio snapshot available"

    def test_no_aligned_price_history(self):
        snap = _Snapshot(total_equity=10_000.0, positions={"AAPL": _Position("AAPL", 6_000.0)})
        fake_store = _FakeStore(snapshot=snap, bars={})  # every get_bars() call -> empty df

        holdings = [{"symbol": "MSFT", "weight": 1.0}]
        risk = {"sharpe_ratio": 1.1, "max_drawdown": -0.1}
        heat = {"heat_pct": 0.03}

        with mock.patch("pilots.catalog.get_pilot", return_value=_TEST_PILOT), \
             mock.patch("data.historical_store.HistoricalStore", return_value=fake_store), \
             mock.patch("pilots.scoring.load_snapshot", return_value={"signals": []}), \
             mock.patch("pilots.scoring.pilot_holdings", return_value=holdings), \
             mock.patch("pilots.simulation.portfolio_risk_metrics", return_value=risk), \
             mock.patch("pilots.simulation.portfolio_heat_metric", return_value=heat):
            out = sim.simulate_pilot_allocation("test-pilot", 5_000.0)

        assert out["reason"] == "no aligned price history available"
        # current/heat are still honestly reported even though the projected
        # side has no aligned history to compute against.
        assert out["current"] == risk
        assert out["heat_pct_current"] == 0.03
        assert out["heat_pct_projected"] is None
        assert out["coverage"] == {"symbols_covered": 0, "symbols_total": 2}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_shape_and_honesty_invariants(self):
        snap = _Snapshot(total_equity=10_000.0, positions={"AAPL": _Position("AAPL", 6_000.0)})
        bars = {
            "AAPL": _bars(40, 150.0, _RATES),
            "MSFT": _bars(40, 300.0, _RATES[::-1]),
        }
        fake_store = _FakeStore(snapshot=snap, bars=bars)

        holdings = [{"symbol": "MSFT", "weight": 1.0}]
        risk = {"sharpe_ratio": 1.23, "max_drawdown": -0.05}
        heat = {"heat_pct": 0.02}

        with mock.patch("pilots.catalog.get_pilot", return_value=_TEST_PILOT), \
             mock.patch("data.historical_store.HistoricalStore", return_value=fake_store), \
             mock.patch("pilots.scoring.load_snapshot", return_value={"signals": []}), \
             mock.patch("pilots.scoring.pilot_holdings", return_value=holdings), \
             mock.patch("pilots.simulation.portfolio_risk_metrics", return_value=risk), \
             mock.patch("pilots.simulation.portfolio_heat_metric", return_value=heat):
            out = sim.simulate_pilot_allocation("test-pilot", 5_000.0)

        # Overall shape
        assert set(out.keys()) == {
            "pilot_id", "current", "projected", "heat_pct_current",
            "heat_pct_projected", "coverage", "reason",
        }
        assert out["pilot_id"] == "test-pilot"
        assert out["reason"] is None

        # `current` is reused VERBATIM from portfolio_risk_metrics() -- never
        # recomputed independently.
        assert out["current"] == risk
        assert out["heat_pct_current"] == 0.02

        # `projected` is real, derived numbers -- not the same as `current`,
        # not a hardcoded delta.
        assert out["projected"]["sharpe_ratio"] is not None
        assert out["projected"]["max_drawdown"] is not None
        assert isinstance(out["projected"]["sharpe_ratio"], float)
        assert isinstance(out["projected"]["max_drawdown"], float)

        # Honesty invariant: heat_pct_projected is ALWAYS None -- never a
        # fabricated proxy number (CONSTRAINT #4).
        assert out["heat_pct_projected"] is None

        # Coverage: both AAPL (current) and MSFT (pilot target) have bars.
        assert out["coverage"] == {"symbols_covered": 2, "symbols_total": 2}

    def test_partial_coverage_renormalizes_and_counts_honestly(self):
        snap = _Snapshot(total_equity=10_000.0, positions={"AAPL": _Position("AAPL", 6_000.0)})
        # Only AAPL has real bars; MSFT (the pilot's target) has none.
        bars = {"AAPL": _bars(40, 150.0, _RATES)}
        fake_store = _FakeStore(snapshot=snap, bars=bars)

        holdings = [{"symbol": "MSFT", "weight": 1.0}]
        risk = {"sharpe_ratio": 0.5, "max_drawdown": -0.2}
        heat = {"heat_pct": 0.01}

        with mock.patch("pilots.catalog.get_pilot", return_value=_TEST_PILOT), \
             mock.patch("data.historical_store.HistoricalStore", return_value=fake_store), \
             mock.patch("pilots.scoring.load_snapshot", return_value={"signals": []}), \
             mock.patch("pilots.scoring.pilot_holdings", return_value=holdings), \
             mock.patch("pilots.simulation.portfolio_risk_metrics", return_value=risk), \
             mock.patch("pilots.simulation.portfolio_heat_metric", return_value=heat):
            out = sim.simulate_pilot_allocation("test-pilot", 5_000.0)

        assert out["reason"] is None
        assert out["coverage"] == {"symbols_covered": 1, "symbols_total": 2}
        # Still real, non-null projected numbers (renormalized over the one
        # covered symbol, AAPL, alone).
        assert out["projected"]["sharpe_ratio"] is not None
        assert out["heat_pct_projected"] is None

    def test_never_raises_on_unexpected_exception(self):
        """A totally broken dependency degrades honestly instead of 500ing
        the endpoint (CONSTRAINT #6)."""
        with mock.patch("pilots.catalog.get_pilot", side_effect=RuntimeError("boom")):
            out = sim.simulate_pilot_allocation("test-pilot", 1000.0)
        assert out["reason"] == "unknown pilot"
        assert out["heat_pct_projected"] is None
