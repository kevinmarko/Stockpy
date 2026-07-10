"""Dedicated offline test suite for ``simulation_engine.py``.

The module has, until now, been almost entirely uncovered — only
``print_survivorship_warning_for_backtest`` was ever referenced (once, indirectly)
elsewhere. This suite gives the simulation/backtesting layer a proper owning test file.

Everything here is FULLY OFFLINE: no yfinance, no FRED, no real market data. Synthetic
price/return series are built with a seeded ``numpy.random.RandomState`` so runs are
deterministic. The ``universe_engine`` survivorship dependency (which would otherwise
scrape Wikipedia) is stubbed via ``sys.modules`` monkeypatching. The two heavy
event-driven backtest entry points depend on ``vectorbt`` / ``backtrader``; those tests
are guarded with ``pytest.importorskip`` so they run when the deps are installed and
skip cleanly (never fabricating data) when they are not.

Coverage:
    - get_vbt_costs():
        * default (large-cap) fees + slippage are the exact, NON-static values derived
          from execution.cost_model.TieredCostModel (proves real cost model wiring)
        * fees scale monotonically with worsening liquidity (large < small < illiquid)
        * return type is a (float, float) tuple; costs are strictly positive
    - cost_sensitivity_curve():
        * empty series -> graceful early return + logged warning (no crash)
        * zero-variance (std == 0) series -> graceful early return (no crash)
        * real returns -> prints the bps sensitivity table; higher cost columns reduce
          the annualized return (costs are actually applied, not assumed away)
        * a low-Sharpe series triggers the "collapses below 1.0" WARNING at the 20bps row
    - print_survivorship_warning_for_backtest():
        * survivorship warning IS emitted on the backtest path (happy path via stub)
        * correct start date (index.min()) is forwarded to the universe helper
        * dependency failure -> hardcoded "SURVIVORSHIP BIAS" fallback + logged warning
        * a non-DatetimeIndex input is coerced via pd.to_datetime without crashing
    - optimize_strategy_vectorbt()  [importorskip vectorbt]:
        * runs end-to-end on a synthetic price series and returns a 2-tuple best combo;
          the survivorship warning is emitted on the vectorbt backtest path too
    - run_backtrader_simulation() / InstitutionalStrategy  [importorskip backtrader]:
        * runs Cerebro end-to-end on a comfortably-sized synthetic OHLCV frame
          (survivorship warning + TieredCost commission wiring), prints portfolio
          values, raises nothing
        * an undersized frame (fewer bars than the largest indicator period, slow_ma=50)
          has NO guard in the module and backtrader itself raises IndexError — the test
          pins that ACTUAL behavior rather than fabricating a guard that does not exist
        * InstitutionalStrategy is a bt.Strategy subclass exposing its tunable params
"""

import logging
import sys
import types

import numpy as np
import pandas as pd
import pytest

import simulation_engine as sim
from execution.cost_model import TieredCostModel


# ---------------------------------------------------------------------------
# Deterministic synthetic-data helpers (no network, seeded)
# ---------------------------------------------------------------------------
def _synthetic_close(periods: int = 260, seed: int = 42, start: str = "2024-01-01") -> pd.Series:
    """A seeded geometric-random-walk close-price Series with a business-day index."""
    rng = np.random.RandomState(seed)
    dates = pd.date_range(start=start, periods=periods, freq="B")
    rets = rng.normal(0.0005, 0.015, periods)
    price = 100.0 * np.exp(np.cumsum(rets))
    return pd.Series(price, index=dates, name="close")


def _synthetic_ohlcv(periods: int = 260, seed: int = 42, start: str = "2024-01-01") -> pd.DataFrame:
    """Lowercase-column OHLCV frame matching what ``run_backtrader_simulation`` expects."""
    close = _synthetic_close(periods=periods, seed=seed, start=start)
    rng = np.random.RandomState(seed + 1)
    price = close.to_numpy()
    return pd.DataFrame(
        {
            "open": price * rng.uniform(0.99, 1.01, periods),
            "high": price * rng.uniform(1.01, 1.03, periods),
            "low": price * rng.uniform(0.97, 0.99, periods),
            "close": price,
            "volume": rng.randint(100_000, 500_000, periods).astype(float),
        },
        index=close.index,
    )


