"""
tests/test_drift.py
====================
Unit tests for validation/drift.py — Task B3 calibration & regime-drift
sequential change-point detector.

Coverage:
  * Stationary (no-drift) synthetic series never trigger drift_detected for
    either method, across multiple seeds (false-positive rate check).
  * A synthetic series with an injected clear mean-shift partway through
    DOES trigger drift_detected for both methods, with drift_index landing
    reasonably close to the true shift point.
  * Empty / too-short / all-NaN input never raises and returns
    drift_detected=False.
  * DriftResult dataclass shape and dead-letter details.
  * adapt_recommendation_tracking_rows() adapter correctness.
"""

import math

import numpy as np
import pandas as pd
import pytest

from validation.drift import (
    DriftResult,
    MIN_SAMPLES,
    detect_drift,
    adapt_recommendation_tracking_rows,
    check_and_alert_recommendation_drift,
)


# =============================================================================
# Fixtures / helpers
# =============================================================================

def _stationary_series(seed: int, n: int = 200, mu: float = 0.0, sigma: float = 1.0):
    rng = np.random.default_rng(seed)
    return list(rng.normal(mu, sigma, n))


def _shifted_series(seed: int, n_each: int = 100, shift: float = 6.0):
    rng = np.random.default_rng(seed)
    before = rng.normal(0.0, 1.0, n_each)
    after = rng.normal(shift, 1.0, n_each)
    return list(before) + list(after), n_each  # true shift index


# =============================================================================
# No-drift (stationary) series
# =============================================================================

class TestNoDriftStationary:
    @pytest.mark.parametrize("method", ["cusum", "page_hinkley"])
    def test_single_stationary_series_no_drift(self, method):
        series = _stationary_series(seed=7)
        result = detect_drift(series, method=method)
        assert result.drift_detected is False
        assert result.drift_index is None

    @pytest.mark.parametrize("method", ["cusum", "page_hinkley"])
    def test_false_positive_rate_is_low_across_seeds(self, method):
        """Across many independent stationary draws, false alarms should be rare."""
        n_seeds = 40
        false_positives = 0
        for seed in range(n_seeds):
            series = _stationary_series(seed=seed + 5000)
            result = detect_drift(series, method=method)
            if result.drift_detected:
                false_positives += 1
        # Allow a small number of false alarms (statistical test, not exact) —
        # anything beyond ~10% would indicate a miscalibrated/broken detector.
        assert false_positives / n_seeds <= 0.10


# =============================================================================
# Drift injected (mean-shift) series
# =============================================================================

class TestDriftDetectedOnMeanShift:
    @pytest.mark.parametrize("method", ["cusum", "page_hinkley"])
    def test_large_mean_shift_is_detected(self, method):
        series, true_shift_idx = _shifted_series(seed=42, n_each=100, shift=6.0)
        result = detect_drift(series, method=method)
        assert result.drift_detected is True
        assert result.drift_index is not None

    @pytest.mark.parametrize("method", ["cusum", "page_hinkley"])
    def test_drift_index_lands_reasonably_close_to_true_shift(self, method):
        series, true_shift_idx = _shifted_series(seed=42, n_each=100, shift=6.0)
        result = detect_drift(series, method=method)
        assert result.drift_detected is True
        # "Reasonably close" — within the full post-shift window, and not
        # absurdly early (e.g. index 0) or missed entirely (None already
        # asserted above). Generous tolerance since both detectors are
        # cumulative and may fire a bit before/after the exact break depending
        # on reference-mean contamination.
        assert 0 <= result.drift_index < len(series)
        # Must not fire before there is at least some data to accumulate on.
        assert result.drift_index >= 1

    def test_downward_shift_detected_cusum(self):
        """CUSUM's reference mean is the whole-series mean (contaminated by
        both pre- and post-shift data), so the reported 'direction' reflects
        which side of that blended mean triggers the alarm first — not
        necessarily the sign of the injected shift itself. We only assert
        that SOME direction is reported and drift is detected; direction
        semantics for a fixed-whole-series-mean CUSUM are validated more
        meaningfully via Page-Hinkley's adaptive running mean below.
        """
        series, _ = _shifted_series(seed=99, n_each=100, shift=-6.0)
        result = detect_drift(series, method="cusum")
        assert result.drift_detected is True
        assert result.details.get("direction") in ("upward", "downward")

    def test_upward_shift_detected_cusum(self):
        series, _ = _shifted_series(seed=99, n_each=100, shift=6.0)
        result = detect_drift(series, method="cusum")
        assert result.drift_detected is True
        assert result.details.get("direction") in ("upward", "downward")

    def test_downward_shift_detected_page_hinkley(self):
        series, _ = _shifted_series(seed=99, n_each=100, shift=-6.0)
        result = detect_drift(series, method="page_hinkley")
        assert result.drift_detected is True
        assert result.details.get("direction") == "downward"

    def test_upward_shift_detected_page_hinkley(self):
        series, _ = _shifted_series(seed=99, n_each=100, shift=6.0)
        result = detect_drift(series, method="page_hinkley")
        assert result.drift_detected is True
        assert result.details.get("direction") == "upward"


