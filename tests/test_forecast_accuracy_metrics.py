"""Unit tests for validation/forecast_accuracy_metrics.py (MASE/RMSE)."""

import numpy as np
import pytest

from validation.forecast_accuracy_metrics import (
    mase,
    naive_one_step_mae,
    rmse,
    rmse_from_errors,
    interval_coverage,
    pinball_loss,
    naive_volatility_baseline,
)
from validation.sector_forecast_types import ForecastError


class TestRMSE:
    def test_hand_computed(self):
        y_true = np.array([100.0, 200.0, 300.0])
        y_pred = np.array([110.0, 190.0, 300.0])
        # errors: 10, -10, 0 -> squared: 100,100,0 -> mean=66.667 -> sqrt=8.165
        assert rmse(y_true, y_pred) == pytest.approx(8.164965, rel=1e-5)

    def test_perfect_forecast_is_zero(self):
        y_true = np.array([1.0, 2.0, 3.0])
        assert rmse(y_true, y_true.copy()) == 0.0

    def test_empty_is_nan(self):
        assert np.isnan(rmse(np.array([]), np.array([])))

    def test_nans_dropped_pairwise(self):
        y_true = np.array([100.0, np.nan, 300.0])
        y_pred = np.array([110.0, 190.0, 300.0])
        # only index 0 and 2 are finite pairs: errors 10, 0
        assert rmse(y_true, y_pred) == pytest.approx(np.sqrt((100 + 0) / 2), rel=1e-6)

    def test_shape_mismatch_raises(self):
        with pytest.raises(ValueError):
            rmse(np.array([1.0, 2.0]), np.array([1.0]))


class TestNaiveOneStepMAE:
    def test_ramp(self):
        # constant step of 2 each day -> mean(|diff|) == 2
        prices = np.array([100.0, 102.0, 104.0, 106.0, 108.0])
        assert naive_one_step_mae(prices) == pytest.approx(2.0)

    def test_flat_series_floors(self):
        prices = np.full(10, 50.0)
        assert naive_one_step_mae(prices) == pytest.approx(1e-9)

    def test_too_few_points_floors(self):
        assert naive_one_step_mae(np.array([100.0])) == pytest.approx(1e-9)
        assert naive_one_step_mae(np.array([])) == pytest.approx(1e-9)

    def test_nans_ignored(self):
        prices = np.array([100.0, np.nan, 104.0, 106.0])
        # after dropping nan: [100,104,106] -> diffs [4,2] -> mean 3
        assert naive_one_step_mae(prices) == pytest.approx(3.0)


class TestMASE:
    def test_perfect_forecast_is_zero(self):
        errors = [ForecastError(y_true=100.0, y_pred=100.0, naive_scale=2.0) for _ in range(5)]
        assert mase(errors) == pytest.approx(0.0)

    def test_naive_equivalent_forecast_is_about_one(self):
        # error magnitude equals the naive scale exactly -> MASE == 1
        errors = [ForecastError(y_true=100.0, y_pred=98.0, naive_scale=2.0) for _ in range(5)]
        assert mase(errors) == pytest.approx(1.0)

    def test_empty_is_nan(self):
        assert np.isnan(mase([]))

    def test_scale_floor_guard(self):
        # naive_scale of 0 must not divide-by-zero; floored to _MIN_SCALE
        errors = [ForecastError(y_true=100.0, y_pred=99.0, naive_scale=0.0)]
        result = mase(errors)
        assert np.isfinite(result)
        assert result > 0

    def test_mixed_observations_averaged(self):
        errors = [
            ForecastError(y_true=100.0, y_pred=100.0, naive_scale=2.0),  # 0
            ForecastError(y_true=100.0, y_pred=98.0, naive_scale=2.0),   # 1
        ]
        assert mase(errors) == pytest.approx(0.5)

    def test_non_finite_observations_dropped(self):
        errors = [
            ForecastError(y_true=100.0, y_pred=100.0, naive_scale=2.0),
            ForecastError(y_true=float("nan"), y_pred=98.0, naive_scale=2.0),
        ]
        assert mase(errors) == pytest.approx(0.0)


