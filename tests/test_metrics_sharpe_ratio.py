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

from validation.metrics import sharpe_ratio, describe_signal_sparsity


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


class TestDescribeSignalSparsity:
    """Regression tests for the self-diagnosing "insufficient trading signal"
    note (validation/metrics.py::describe_signal_sparsity), which explains
    the exact NaN-Sharpe condition tested above -- causally gated on the SAME
    degenerate-std guard, so it fires if and only if sharpe_ratio() would
    itself have returned NaN from this series.

    Real incident this documents: `python -m scripts.refresh_validations
    --strategies put_credit_spread,call_credit_spread` reported
    deployable=false, pbo=NaN, dsr=NaN, sharpe=null, max_drawdown=0.24 with no
    explanation -- the strategy's gate (true_ivr>50 AND VRP>threshold AND
    VIX<30 AND directional trend_bias) essentially never matched over a
    20-year SPY backtest, so its raw per-day return series was all exactly
    0.0; StrategyValidationHarness._apply_cost_model's flat per-day turnover
    cost then produced a numerically-constant series (see
    TestHarnessDegenerateReturnsEndToEnd above) whose compounding cost drag
    over ~4900 zero-trading days independently reproduced the reported
    max_drawdown≈0.24. See docs/VALIDATION_STRATEGY_FIX_LOG.md's entry for
    this incident for the full root-cause writeup.
    """

    def test_all_zero_returns_reports_zero_of_n_nonzero(self):
        returns = pd.Series([0.0] * 239)
        note = describe_signal_sparsity(returns)
        assert note is not None
        assert "insufficient trading signal" in note
        assert "0/239" in note

    def test_dense_real_returns_produce_no_note(self):
        """A real, non-degenerate strategy's returns -- even if the strategy
        itself turns out to have a poor Sharpe -- must not be flagged."""
        rng = np.random.default_rng(7)
        returns = pd.Series(rng.normal(0.0002, 0.0005, size=2000))
        assert describe_signal_sparsity(returns) is None

    def test_empty_or_none_returns_no_note(self):
        assert describe_signal_sparsity(None) is None
        assert describe_signal_sparsity(pd.Series(dtype=float)) is None
        assert describe_signal_sparsity(pd.Series([0.001])) is None

    def test_constant_after_flat_cost_deduction_reports_zero_nonzero(self):
        """The exact real-world shape: an all-zero raw book (this function
        should be called on the RAW pre-cost series, but confirms the guard
        condition is met the same way sharpe_ratio's is)."""
        n = 5000
        daily_cost = 0.05 * (11.0 / 10000.0)
        returns = pd.Series([0.0] * n) - daily_cost
        assert returns.std() < 1e-12  # confirm premise, matches sharpe_ratio's own guard
        note = describe_signal_sparsity(returns)
        assert note is not None
        # Every entry is `-daily_cost` (nonzero), not a true 0.0 fill -- this
        # is why the harness computes the note off the RAW pre-cost series,
        # not this cost-adjusted one (see validation/harness.py::run()).
        assert f"{n}/{n}" in note

    def test_sparse_but_nonzero_reports_actual_count(self):
        """A handful of genuinely nonzero (> the function's own 1e-9 "real
        signal" floor) observations that still collectively fail the
        degenerate-std guard -- the rarer "some signal, still not
        measurable" branch, distinct from the "0 nonzero" branch above.

        Getting BOTH conditions to hold simultaneously (nonzero by the 1e-9
        floor, yet std < 1e-12) needs a very large n -- std ~ sqrt(k/n)*v, so
        for v just above 1e-9 to keep std below 1e-12, n/k must exceed
        roughly 1e6. This branch is therefore not reachable in practice with
        this codebase's actual backtest sizes (at most tens of thousands of
        daily observations) -- included as a defensive generalization, not
        because it fires on real data (see the "0/N" test above for the
        realistic case)."""
        n = 5_000_000
        v = 1.2e-9
        returns = pd.Series([0.0] * n)
        returns.iloc[0] = v
        returns.iloc[1] = -v
        assert returns.std() < 1e-12
        note = describe_signal_sparsity(returns)
        assert note is not None
        assert "0/" not in note
        assert f"2/{n}" in note