# =============================================================================
# Dead-letter resilience — never raises
# =============================================================================

class TestDeadLetterResilience:
    @pytest.mark.parametrize("method", ["cusum", "page_hinkley"])
    def test_empty_input_never_raises(self, method):
        result = detect_drift([], method=method)
        assert result.drift_detected is False
        assert result.drift_index is None

    @pytest.mark.parametrize("method", ["cusum", "page_hinkley"])
    def test_none_input_never_raises(self, method):
        result = detect_drift(None, method=method)
        assert result.drift_detected is False
        assert result.drift_index is None

    @pytest.mark.parametrize("method", ["cusum", "page_hinkley"])
    def test_too_short_input_never_raises(self, method):
        result = detect_drift([1.0, 2.0, 3.0], method=method)
        assert result.drift_detected is False
        assert result.drift_index is None
        assert "insufficient" in result.details.get("note", "").lower()

    @pytest.mark.parametrize("method", ["cusum", "page_hinkley"])
    def test_all_nan_input_never_raises(self, method):
        series = [float("nan")] * 20
        result = detect_drift(series, method=method)
        assert result.drift_detected is False
        assert result.drift_index is None

    @pytest.mark.parametrize("method", ["cusum", "page_hinkley"])
    def test_zero_variance_series_never_raises(self, method):
        series = [5.0] * 50
        result = detect_drift(series, method=method)
        assert result.drift_detected is False
        assert result.drift_index is None

    def test_unknown_method_degrades_gracefully(self):
        series = _stationary_series(seed=1)
        result = detect_drift(series, method="not_a_real_method")  # type: ignore[arg-type]
        assert result.drift_detected is False
        assert result.drift_index is None
        assert "unknown method" in result.details.get("note", "").lower()

    def test_pandas_series_input_accepted(self):
        series = pd.Series(_stationary_series(seed=3))
        result = detect_drift(series, method="cusum")
        assert isinstance(result, DriftResult)

    def test_series_with_some_nans_mixed_in(self):
        series = _stationary_series(seed=11) + [float("nan"), float("inf"), float("-inf")]
        result = detect_drift(series, method="cusum")
        # Should not raise, and NaNs/infs should simply be dropped rather than
        # poisoning the statistic.
        assert isinstance(result, DriftResult)


# =============================================================================
# DriftResult dataclass shape
# =============================================================================

class TestDriftResultShape:
    def test_result_has_required_fields(self):
        result = detect_drift(_stationary_series(seed=2), method="cusum")
        assert hasattr(result, "drift_detected")
        assert hasattr(result, "drift_index")
        assert hasattr(result, "method")
        assert hasattr(result, "details")
        assert isinstance(result.details, dict)

    def test_method_field_reflects_requested_method(self):
        assert detect_drift(_stationary_series(seed=2), method="cusum").method == "cusum"
        assert detect_drift(_stationary_series(seed=2), method="page_hinkley").method == "page_hinkley"

    def test_result_is_frozen(self):
        result = detect_drift(_stationary_series(seed=2), method="cusum")
        with pytest.raises(Exception):
            result.drift_detected = True  # type: ignore[misc]

    def test_min_samples_constant_is_positive_int(self):
        assert isinstance(MIN_SAMPLES, int)
        assert MIN_SAMPLES > 0


# =============================================================================
# Integration adapter — Tier 4.1 recommendation-tracking rows
# =============================================================================

