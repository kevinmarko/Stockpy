"""Unit tests for the validation harness's persisted equity curve.

Fully offline: exercises the pure ``_build_equity_curve`` helper and the
``ValidationReport.to_summary_dict()`` contract directly (no yfinance, no real
backtest). The curve feeds the Pilots PWA performance chart via
``pilots/performance.py``; these tests lock in its shape and honesty rules.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from validation.harness import (
    MAX_EQUITY_CURVE_POINTS,
    ValidationReport,
    _build_equity_curve,
)


def _dummy_report(**overrides):
    """Construct a ValidationReport with the minimum required positional args."""
    kwargs = dict(
        name="unit",
        start_date="2020-01-01",
        end_date="2024-12-31",
        sharpe=1.0,
        sortino=1.0,
        calmar=1.0,
        max_dd=0.1,
        turnover=0.05,
        hit_rate=0.55,
        avg_trade_pct=0.001,
        dsr=0.96,
        pbo=0.2,
        bias_report={},
        walk_forward_60_40=1.0,
        walk_forward_70_30=1.0,
        walk_forward_80_20=1.0,
        distribution=np.array([1.0, 1.1]),
        paths=[],
        n_trials=10,
    )
    kwargs.update(overrides)
    return ValidationReport(**kwargs)


class TestBuildEquityCurve:
    def test_base_100_ascending_from_positive_returns(self):
        idx = pd.date_range("2020-01-01", periods=250, freq="B")
        r = pd.Series(0.001, index=idx)  # constant positive drift
        curve = _build_equity_curve(r)
        assert curve, "a real return series must yield a curve"
        assert curve[0]["value"] > 100.0  # (1.001)^1 * 100 after first compound
        assert curve[-1]["value"] > curve[0]["value"]
        assert all(set(p) == {"date", "value"} for p in curve)

    def test_downsampled_to_cap(self):
        idx = pd.date_range("2015-01-01", periods=2000, freq="B")
        rng = np.random.default_rng(1)
        r = pd.Series(rng.normal(0.0004, 0.01, size=2000), index=idx)
        curve = _build_equity_curve(r)
        assert 2 <= len(curve) <= MAX_EQUITY_CURVE_POINTS
        # dates are ISO and strictly increasing
        dates = [p["date"] for p in curve]
        assert dates == sorted(dates)

    def test_all_zero_returns_empty(self):
        idx = pd.date_range("2020-01-01", periods=100, freq="B")
        assert _build_equity_curve(pd.Series(0.0, index=idx)) == []

    def test_empty_returns_empty(self):
        assert _build_equity_curve(pd.Series([], dtype=float)) == []

    def test_none_returns_empty(self):
        assert _build_equity_curve(None) == []

    def test_nans_are_dropped_not_fabricated(self):
        idx = pd.date_range("2020-01-01", periods=100, freq="B")
        r = pd.Series(0.001, index=idx)
        r.iloc[:10] = np.nan
        curve = _build_equity_curve(r)
        assert curve  # remaining 90 points still build a curve
        assert all(np.isfinite(p["value"]) for p in curve)


class TestSummaryContract:
    def test_to_summary_dict_emits_equity_curve(self):
        pts = [
            {"date": "2020-01-31", "value": 100.0},
            {"date": "2020-02-28", "value": 101.5},
        ]
        summary = _dummy_report(equity_curve=pts).to_summary_dict()
        assert summary["equity_curve"] == pts

    def test_absent_curve_defaults_to_empty_list(self):
        # No equity_curve passed -> [] (never None/missing), so consumers can
        # rely on the key existing.
        summary = _dummy_report().to_summary_dict()
        assert summary["equity_curve"] == []
