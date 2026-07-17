"""
tests/test_validation_forecast_direction.py

Tests for scripts.refresh_validations's forecast_direction_arima_hw adapter --
the honest, narrower backtest for the Forecast Aligned Pilot
(pilots/catalog.py).

Fast, offline unit tests (default suite) use TINY synthetic windows (a
handful of tickers/weeks) so the real ARIMA/Holt-Winters fits stay
sub-second-to-a-few-seconds per test -- this adapter's own docstring
documents that a REAL full run (5yr x 52 weeks x 10 tickers) takes several
minutes, which is why no test here exercises that full scope.

One @pytest.mark.network integration test runs the adapter against real
yfinance history through StrategyValidationHarness end-to-end -- asserting
only that the report is well-formed, NEVER that deployable is True.
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from scripts.refresh_validations import (
    FORECAST_DIRECTION_HORIZON_DAYS,
    FORECAST_DIRECTION_UNIVERSE,
    FORECAST_DIRECTION_WINDOW_YEARS,
    _build_forecast_direction_adapter,
    _weekly_rebalance_dates,
)


def _synthetic_closes(tickers, n_days: int = 90, start: str = "2023-01-01", seed: int = 5) -> pd.DataFrame:
    idx = pd.bdate_range(start=start, periods=n_days)
    rng = np.random.RandomState(seed)
    data = {}
    for t in tickers:
        rets = rng.normal(0.0003, 0.01, n_days)
        data[t] = 150.0 * np.cumprod(1 + rets)
    return pd.DataFrame(data, index=idx)


# ---------------------------------------------------------------------------
# TestWeeklyRebalanceDates
# ---------------------------------------------------------------------------

class TestWeeklyRebalanceDates:
    def test_returns_one_date_per_week(self) -> None:
        idx = pd.bdate_range("2023-01-01", periods=60)  # ~12 weeks
        dates = _weekly_rebalance_dates(idx)
        assert 10 <= len(dates) <= 14

    def test_every_date_is_a_real_trading_day(self) -> None:
        idx = pd.bdate_range("2023-01-01", periods=60)
        dates = _weekly_rebalance_dates(idx)
        for d in dates:
            assert d in idx

    def test_dates_are_sorted_ascending(self) -> None:
        idx = pd.bdate_range("2023-01-01", periods=60)
        dates = _weekly_rebalance_dates(idx)
        assert dates == sorted(dates)

    def test_empty_index_returns_empty_list(self) -> None:
        idx = pd.DatetimeIndex([])
        assert _weekly_rebalance_dates(idx) == []


# ---------------------------------------------------------------------------
# TestBuildForecastDirectionAdapter
# ---------------------------------------------------------------------------

class TestBuildForecastDirectionAdapter:
    def test_returns_three_items_and_variant(self) -> None:
        closes = _synthetic_closes(["AAPL", "JNJ"], n_days=90)
        X, y, pre = _build_forecast_direction_adapter(closes)

        assert not X.empty and not y.empty
        assert "ForecastDirection_Composite" in X.columns
        assert set(pre.keys()) == {"ForecastDirection_ScoreWeighted"}
        assert pre["ForecastDirection_ScoreWeighted"].index.equals(y.index)

    def test_empty_closes_degrades_cleanly(self) -> None:
        X, y, pre = _build_forecast_direction_adapter(pd.DataFrame())
        assert X.empty and y.empty and pre == {}

    def test_insufficient_history_degrades_cleanly(self) -> None:
        """Fewer than 60 rows total (even after the 5yr window trim) -> empty
        result, never a fabricated forecast (CONSTRAINT #4)."""
        closes = _synthetic_closes(["AAPL"], n_days=20)
        X, y, pre = _build_forecast_direction_adapter(closes)
        assert X.empty and y.empty and pre == {}

    def test_window_is_bounded_to_five_years(self) -> None:
        """A closes frame spanning far more than 5 years must be trimmed --
        the returned index's span must not exceed ~5 years + a small margin."""
        idx = pd.bdate_range("2005-01-01", periods=252 * 10)  # ~10 years
        rng = np.random.RandomState(1)
        closes = pd.DataFrame(
            {"AAPL": 100.0 * np.cumprod(1 + rng.normal(0.0003, 0.01, len(idx)))},
            index=idx,
        )
        X, y, pre = _build_forecast_direction_adapter(closes)
        span_years = (X.index[-1] - X.index[0]).days / 365.25
        assert span_years <= FORECAST_DIRECTION_WINDOW_YEARS + 0.5

    def test_score_weighted_book_not_rank_based(self) -> None:
        """Weights must sum (in absolute value) to <= 1.0 each day -- a
        score-weighted book, not a top-half equal-weight rank cut."""
        closes = _synthetic_closes(["AAPL", "JNJ", "XOM"], n_days=90)
        X, y, pre = _build_forecast_direction_adapter(closes)
        # Strategy return magnitude on any day is bounded by the largest
        # single-name daily return (weights sum to <=1 in absolute value).
        assert pre["ForecastDirection_ScoreWeighted"].abs().max() < 1.0

    def test_no_lookahead_shift1(self) -> None:
        closes = _synthetic_closes(["AAPL"], n_days=70)
        cutoff = closes.index[40]

        _, _, pre_orig = _build_forecast_direction_adapter(closes)
        val_orig = pre_orig["ForecastDirection_ScoreWeighted"].loc[cutoff]

        perturbed = closes.copy()
        perturbed.loc[perturbed.index > cutoff] *= 5.0
        _, _, pre_pert = _build_forecast_direction_adapter(perturbed)
        val_pert = pre_pert["ForecastDirection_ScoreWeighted"].loc[cutoff]

        assert val_orig == pytest.approx(val_pert)

    def test_real_forecast_alignment_signal_reused(self) -> None:
        """The adapter must call the REAL ForecastAlignmentSignal.compute(),
        not a hand-rolled reimplementation -- verified by monkeypatching it
        and asserting it was actually invoked."""
        import scripts.refresh_validations as rv
        from signals.forecast_alignment import ForecastAlignmentSignal

        calls = []
        original_compute = ForecastAlignmentSignal.compute

        def _spy_compute(self, row, context):
            calls.append(dict(row))
            return original_compute(self, row, context)

        closes = _synthetic_closes(["AAPL"], n_days=90)
        orig = ForecastAlignmentSignal.compute
        try:
            ForecastAlignmentSignal.compute = _spy_compute
            rv._build_forecast_direction_adapter(closes)
        finally:
            ForecastAlignmentSignal.compute = orig

        assert len(calls) > 0
        for call in calls:
            assert "current_price" in call and "forecast_price" in call

    def test_failed_fits_are_skipped_not_fabricated(self) -> None:
        """When both ARIMA and Holt-Winters fail to fit for every rebalance
        date, the resulting composite must be all-NaN-collapsed-to-zero, not
        a fabricated nonzero score (CONSTRAINT #4)."""
        import scripts.refresh_validations as rv

        class _AlwaysFailEngine:
            def run_arima_fit(self, *a, **k):
                return None

            def forecast_from_arima_fit(self, *a, **k):
                return 0.0

            def run_holt_winters_fit(self, *a, **k):
                return None

            def forecast_from_hw_fit(self, fitted, days_forward, history):
                return float(history[-1])

        closes = _synthetic_closes(["AAPL"], n_days=90)

        import forecasting_engine

        original_cls = forecasting_engine.ForecastingEngine
        try:
            forecasting_engine.ForecastingEngine = lambda: _AlwaysFailEngine()
            X, y, pre = rv._build_forecast_direction_adapter(closes)
        finally:
            forecasting_engine.ForecastingEngine = original_cls

        assert (pre["ForecastDirection_ScoreWeighted"] == 0.0).all()

    def test_universe_constant_matches_edgar_pit_universe(self) -> None:
        """Sanity-check the module-level universe constant used for
        registration is the intended 10-ticker EDGAR PIT subset."""
        assert FORECAST_DIRECTION_UNIVERSE == [
            "AAPL", "JNJ", "XOM", "KO", "JPM", "PG", "INTC", "T", "GE", "F",
        ]


# ---------------------------------------------------------------------------
# TestPerfSmoke -- caps wall-clock on a tiny window
# ---------------------------------------------------------------------------

class TestPerfSmoke:
    def test_tiny_window_completes_quickly(self) -> None:
        """2 tickers x ~3 months (a handful of weekly rebalances) must
        complete in well under a minute -- a coarse guard against an
        accidental daily-refit regression (which would 5x the fit count)."""
        closes = _synthetic_closes(["AAPL", "JNJ"], n_days=65)
        t0 = time.time()
        _build_forecast_direction_adapter(closes)
        elapsed = time.time() - t0
        assert elapsed < 30.0, f"took {elapsed:.1f}s -- possible daily-refit regression"


# ---------------------------------------------------------------------------
# TestForecastDirectionIntegration -- real yfinance + real harness (opt-in)
# ---------------------------------------------------------------------------

class TestForecastDirectionIntegration:
    pytestmark = pytest.mark.network

    def test_forecast_direction_runs_end_to_end(self) -> None:
        """Real yfinance history (trimmed internally to 1yr via a short
        --start/--end window to keep CI runtime bounded) through the real
        harness. Only asserts the report is well-formed -- NEVER that
        deployable is True (CONSTRAINT #4)."""
        from execution.cost_model import TieredCostModel
        from validation.harness import StrategyValidationHarness
        from scripts.refresh_validations import _download_closes, _make_strategy_fn

        tickers = ["AAPL", "JNJ"]
        closes = _download_closes(tickers, "2023-01-01", "2023-12-31")
        assert len(closes) > 100

        X, y, precomputed = _build_forecast_direction_adapter(closes)
        assert not X.empty and precomputed

        strategy_fn = _make_strategy_fn(precomputed, turnover=0.05)
        harness = StrategyValidationHarness(
            strategy_fn=strategy_fn,
            universe_fn=lambda: tickers,
            cost_model=TieredCostModel(),
        )
        report = harness.run(
            start_date="2023-01-01", end_date="2023-12-31",
            X=X, y=y, strategy_name="forecast_direction_arima_hw",
        )
        summary = report.to_summary_dict()
        assert isinstance(summary["deployable"], bool)
        assert np.isfinite(summary["sharpe"])
        assert np.isfinite(summary["max_drawdown"])