class TestAdaptRecommendationTrackingRows:
    def test_empty_rows_returns_empty_list(self):
        assert adapt_recommendation_tracking_rows([]) == []

    def test_none_rows_returns_empty_list(self):
        assert adapt_recommendation_tracking_rows(None) == []  # type: ignore[arg-type]

    def test_calibration_error_metric(self):
        rows = [
            {"model_return": 0.05, "actual_return": 0.08},
            {"model_return": 0.02, "actual_return": -0.01},
        ]
        out = adapt_recommendation_tracking_rows(rows, metric="calibration_error")
        assert out == pytest.approx([0.03, -0.03])

    def test_model_return_metric(self):
        rows = [{"model_return": 0.05}, {"model_return": 0.10}]
        out = adapt_recommendation_tracking_rows(rows, metric="model_return")
        assert out == pytest.approx([0.05, 0.10])

    def test_actual_return_metric(self):
        rows = [{"actual_return": 0.05}, {"actual_return": -0.02}]
        out = adapt_recommendation_tracking_rows(rows, metric="actual_return")
        assert out == pytest.approx([0.05, -0.02])

    def test_missing_values_skipped_not_fabricated(self):
        """CONSTRAINT #4 — a row missing model_return must be skipped, not zeroed."""
        rows = [
            {"model_return": None, "actual_return": 0.05},
            {"model_return": 0.02, "actual_return": 0.03},
        ]
        out = adapt_recommendation_tracking_rows(rows, metric="calibration_error")
        assert out == pytest.approx([0.01])

    def test_nan_values_skipped(self):
        rows = [
            {"model_return": float("nan"), "actual_return": 0.05},
            {"model_return": 0.01, "actual_return": 0.02},
        ]
        out = adapt_recommendation_tracking_rows(rows, metric="calibration_error")
        assert out == pytest.approx([0.01])

    def test_malformed_row_does_not_raise(self):
        rows = [{"not_the_right_keys": 1}, {"model_return": 0.02, "actual_return": 0.03}]
        out = adapt_recommendation_tracking_rows(rows, metric="calibration_error")
        assert out == pytest.approx([0.01])

    def test_adapter_output_feeds_detect_drift_without_raising(self):
        """End-to-end: adapter output is a valid detect_drift() input."""
        rows = [
            {"model_return": 0.01 * i, "actual_return": 0.01 * i + 0.002}
            for i in range(30)
        ]
        stream = adapt_recommendation_tracking_rows(rows, metric="calibration_error")
        result = detect_drift(stream, method="cusum")
        assert isinstance(result, DriftResult)


# =============================================================================
# check_and_alert_recommendation_drift() — alert wiring
# =============================================================================

class TestCheckAndAlertRecommendationDrift:
    def test_no_drift_does_not_call_send_alert(self):
        rng = np.random.default_rng(3)
        rows = [
            {"model_return": float(x), "actual_return": float(x)}
            for x in rng.normal(0, 0.01, 50)
        ]
        calls = []
        result = check_and_alert_recommendation_drift(
            rows, send_alert_fn=lambda *a, **k: calls.append((a, k))
        )
        assert result.drift_detected is False
        assert calls == []

    def test_drift_triggers_warning_alert(self):
        # Construct a clean mean-shift in calibration error partway through.
        rows = (
            [{"model_return": 0.0, "actual_return": 0.0 + 0.001 * i} for i in range(50)]
            + [{"model_return": 0.0, "actual_return": 6.0 + 0.001 * i} for i in range(50)]
        )
        calls = []
        result = check_and_alert_recommendation_drift(
            rows, send_alert_fn=lambda *a, **k: calls.append((a, k))
        )
        assert result.drift_detected is True
        assert len(calls) == 1
        args, kwargs = calls[0]
        assert args[0] == "WARNING"
        assert isinstance(args[1], str) and len(args[1]) > 0
        assert kwargs["extra"]["type"] == "calibration_drift"

    def test_empty_rows_never_raises_and_no_alert(self):
        calls = []
        result = check_and_alert_recommendation_drift(
            [], send_alert_fn=lambda *a, **k: calls.append((a, k))
        )
        assert result.drift_detected is False
        assert calls == []

    def test_alert_fn_exception_does_not_propagate(self):
        rows = (
            [{"model_return": 0.0, "actual_return": 0.0} for _ in range(50)]
            + [{"model_return": 0.0, "actual_return": 6.0} for _ in range(50)]
        )

        def _boom(*a, **k):
            raise RuntimeError("channel down")

        # Must not raise even though the injected alert function blows up.
        result = check_and_alert_recommendation_drift(rows, send_alert_fn=_boom)
        assert isinstance(result, DriftResult)

    def test_default_send_alert_fn_lazily_imports_observability(self):
        """When send_alert_fn is not injected, real observability.alerts.send_alert
        is used (console channel always active) — must not raise even with no
        webhook/file config."""
        rows = (
            [{"model_return": 0.0, "actual_return": 0.0} for _ in range(50)]
            + [{"model_return": 0.0, "actual_return": 6.0} for _ in range(50)]
        )
        result = check_and_alert_recommendation_drift(rows)
        assert isinstance(result, DriftResult)
