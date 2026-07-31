"""Regression tests for validation/metrics.py::sharpe_ratio's degenerate-std
guard.

Bug this locks in: a returns series that is exactly (or effectively)
constant -- e.g. an all-zero "no signal" book after
StrategyValidationHarness._apply_cost_model subtracts a flat per-day cost --
is mathematically constant, but pandas' Series.std() accumulates
floating-point rounding noise over many rows and lands near (not
bit-identical to) 0.0. The old code only guarded the exact-equality case
(``std_ret == 0``), so a near-zero-but-nonzero std slipped through and
``mean_ret / std_ret`` exploded into an absurd, unbounded "Sharpe" -- this is
exactly how the Pilots PWA's "Regime Navigator" marketplace card once
surfaced a Sharpe ratio of ~-7.7e16 (CONSTRAINT #4 violation: a genuinely
degenerate computation must degrade to NaN, never a garbage number).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from validation.metrics import sharpe_ratio


class TestDegenerateStdGuard:
    def test_exactly_zero_returns_is_nan(self):
        """Bit-identical zeros: std is exactly 0.0 -- already handled pre-fix,
        kept as a baseline."""
        returns = pd.Series([0.0] * 5000)
        assert np.isnan(sharpe_ratio(returns))

    def test_constant_series_after_flat_cost_deduction_is_nan(self):
        """The actual reported bug: an all-zero book run through a flat
        per-day cost deduction (turnover * cost_rate), the same operation
        StrategyValidationHarness._apply_cost_model performs. Reproduces the
        real magnitude (~-7.7e16) that used to leak through."""
        n = 5000
        daily_cost = 0.03 * (11.0 / 10000.0)  # turnover=0.03, 11bps round-trip
        returns = pd.Series([0.0] * n) - daily_cost
        # Confirm the premise: std is nonzero (floating noise), not exactly 0.0.
        assert returns.std() != 0.0
        assert returns.std() < 1e-12
        result = sharpe_ratio(returns)
        assert np.isnan(result), f"expected NaN, got an absurd value: {result}"

    def test_genuine_low_but_real_variance_is_not_treated_as_degenerate(self):
        """A real strategy's daily-return std is always many orders of
        magnitude above the 1e-12 noise floor -- the guard must not misfire
        on legitimate low-volatility returns."""
        idx = pd.RangeIndex(2000)
        rng = np.random.default_rng(11)
        returns = pd.Series(rng.normal(0.0002, 0.0005, size=len(idx)), index=idx)
        result = sharpe_ratio(returns)
        assert np.isfinite(result)
        assert abs(result) < 50  # sane order of magnitude, not astronomical

    def test_fewer_than_two_observations_is_nan(self):
        assert np.isnan(sharpe_ratio(pd.Series([0.001])))

    def test_nan_std_is_nan(self):
        assert np.isnan(sharpe_ratio(pd.Series([np.nan, np.nan, np.nan])))


class TestHarnessDegenerateReturnsEndToEnd:
    """The same guard exercised the way StrategyValidationHarness actually
    triggers it: a zero-signal strategy's raw returns are exactly 0.0, then
    _apply_cost_model subtracts a flat per-day cost, producing a
    numerically-constant negative series."""

    def test_apply_cost_model_on_all_zero_returns_yields_nan_sharpe(self):
        from validation.harness import StrategyValidationHarness
        from execution.cost_model import TieredCostModel

        harness = StrategyValidationHarness(
            strategy_fn=lambda *a, **k: [],
            universe_fn=lambda _d: [],
            cost_model=TieredCostModel(),
        )
        n = 5000
        zero_returns = pd.Series([0.0] * n)
        net_returns = harness._apply_cost_model(zero_returns, turnover=0.03)
        result = sharpe_ratio(net_returns)
        assert np.isnan(result), f"expected NaN, got an absurd value: {result}"