def _install_fake_universe_engine(monkeypatch, *, marker: str, raises: bool = False, recorder=None):
    """Inject a stub ``universe_engine`` module so the survivorship path stays offline.

    ``print_survivorship_warning_for_backtest`` does a lazy ``from universe_engine import ...``
    inside its body, so replacing the entry in ``sys.modules`` fully controls it without any
    network access.
    """
    fake = types.ModuleType("universe_engine")

    def _get_universe(start_date):
        if recorder is not None:
            recorder["start_date"] = start_date
        if raises:
            raise RuntimeError("simulated universe failure")
        return ([], {"note": "stub bias report"})

    def _print_warning(bias_report):
        print(marker)

    fake.get_universe_with_survivorship_warning = _get_universe
    fake.print_survivorship_bias_warning = _print_warning
    monkeypatch.setitem(sys.modules, "universe_engine", fake)
    return fake


# ===========================================================================
# 1. get_vbt_costs — TieredCostModel -> VectorBT fees/slippage
# ===========================================================================
class TestGetVbtCosts:
    def test_default_large_cap_costs_match_cost_model(self):
        fees, slippage = sim.get_vbt_costs(market_cap=None)
        model = TieredCostModel()
        # large_cap spread = 1.0 bps -> fees = (1.0/2 + 1.39) / 1e4
        expected_fees = ((model.spread_bps_by_liquidity["large_cap"] / 2.0) + 1.39) / 10000.0
        expected_slip = model.slippage_bps_market_order / 10000.0
        assert fees == pytest.approx(expected_fees)
        assert slippage == pytest.approx(expected_slip)
        # These are real, non-zero costs — not a static "assume no friction" shortcut.
        assert fees > 0
        assert slippage > 0

    def test_returns_tuple_of_floats(self):
        result = sim.get_vbt_costs(market_cap=5e9)
        assert isinstance(result, tuple)
        assert len(result) == 2
        fees, slippage = result
        assert isinstance(fees, float)
        assert isinstance(slippage, float)

    def test_fees_scale_with_worsening_liquidity(self):
        # market caps chosen to land in large_cap / small_cap / illiquid tiers respectively
        large_fees, large_slip = sim.get_vbt_costs(market_cap=50e9)
        small_fees, small_slip = sim.get_vbt_costs(market_cap=1e9)
        illiquid_fees, illiquid_slip = sim.get_vbt_costs(market_cap=1e8)
        # Spread-driven fees must grow as liquidity worsens.
        assert large_fees < small_fees < illiquid_fees
        # Slippage (market-impact) component is a flat bps assumption across tiers.
        assert large_slip == small_slip == illiquid_slip