class TestRMSEFromErrors:
    def test_matches_direct_rmse(self):
        errors = [
            ForecastError(y_true=100.0, y_pred=110.0, naive_scale=1.0),
            ForecastError(y_true=200.0, y_pred=190.0, naive_scale=1.0),
            ForecastError(y_true=300.0, y_pred=300.0, naive_scale=1.0),
        ]
        assert rmse_from_errors(errors) == pytest.approx(8.164965, rel=1e-5)

    def test_empty_is_nan(self):
        assert np.isnan(rmse_from_errors([]))


class TestIntervalCoverage:
    def test_perfect_coverage(self):
        y_true = np.array([1.5, 2.0, 2.5])
        y_lower = np.array([1.0, 1.0, 2.0])
        y_upper = np.array([2.0, 3.0, 3.0])
        assert interval_coverage(y_true, y_lower, y_upper) == pytest.approx(1.0)

    def test_partial_coverage(self):
        y_true = np.array([0.5, 2.0, 3.5])
        y_lower = np.array([1.0, 1.0, 2.0])
        y_upper = np.array([2.0, 3.0, 3.0])
        # Only index 1 is covered
        assert interval_coverage(y_true, y_lower, y_upper) == pytest.approx(1/3)

    def test_shape_mismatch_raises(self):
        with pytest.raises(ValueError):
            interval_coverage(np.array([1.0]), np.array([1.0, 2.0]), np.array([1.0, 2.0]))

    def test_nans_dropped_pairwise(self):
        y_true = np.array([1.5, np.nan, 2.5])
        y_lower = np.array([1.0, 1.0, 2.0])
        y_upper = np.array([2.0, 3.0, 3.0])
        assert interval_coverage(y_true, y_lower, y_upper) == pytest.approx(1.0)


class TestPinballLoss:
    def test_median_is_mae(self):
        y_true = np.array([1.0, 2.0, 3.0])
        y_pred = np.array([1.5, 1.5, 3.5])
        # errors: -0.5, 0.5, -0.5 -> max(0.5e, -0.5e) -> 0.25, 0.25, 0.25 -> mean=0.25
        # MAE is 0.5, 0.5, 0.5 -> mean=0.5. Pinball loss at 0.5 is half of MAE.
        assert pinball_loss(y_true, y_pred, 0.5) == pytest.approx(0.25)

    def test_quantile_asymmetry(self):
        y_true = np.array([1.0])
        y_pred = np.array([0.0]) # underprediction, e=1.0
        # alpha=0.9 -> max(0.9*1, -0.1*1) = 0.9
        assert pinball_loss(y_true, y_pred, 0.9) == pytest.approx(0.9)
        
        y_pred2 = np.array([2.0]) # overprediction, e=-1.0
        # alpha=0.9 -> max(0.9*-1, -0.1*-1) = 0.1
        assert pinball_loss(y_true, y_pred2, 0.9) == pytest.approx(0.1)


class TestNaiveVolatilityBaseline:
    def test_standard(self):
        # constant trend prices
        prices = np.array([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
        # Returns: 0.01, 0.0099, 0.0098, 0.0097, 0.0096
        # Expected std is non-zero, very small.
        vol = naive_volatility_baseline(prices)
        assert vol > 0.0
        assert np.isfinite(vol)

    def test_degenerate_flat(self):
        prices = np.full(10, 100.0)
        # Returns: 0.0, 0.0, ... std is 0.
        vol = naive_volatility_baseline(prices)
        assert vol == pytest.approx(1e-9)

    def test_insufficient_data(self):
        assert naive_volatility_baseline(np.array([100.0])) == pytest.approx(1e-9)
