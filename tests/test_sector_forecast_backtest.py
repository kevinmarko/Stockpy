"""Tests for validation/sector_forecast_backtest.py.

All data is synthetic and generated in-process with numpy (no network I/O).
Monte Carlo forecasts consume the global numpy RNG (see
``ForecastingEngine.run_monte_carlo``), so every test that exercises MC seeds
``np.random.seed(...)`` before invoking backtest code, for reproducibility.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from forecasting_engine import ForecastingEngine
from validation.forecast_accuracy_metrics import naive_one_step_mae
from validation.sector_forecast_backtest import (
    _forecast_one,
    _walk_forward_symbol,
    run_sector_backtest,
)
from validation.sector_forecast_types import BacktestConfig, ForecastError

np.random.seed(42)


# ---------------------------------------------------------------------------
# Synthetic data builders
# ---------------------------------------------------------------------------


def _make_ohlcv(close: np.ndarray) -> pd.DataFrame:
    """Wrap a Close array into an OHLCV-shaped DataFrame matching the
    DataEngine/HistoricalStore contract (tz-naive DatetimeIndex, sorted
    ascending, columns [Open, High, Low, Close, Volume])."""
    idx = pd.bdate_range("2022-01-03", periods=len(close))
    return pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.001,
            "Low": close * 0.999,
            "Close": close,
            "Volume": np.full(len(close), 1_000_000, dtype=float),
        },
        index=idx,
    )


def _smooth_trend_prices(n: int, start: float, slope: float) -> np.ndarray:
    """Purely deterministic linear trend, zero noise. ARIMA/Holt-Winters can
    fit this near-perfectly; a lognormal-diffusion (Monte Carlo) model
    cannot, since a linear price path is not a geometric process."""
    t = np.arange(n, dtype=float)
    return start + slope * t


def _gbm_prices(n: int, start: float, mu_daily: float, sigma_daily: float, seed: int) -> np.ndarray:
    """A genuine Geometric Brownian Motion path — the exact data-generating
    process Monte Carlo assumes, giving it a structural edge over
    trend-extrapolation models (ARIMA/Holt-Winters) for this sector."""
    rng = np.random.RandomState(seed)
    shocks = rng.normal(0, 1, n)
    log_returns = (mu_daily - 0.5 * sigma_daily ** 2) + sigma_daily * shocks
    log_path = np.cumsum(log_returns)
    return start * np.exp(log_path)


# ---------------------------------------------------------------------------
# _forecast_one / _walk_forward_symbol unit tests
# ---------------------------------------------------------------------------


class TestForecastOneDispatch:
    def test_arima_dispatch(self):
        engine = ForecastingEngine()
        train = pd.Series(_smooth_trend_prices(120, 100.0, 0.5))
        pred = _forecast_one(engine, "ARIMA", train, start_price=float(train.iloc[-1]), horizon=10)
        assert np.isfinite(pred)
        assert pred > 0

    def test_hw_dispatch(self):
        engine = ForecastingEngine()
        train = pd.Series(_smooth_trend_prices(120, 100.0, 0.5))
        pred = _forecast_one(engine, "HW", train, start_price=float(train.iloc[-1]), horizon=10)
        assert np.isfinite(pred)
        assert pred > 0

    def test_mc_dispatch(self):
        np.random.seed(7)
        engine = ForecastingEngine()
        train = pd.Series(_gbm_prices(120, 100.0, 0.0003, 0.02, seed=7))
        pred = _forecast_one(engine, "MC", train, start_price=float(train.iloc[-1]), horizon=10)
        assert np.isfinite(pred)
        assert pred > 0

    def test_unknown_model_returns_zero(self):
        engine = ForecastingEngine()
        train = pd.Series(_smooth_trend_prices(120, 100.0, 0.5))
        pred = _forecast_one(engine, "PROPHET", train, start_price=100.0, horizon=10)
        assert pred == 0.0


class _StubEngineAlwaysFails:
    """Fake engine whose model methods always return the sentinel failure
    value (0.0) — used to prove the walk-forward never records a spurious
    ForecastError from a failed fit."""

    def run_arima(self, history, days_forward, order=(1, 1, 1)):
        return 0.0

    def run_holt_winters_grid_search(self, history, days_forward):
        return 0.0

    def run_monte_carlo(self, start_price, mu, sigma, days_forward, simulations=1000):
        return 0.0, 0.0, 0.0


class TestWalkForwardSymbol:
    def test_too_little_history_yields_no_errors(self):
        engine = ForecastingEngine()
        config = BacktestConfig(min_train_bars=120)
        prices = _make_ohlcv(_smooth_trend_prices(50, 100.0, 0.5))  # < min_train_bars
        errors = _walk_forward_symbol(prices, engine, "ARIMA", horizon=10, config=config)
        assert errors == []

    def test_model_sentinel_failure_excluded_not_recorded(self):
        stub = _StubEngineAlwaysFails()
        config = BacktestConfig(min_train_bars=60, lookback_days=300, step_days=30)
        prices = _make_ohlcv(_smooth_trend_prices(200, 100.0, 0.5))
        for model in ("ARIMA", "HW", "MC"):
            errors = _walk_forward_symbol(prices, stub, model, horizon=10, config=config)
            assert errors == [], f"model={model} should yield zero errors when engine always fails"

    def test_produces_errors_for_sufficient_data(self):
        engine = ForecastingEngine()
        config = BacktestConfig(min_train_bars=60, lookback_days=300, step_days=30)
        prices = _make_ohlcv(_smooth_trend_prices(200, 100.0, 0.5))
        errors = _walk_forward_symbol(prices, engine, "ARIMA", horizon=10, config=config)
        assert len(errors) > 0
        for e in errors:
            assert isinstance(e, ForecastError)
            assert np.isfinite(e.y_true)
            assert np.isfinite(e.y_pred)
            assert e.y_pred > 0
            assert e.naive_scale > 0


# ---------------------------------------------------------------------------
# No-lookahead test — the single most important property of this module.
# ---------------------------------------------------------------------------


class TestNoLookahead:
    def test_forecast_at_anchor_unaffected_by_future_perturbation(self):
        """Perturb prices strictly AFTER anchor t to extreme values, re-run
        the walk-forward, and assert the forecast produced AT anchor t is
        byte-identical. Only y_pred (and naive_scale, both functions of the
        training window ending strictly before t) are compared — y_true
        legitimately differs because we deliberately perturb the future
        outcome too, which is exactly the point: the anchor's forecast must
        not have "seen" it.
        """
        engine = ForecastingEngine()
        min_train_bars = 60
        horizon = 10
        trailing_extra = 15  # room to perturb points beyond t+horizon too
        n = min_train_bars + horizon + 1 + trailing_extra
        anchor_t = min_train_bars  # first (and only, via step_days below) anchor visited

        # step_days larger than the anchor range collapses the walk-forward
        # to a single anchor: t = min_train_bars.
        config = BacktestConfig(
            min_train_bars=min_train_bars,
            lookback_days=750,
            step_days=1_000,
        )

        base_close = _smooth_trend_prices(n, 100.0, 0.3)
        original = _make_ohlcv(base_close.copy())

        perturbed_close = base_close.copy()
        # Perturb everything strictly after the anchor index (including the
        # realized outcome at t+horizon and everything past it) to extreme,
        # wildly different values.
        perturbed_close[anchor_t + 1 :] = 1_000_000.0
        perturbed = _make_ohlcv(perturbed_close)

        errors_original = _walk_forward_symbol(original, engine, "ARIMA", horizon, config)
        errors_perturbed = _walk_forward_symbol(perturbed, engine, "ARIMA", horizon, config)

        assert len(errors_original) == 1
        assert len(errors_perturbed) == 1

        orig = errors_original[0]
        pert = errors_perturbed[0]

        # The training window (indices < anchor_t) and start_price (index
        # anchor_t) are untouched by the perturbation, so the forecast and
        # the naive scale must be identical.
        assert orig.y_pred == pytest.approx(pert.y_pred, rel=1e-12, abs=1e-9)
        assert orig.naive_scale == pytest.approx(pert.naive_scale, rel=1e-12, abs=1e-9)

        # Sanity: the outcome DID change (proves the perturbation was real
        # and that y_true isn't trivially equal for an unrelated reason).
        assert orig.y_true != pert.y_true

    def test_naive_scale_depends_only_on_training_window(self):
        """Direct check on naive_one_step_mae: perturbing data at/after the
        anchor must not change the naive scale computed from data strictly
        before it."""
        anchor_t = 80
        base = _smooth_trend_prices(150, 50.0, 0.2)
        train_only = base[:anchor_t]

        perturbed = base.copy()
        perturbed[anchor_t:] = -999.0  # garbage from the anchor onward

        scale_original = naive_one_step_mae(train_only)
        scale_from_perturbed_source = naive_one_step_mae(perturbed[:anchor_t])
        assert scale_original == pytest.approx(scale_from_perturbed_source)


# ---------------------------------------------------------------------------
# End-to-end run_sector_backtest tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def backtest_result():
    np.random.seed(123)
    engine = ForecastingEngine()

    n = 200
    price_data = {
        # "SmoothTrend" sector: two symbols on a purely deterministic
        # linear trend (zero noise). ARIMA/Holt-Winters should fit this
        # far better than a lognormal-diffusion (Monte Carlo) model,
        # which structurally mismatches a linear (non-geometric) path.
        "TRND1": _make_ohlcv(_smooth_trend_prices(n, 100.0, 0.5)),
        "TRND2": _make_ohlcv(_smooth_trend_prices(n, 60.0, 0.35)),
        # "Volatile" sector: a genuine GBM path — exactly the
        # data-generating process Monte Carlo assumes.
        "GBM1": _make_ohlcv(_gbm_prices(n, 80.0, 0.0005, 0.025, seed=99)),
    }
    ticker_sectors = {
        "TRND1": "SmoothTrend",
        "TRND2": "SmoothTrend",
        "GBM1": "Volatile",
    }
    config = BacktestConfig(
        horizons=(10,),
        models=("MC", "ARIMA", "HW"),
        lookback_days=300,
        min_train_bars=60,
        step_days=90,
        embargo_days=5,
    )
    return run_sector_backtest(price_data, ticker_sectors, engine, config)


class TestRunSectorBacktest:
    def test_returns_cell_for_every_sector_model_horizon_combo(self, backtest_result):
        sectors = {r.sector for r in backtest_result}
        models = {r.model for r in backtest_result}
        horizons = {r.horizon for r in backtest_result}
        assert sectors == {"SmoothTrend", "Volatile"}
        assert models == {"MC", "ARIMA", "HW"}
        assert horizons == {10}
        assert len(backtest_result) == 2 * 3 * 1  # sectors x models x horizons

    def test_counts_are_positive_for_populated_cells(self, backtest_result):
        for cell in backtest_result:
            assert cell.n_forecasts > 0, f"expected forecasts for {cell}"
            assert cell.n_symbols > 0, f"expected contributing symbols for {cell}"
            assert np.isfinite(cell.mase)
            assert np.isfinite(cell.rmse)

    def test_smoothtrend_sector_penalizes_monte_carlo(self, backtest_result):
        by_model = {
            r.model: r
            for r in backtest_result
            if r.sector == "SmoothTrend" and r.horizon == 10
        }
        mc_mase = by_model["MC"].mase
        arima_mase = by_model["ARIMA"].mase
        hw_mase = by_model["HW"].mase
        # A purely deterministic linear trend is fit near-perfectly by
        # trend-extrapolation models but poorly by a lognormal-diffusion
        # model whose stochastic shocks have no reason to cancel out over
        # a non-geometric path.
        assert mc_mase > arima_mase
        assert mc_mase > hw_mase

    def test_volatile_sector_favors_monte_carlo_over_arima(self, backtest_result):
        by_model = {
            r.model: r
            for r in backtest_result
            if r.sector == "Volatile" and r.horizon == 10
        }
        mc_mase = by_model["MC"].mase
        arima_mase = by_model["ARIMA"].mase
        # A genuine GBM path is exactly Monte Carlo's assumed generative
        # process; ARIMA models raw-price differences with a linear trend
        # and constant-variance innovations, mismatching a compounding,
        # heteroskedastic price path.
        assert mc_mase < arima_mase

    def test_empty_inputs_yield_no_cells(self):
        engine = ForecastingEngine()
        results = run_sector_backtest({}, {}, engine, BacktestConfig(horizons=(10,)))
        assert results == []

    def test_unmatched_sector_symbol_ignored(self):
        """A symbol present in ticker_sectors but absent from price_data must
        not crash the backtest — it is simply excluded from that sector's
        symbol list."""
        engine = ForecastingEngine()
        price_data = {"ONLY": _make_ohlcv(_smooth_trend_prices(200, 100.0, 0.5))}
        ticker_sectors = {"ONLY": "SectorA", "MISSING": "SectorA"}
        config = BacktestConfig(
            horizons=(10,), models=("ARIMA",), lookback_days=300,
            min_train_bars=60, step_days=90,
        )
        results = run_sector_backtest(price_data, ticker_sectors, engine, config)
        assert len(results) == 1
        assert results[0].n_symbols == 1

    def test_zero_data_cell_is_nan_not_crash(self):
        """A sector whose only symbol has too little history to produce any
        forecast still yields a CellResult with nan mase/rmse and zero
        counts — never a crash, never a fabricated score."""
        engine = ForecastingEngine()
        price_data = {"SHORT": _make_ohlcv(_smooth_trend_prices(20, 100.0, 0.5))}
        ticker_sectors = {"SHORT": "TinySector"}
        config = BacktestConfig(
            horizons=(10,), models=("ARIMA",), min_train_bars=60, step_days=30,
        )
        results = run_sector_backtest(price_data, ticker_sectors, engine, config)
        assert len(results) == 1
        cell = results[0]
        assert cell.n_forecasts == 0
        assert cell.n_symbols == 0
        assert np.isnan(cell.mase)
        assert np.isnan(cell.rmse)