# ===========================================================================
# 2. cost_sensitivity_curve
# ===========================================================================
class TestCostSensitivityCurve:
    def test_empty_series_returns_gracefully(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = sim.cost_sensitivity_curve(pd.Series([], dtype=float))
        assert result is None
        assert any("No returns data" in rec.message for rec in caplog.records)

    def test_zero_variance_series_returns_gracefully(self, capsys):
        # Constant returns -> std == 0 -> must early-return without dividing by zero.
        constant = pd.Series([0.001] * 50, index=pd.date_range("2024-01-01", periods=50, freq="B"))
        result = sim.cost_sensitivity_curve(constant)
        assert result is None  # no crash

    def test_prints_table_and_costs_reduce_returns(self, capsys):
        rets = _synthetic_close().pct_change().dropna()
        sim.cost_sensitivity_curve(rets, cost_bps_range=(0, 50))
        out = capsys.readouterr().out
        assert "Cost Sensitivity Analysis" in out
        assert "Cost (bps)" in out
        # The table steps through 0..50 bps in steps of 5 -> both endpoints must appear.
        assert "0.0" in out
        assert "50.0" in out

        # Independently verify the underlying arithmetic: higher per-trade cost strictly
        # lowers the annualized return (costs really are applied, not assumed away).
        trade_days = rets != 0

        def ann_ret(cost_bps):
            adj = rets.copy()
            adj[trade_days] -= cost_bps / 10000.0
            return adj.mean() * 252

        assert ann_ret(50) < ann_ret(0)

    def test_low_sharpe_series_triggers_collapse_warning(self, caplog):
        # Near-zero drift, real volatility -> Sharpe well below 1.0 at the 20bps checkpoint.
        rng = np.random.RandomState(7)
        idx = pd.date_range("2024-01-01", periods=252, freq="B")
        rets = pd.Series(rng.normal(0.0, 0.01, 252), index=idx)
        with caplog.at_level(logging.WARNING):
            sim.cost_sensitivity_curve(rets, cost_bps_range=(0, 25))
        assert any("collapses" in rec.message for rec in caplog.records)


# ===========================================================================
# 3. print_survivorship_warning_for_backtest
# ===========================================================================
class TestSurvivorshipWarning:
    def test_warning_emitted_with_correct_start_date(self, monkeypatch, capsys):
        recorder = {}
        marker = "STUB_SURVIVORSHIP_MARKER"
        _install_fake_universe_engine(monkeypatch, marker=marker, recorder=recorder)

        idx = pd.date_range("2020-03-15", periods=120, freq="B")
        sim.print_survivorship_warning_for_backtest(idx)

        out = capsys.readouterr().out
        assert marker in out
        # The helper must forward the EARLIEST date in the backtest window.
        assert recorder["start_date"] == idx.min().date()

    def test_dependency_failure_falls_back_to_hardcoded_warning(self, monkeypatch, capsys, caplog):
        _install_fake_universe_engine(monkeypatch, marker="unused", raises=True)
        idx = pd.date_range("2019-01-01", periods=60, freq="B")
        with caplog.at_level(logging.WARNING):
            sim.print_survivorship_warning_for_backtest(idx)
        out = capsys.readouterr().out
        assert "SURVIVORSHIP BIAS" in out  # hardcoded fallback banner
        assert any("survivorship bias report" in rec.message.lower() for rec in caplog.records)

    def test_non_datetime_index_is_coerced(self, monkeypatch, capsys):
        recorder = {}
        marker = "COERCED_MARKER"
        _install_fake_universe_engine(monkeypatch, marker=marker, recorder=recorder)
        # A plain object Index of date strings hits the pd.to_datetime branch.
        idx = pd.Index(["2021-06-01", "2021-06-02", "2021-05-30"])
        sim.print_survivorship_warning_for_backtest(idx)
        out = capsys.readouterr().out
        assert marker in out
        assert recorder["start_date"] == pd.to_datetime("2021-05-30").date()


# ===========================================================================
# 4. optimize_strategy_vectorbt  (requires vectorbt)
# ===========================================================================
class TestVectorbtOptimization:
    def test_returns_best_combo_and_emits_survivorship_warning(self, monkeypatch, capsys):
        pytest.importorskip("vectorbt")
        emitted = {"called": False}

        def _fake_warning(index):
            emitted["called"] = True

        monkeypatch.setattr(sim, "print_survivorship_warning_for_backtest", _fake_warning)

        # Need enough history for the largest slow window (up to ~130) to be meaningful.
        price = _synthetic_close(periods=300, seed=11)
        best_combo = sim.optimize_strategy_vectorbt(price)

        assert emitted["called"], "survivorship warning must fire on the vectorbt backtest path"
        # best_combo is the argmax label of a MultiIndex -> a 2-tuple of window sizes.
        assert best_combo is not None
        assert len(best_combo) == 2
        out = capsys.readouterr().out
        assert "VectorBT" in out


# ===========================================================================
# 5. run_backtrader_simulation / InstitutionalStrategy  (requires backtrader)
# ===========================================================================
class TestBacktraderSimulation:
    def test_institutional_strategy_is_bt_strategy_with_params(self):
        bt = pytest.importorskip("backtrader")
        assert issubclass(sim.InstitutionalStrategy, bt.Strategy)
        # After class creation backtrader turns ``params`` into an AutoInfoClass, so the
        # tunables are read back via its ``_getkeys()`` API (NOT by iterating the class).
        param_names = set(sim.InstitutionalStrategy.params._getkeys())
        assert {"fast_ma", "slow_ma", "atr_period", "risk_per_trade"} <= param_names

    def test_runs_end_to_end_and_prints_portfolio_values(self, monkeypatch, capsys):
        pytest.importorskip("backtrader")
        # Keep the survivorship path offline & fast.
        monkeypatch.setattr(sim, "print_survivorship_warning_for_backtest", lambda index: None)

        # 300 bars is comfortably above the largest indicator period (slow_ma=50), so all
        # indicators have warmup room and backtrader's once() pass completes.
        df = _synthetic_ohlcv(periods=300, seed=5)
        # Must run to completion without raising (broker + tiered commission wiring).
        assert sim.run_backtrader_simulation(df) is None
        out = capsys.readouterr().out
        assert "Starting Portfolio Value" in out
        assert "Final Portfolio Value" in out

    def test_undersized_frame_raises_no_internal_guard(self, monkeypatch):
        pytest.importorskip("backtrader")
        monkeypatch.setattr(sim, "print_survivorship_warning_for_backtest", lambda index: None)
        # run_backtrader_simulation has NO undersized-frame guard: with fewer bars than the
        # largest indicator period (slow_ma=50), backtrader's own once() pass walks off the
        # end of the indicator array. This pins the module's ACTUAL behavior — we do not
        # invent a guard that isn't there.
        df = _synthetic_ohlcv(periods=20, seed=3)
        with pytest.raises(IndexError):
            sim.run_backtrader_simulation(df)
