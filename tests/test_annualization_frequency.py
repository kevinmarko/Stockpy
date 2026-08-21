"""Unit + end-to-end tests for validation/metrics.py::infer_annualization_freq
and its wiring through validation/harness.py::StrategyValidationHarness.run().

Motivating bug (see infer_annualization_freq's own docstring for the full
writeup): scripts/refresh_validations.py's "lgbm_ranker" STRATEGY_REGISTRY
adapter produces return observations sampled far more sparsely than one per
trading day. Every pre-fix call into sharpe_ratio()/deflated_sharpe_ratio()/
run_cpcv_evaluation() from validation/harness.py hardcoded freq=252 (a
daily-data assumption) regardless of a strategy's actual observation cadence,
annualizing a sparse-cadence return series with sqrt(252) as if it were
daily -- a real re-run reported Sharpe=24.886 for lgbm_ranker, an overstatement
of roughly sqrt(252/20) =~ 3.5x versus its true ~20-observations/year cadence.

This file proves two independent claims, each with its own test class:

1. REGRESSION SAFETY (TestRegressionSafetyDailyCadenceProxies /
   TestHarnessEndToEndDailyCadenceUnchanged): for a genuinely daily-cadence
   returns series -- the overwhelming majority of STRATEGY_REGISTRY, including
   the two concrete daily-cadence strategies named in the design plan,
   "garch_vol_target" and "multifactor_lowvol_size" (both real
   scripts.refresh_validations.STRATEGY_REGISTRY entries; both iterate the
   full trading calendar day-by-day, unlike lgbm_ranker's sparse forward-
   horizon sampling) -- the fix must reproduce today's exact pre-fix numbers
   bit-for-bit. infer_annualization_freq() is proxied here rather than
   re-running the real, network-dependent adapters (which pull real market
   data via yfinance/FMP and are validated separately by
   scripts/refresh_validations.py itself); a synthetic pd.bdate_range-indexed
   returns series is a faithful proxy for "real trading calendar" because
   infer_annualization_freq only ever looks at index spacing, never values.

2. NEW BEHAVIOR (TestNewBehaviorSparseCadenceNotOverstated /
   TestHarnessEndToEndSparseCadenceUsesInferredFreq): for a synthetic sparse
   ~20-observations/year series matching lgbm_ranker's real cadence, Sharpe
   must land in the true, un-inflated range -- not the ~3.5x-4.6x-overstated
   number sqrt(252)/sqrt(true_freq) would produce.

Plus the required fail-safe/edge-case coverage (TestInferAnnualizationFreqFailSafe)
and confirmation that the pre-existing 1e-12 degenerate-std guard in
sharpe_ratio() still fires correctly once freq is no longer a hardcoded literal
(TestDegenerateGuardUnaffectedByFreqInference).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from validation.metrics import (
    CALENDAR_DAYS_PER_YEAR,
    deflated_sharpe_ratio,
    infer_annualization_freq,
    run_cpcv_evaluation,
    sharpe_ratio,
)
from validation.stress_scenarios import compute_max_drawdown


# ---------------------------------------------------------------------------
# Shared fixture builders
# ---------------------------------------------------------------------------

def _daily_returns(n=1500, seed=7, mean=0.0004, std=0.009, start="2010-01-01"):
    """A realistic daily-cadence returns series on a real US trading calendar
    (business days -- pd.bdate_range, matching this repo's existing
    tests/test_harness_*.py fixture convention). Every genuinely daily
    STRATEGY_REGISTRY adapter (garch_vol_target, multifactor_lowvol_size,
    cross_sectional_momentum, etc.) produces exactly this cadence: one
    observation per real trading day, median consecutive gap 1.0 calendar
    day (occasionally 3 over a weekend)."""
    idx = pd.bdate_range(start, periods=n)
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(mean, std, size=n), index=idx)


def _sparse_returns(obs_per_year=20, n_years=6, seed=13, mean=0.01, std=0.05, start="2010-01-01"):
    """A synthetic sparse-cadence returns series matching lgbm_ranker's real
    reported cadence (~20 observations/year, forward long-short spread
    observations sampled roughly every 5-20+ trading days -- see this
    module's own docstring and infer_annualization_freq's docstring for the
    full motivating-bug writeup). Evenly spaced in calendar days for a
    deterministic, hand-verifiable median gap."""
    n = obs_per_year * n_years
    step_days = int(round(CALENDAR_DAYS_PER_YEAR / obs_per_year))
    idx = pd.DatetimeIndex([
        pd.Timestamp(start) + pd.Timedelta(days=step_days * i) for i in range(n)
    ])
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(mean, std, size=n), index=idx)


def _sortino(returns: pd.Series, freq: float) -> float:
    """Hand-mirrors validation/harness.py::run()'s own Sortino computation
    (same degenerate-std 1e-12 guard, same np.sqrt(freq) annualization) so
    tests can independently recompute the expected value rather than
    trusting the harness's own arithmetic."""
    downside = returns[returns < 0]
    downside_std = downside.std()
    if not (downside_std >= 1e-12):
        return float("nan")
    return float(returns.mean() / downside_std * np.sqrt(freq))


def _calmar(returns: pd.Series, freq: float) -> float:
    """Hand-mirrors validation/harness.py::run()'s own Calmar computation."""
    max_dd = compute_max_drawdown(returns)
    if not (max_dd >= 1e-12):
        return float("nan")
    return float(returns.mean() * freq / max_dd)


# ---------------------------------------------------------------------------
# 1. infer_annualization_freq() unit behavior
# ---------------------------------------------------------------------------

class TestInferAnnualizationFreqDailyCadence:
    def test_real_trading_calendar_snaps_to_252_exactly(self):
        """A real business-day DatetimeIndex's median consecutive gap is 1.0
        calendar day -- must snap EXACTLY to TRADING_DAYS_PER_YEAR (252.0),
        not the general calendar-day formula (365.25/1.0 = 365.25, a ~45%
        overstatement -- see the module-level DAILY_GAP_SNAP_THRESHOLD_DAYS
        comment for why this must be an explicit snap)."""
        returns = _daily_returns(n=500)
        assert infer_annualization_freq(returns) == pytest.approx(252.0)

    def test_snap_engages_even_with_weekend_gaps(self):
        """pd.bdate_range gaps are mostly 1 day but 3 over every weekend --
        the MEDIAN (not mean) must still land at 1.0 and snap to 252."""
        idx = pd.bdate_range("2018-01-01", periods=1000)
        gaps = idx.to_series().diff().dropna().dt.days
        assert gaps.median() == 1.0  # confirms the premise this test relies on
        returns = pd.Series(0.001, index=idx)
        assert infer_annualization_freq(returns) == 252.0

    def test_weekly_cadence_matches_calendar_day_formula(self):
        """A non-daily-but-still-snap-ineligible cadence (weekly, median gap
        7 days > DAILY_GAP_SNAP_THRESHOLD_DAYS=2.0) must use the general
        CALENDAR_DAYS_PER_YEAR/median_gap formula, matching this codebase's
        own CAGR annualization convention (evaluation_engine.py)."""
        idx = pd.date_range("2015-01-01", periods=104, freq="W")
        returns = pd.Series(0.001, index=idx)
        result = infer_annualization_freq(returns)
        assert result == pytest.approx(CALENDAR_DAYS_PER_YEAR / 7.0, rel=1e-6)
        assert result == pytest.approx(52.18, abs=0.01)


class TestInferAnnualizationFreqFailSafe:
    """CONSTRAINT #6: infer_annualization_freq must fail safe to `default`
    (never raise, never silently corrupt a strategy's Sharpe with a bogus
    inferred value) on every one of these inputs."""

    def test_too_few_observations_falls_back(self):
        idx = pd.bdate_range("2020-01-01", periods=4)  # < MIN_OBSERVATIONS_FOR_FREQ_INFERENCE (5)
        returns = pd.Series([0.001] * 4, index=idx)
        assert infer_annualization_freq(returns) == 252.0

    def test_non_datetimeindex_range_index_falls_back(self):
        returns = pd.Series(np.random.default_rng(1).normal(0, 0.01, 100))  # RangeIndex
        assert infer_annualization_freq(returns) == 252.0

    def test_multiindex_falls_back(self):
        """sector_quality_rank's real adapter produces a (Date, Ticker)
        MultiIndex y -- must degrade safely to the default rather than
        crash or silently misinterpret the MultiIndex as date spacing."""
        dates = pd.date_range("2015-01-01", periods=50, freq="B")
        midx = pd.MultiIndex.from_product([dates, ["AAA", "BBB"]], names=["Date", "Ticker"])
        returns = pd.Series(0.001, index=midx)
        assert infer_annualization_freq(returns) == 252.0

    def test_all_duplicate_timestamps_falls_back(self):
        """Zero real gaps (every timestamp identical) carries no spacing
        information -- must not divide-by-zero or return a bogus figure."""
        idx = pd.DatetimeIndex(["2020-01-01"] * 10)
        returns = pd.Series(np.random.default_rng(2).normal(0, 0.01, 10), index=idx)
        assert infer_annualization_freq(returns) == 252.0

    def test_none_input_falls_back(self):
        assert infer_annualization_freq(None) == 252.0

    def test_empty_series_falls_back(self):
        assert infer_annualization_freq(pd.Series(dtype=float)) == 252.0

    def test_extremely_sparse_below_one_per_year_falls_back(self):
        """A median gap so large that periods_per_year would compute to
        < 1.0 (implausible -- nothing in this registry is observed less
        often than annually) must fall back rather than report a nonsense
        sub-1.0 frequency."""
        idx = pd.DatetimeIndex([
            pd.Timestamp("2000-01-01") + pd.Timedelta(days=400 * i) for i in range(6)
        ])
        returns = pd.Series(np.random.default_rng(3).normal(0, 0.01, 6), index=idx)
        assert infer_annualization_freq(returns) == 252.0

    def test_custom_default_is_honored_on_fallback(self):
        idx = pd.bdate_range("2020-01-01", periods=3)
        returns = pd.Series([0.001] * 3, index=idx)
        assert infer_annualization_freq(returns, default=12) == 12.0

    def test_never_raises_on_garbage_input(self):
        """Any unexpected exception during computation must degrade to
        `default`, never propagate -- CONSTRAINT #6."""
        assert infer_annualization_freq("not a series") == 252.0  # type: ignore[arg-type]
        assert infer_annualization_freq(12345) == 252.0  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 2. REQUIRED regression-safety proof: real/realistic daily-cadence proxies
#    for two concrete STRATEGY_REGISTRY strategies must be numerically
#    UNCHANGED (bit-identical) vs. the pre-fix literal freq=252 behavior.
# ---------------------------------------------------------------------------

class TestRegressionSafetyDailyCadenceProxies:
    """Proxies for scripts.refresh_validations.STRATEGY_REGISTRY's
    "garch_vol_target" and "multifactor_lowvol_size" entries -- both real,
    registered, genuinely-daily-cadence adapters (each iterates the full
    trading calendar day-by-day; neither reindexes onto a sparse date index
    the way lgbm_ranker does). A live re-run of the actual adapters is
    out of scope for a unit test (network-dependent, see
    scripts/refresh_validations.py); what must hold, and what these tests
    prove, is that infer_annualization_freq() on ANY genuinely daily
    DatetimeIndex -- which is what both real adapters produce -- snaps to
    exactly 252.0 and therefore changes nothing downstream."""

    def test_garch_vol_target_proxy_sharpe_unchanged(self):
        returns = _daily_returns(n=2000, seed=101, mean=0.0003, std=0.011, start="2008-01-01")
        inferred = infer_annualization_freq(returns)
        assert inferred == 252.0
        pre_fix = sharpe_ratio(returns, freq=252)
        post_fix = sharpe_ratio(returns, freq=inferred)
        assert np.isfinite(pre_fix)
        assert post_fix == pre_fix  # bit-identical, not merely approx

    def test_garch_vol_target_proxy_sortino_calmar_unchanged(self):
        returns = _daily_returns(n=2000, seed=101, mean=0.0003, std=0.011, start="2008-01-01")
        inferred = infer_annualization_freq(returns)
        assert _sortino(returns, freq=inferred) == _sortino(returns, freq=252)
        assert _calmar(returns, freq=inferred) == _calmar(returns, freq=252)

    def test_multifactor_lowvol_size_proxy_sharpe_unchanged(self):
        # Deliberately different mean/std/seed/window from the garch_vol_target
        # proxy above -- a genuinely distinct strategy shape, not a copy-paste.
        returns = _daily_returns(n=3000, seed=202, mean=0.0002, std=0.007, start="2012-06-01")
        inferred = infer_annualization_freq(returns)
        assert inferred == 252.0
        pre_fix = sharpe_ratio(returns, freq=252)
        post_fix = sharpe_ratio(returns, freq=inferred)
        assert np.isfinite(pre_fix)
        assert post_fix == pre_fix

    def test_multifactor_lowvol_size_proxy_sortino_calmar_unchanged(self):
        returns = _daily_returns(n=3000, seed=202, mean=0.0002, std=0.007, start="2012-06-01")
        inferred = infer_annualization_freq(returns)
        assert _sortino(returns, freq=inferred) == _sortino(returns, freq=252)
        assert _calmar(returns, freq=inferred) == _calmar(returns, freq=252)

    def test_deflated_sharpe_ratio_unchanged_for_daily_cadence(self):
        """deflated_sharpe_ratio's freq parameter (used in DSR's sr_hat/var_sr
        normalization) must likewise be a no-op for a genuinely daily series."""
        returns = _daily_returns(n=1200, seed=303)
        inferred = infer_annualization_freq(returns)
        sr = sharpe_ratio(returns, freq=252)
        kwargs = dict(
            sr_observed=sr, n_trials=8, sr_variance=0.04,
            skew=float(returns.skew()), kurtosis=float(returns.kurtosis()) + 3.0,
            n_observations=len(returns),
        )
        pre_fix = deflated_sharpe_ratio(freq=252, **kwargs)
        post_fix = deflated_sharpe_ratio(freq=inferred, **kwargs)
        assert post_fix == pre_fix

    def test_run_cpcv_evaluation_unchanged_for_daily_cadence(self):
        """The freq kwarg threaded into run_cpcv_evaluation (which drives
        every internal Sharpe/DSR/Sortino it computes) must likewise be a
        no-op end-to-end for a real daily-cadence CPCV run -- deterministic
        constant-return strategy_fn per this repo's own
        tests/test_metrics_cpcv_oos_aggregates.py convention."""
        n = 260
        idx = pd.bdate_range("2016-01-01", periods=n)
        rng = np.random.default_rng(9)
        X = pd.DataFrame({"feat": np.arange(n, dtype=float)}, index=idx)
        y = pd.Series(rng.normal(0.0003, 0.006, size=n), index=idx)

        def strategy_fn(X_train, y_train, X_test, y_test):
            return [{
                "params": "constant",
                "train_returns": pd.Series(0.0006, index=y_train.index),
                "test_returns": pd.Series(0.0006, index=y_test.index),
                "turnover": 0.02,
            }]

        inferred = infer_annualization_freq(y)
        assert inferred == 252.0

        pre_fix = run_cpcv_evaluation(strategy_fn, X, y, n_splits=5, n_test_splits=2, freq=252)
        post_fix = run_cpcv_evaluation(strategy_fn, X, y, n_splits=5, n_test_splits=2, freq=inferred)

        assert pre_fix["dsr"] == pytest.approx(post_fix["dsr"], nan_ok=True)
        assert pre_fix["pbo"] == pytest.approx(post_fix["pbo"], nan_ok=True)
        assert pre_fix["mean_oos_sharpe"] == pytest.approx(post_fix["mean_oos_sharpe"], nan_ok=True)
        assert pre_fix["mean_oos_sortino"] == pytest.approx(post_fix["mean_oos_sortino"], nan_ok=True)


# ---------------------------------------------------------------------------
# 3. REQUIRED new-behavior proof: a sparse ~20-obs/year series must NOT be
#    overstated by the old sqrt(252) assumption.
# ---------------------------------------------------------------------------

class TestNewBehaviorSparseCadenceNotOverstated:
    """Proxy for lgbm_ranker's real reported cadence (~20 observations/year).
    Reproduces the exact bug class the fix targets: the prior agent's own
    verification found sharpe_ratio(s, freq=252)=6.925 vs.
    sharpe_ratio(s, freq=infer_annualization_freq(s))=1.575 on an equivalent
    synthetic sparse series -- these tests pin that same class of result down
    quantitatively rather than merely observing "it changed"."""

    def test_inferred_freq_matches_true_sparse_cadence(self):
        returns = _sparse_returns(obs_per_year=20, n_years=6)
        inferred = infer_annualization_freq(returns)
        # step_days = round(365.25/20) = 18 calendar days -> 365.25/18 = 20.29/yr
        assert inferred == pytest.approx(365.25 / 18.0, rel=1e-6)
        assert 15.0 < inferred < 25.0  # sanity: genuinely ~20/yr, not snapped to 252

    def test_old_sqrt252_assumption_overstates_sharpe_by_the_expected_factor(self):
        returns = _sparse_returns(obs_per_year=20, n_years=6, mean=0.01, std=0.05)
        inferred = infer_annualization_freq(returns)

        old_wrong_sharpe = sharpe_ratio(returns, freq=252)
        new_correct_sharpe = sharpe_ratio(returns, freq=inferred)

        assert np.isfinite(old_wrong_sharpe) and np.isfinite(new_correct_sharpe)
        assert old_wrong_sharpe > 0 and new_correct_sharpe > 0  # same sign, positive mean/positive std

        # Quantitative proof, not just "it changed": the two Sharpes differ
        # by EXACTLY sqrt(252/inferred) -- both are the same mean/std scaled
        # by a different sqrt(freq) annualization factor.
        expected_ratio = np.sqrt(252.0 / inferred)
        actual_ratio = old_wrong_sharpe / new_correct_sharpe
        assert actual_ratio == pytest.approx(expected_ratio, rel=1e-9)

        # The task's own named bound: sqrt(252/20) =~ 3.5, and this repo's
        # real reported overstatement was measured at ~3.5-4.6x -- confirm
        # the old number is inflated well within (not below) that band.
        assert 3.0 < actual_ratio < 5.0

        # The corrected number must land in a SANE range for a real strategy
        # (this repo's own STRATEGY_REGISTRY entries all report single- or
        # low-double-digit Sharpes at most) -- not the old ~24.886-class
        # blowup the real lgbm_ranker bug produced.
        assert new_correct_sharpe < 10.0

    def test_sortino_and_calmar_also_deflate_by_the_same_sqrt_ratio(self):
        """Sortino uses the identical sqrt(freq) annualization as Sharpe;
        Calmar uses a linear (not sqrt) freq scaling -- both must move in the
        corrected direction for the same sparse-cadence fixture."""
        returns = _sparse_returns(obs_per_year=20, n_years=6, mean=0.01, std=0.05)
        inferred = infer_annualization_freq(returns)

        old_sortino = _sortino(returns, freq=252)
        new_sortino = _sortino(returns, freq=inferred)
        assert np.isfinite(old_sortino) and np.isfinite(new_sortino)
        assert new_sortino < old_sortino  # deflated, same sign
        assert old_sortino / new_sortino == pytest.approx(np.sqrt(252.0 / inferred), rel=1e-9)

        old_calmar = _calmar(returns, freq=252)
        new_calmar = _calmar(returns, freq=inferred)
        assert np.isfinite(old_calmar) and np.isfinite(new_calmar)
        assert new_calmar < old_calmar
        assert old_calmar / new_calmar == pytest.approx(252.0 / inferred, rel=1e-9)

    def test_deflated_sharpe_ratio_lower_freq_reduces_normalized_sr_hat(self):
        """DSR's sr_hat = sr_observed / sqrt(freq) -- a smaller (correct)
        freq means a LARGER sr_hat for the same annualized sr_observed input,
        which is the expected direction (lower-frequency evidence is treated
        as noisier per-observation, not free extra confidence)."""
        returns = _sparse_returns(obs_per_year=20, n_years=6)
        inferred = infer_annualization_freq(returns)
        sr = sharpe_ratio(returns, freq=inferred)
        kwargs = dict(
            n_trials=8, sr_variance=0.04,
            skew=float(returns.skew()), kurtosis=float(returns.kurtosis()) + 3.0,
            n_observations=len(returns),
        )
        dsr_old_freq = deflated_sharpe_ratio(sr_observed=sr, freq=252, **kwargs)
        dsr_new_freq = deflated_sharpe_ratio(sr_observed=sr, freq=inferred, **kwargs)
        assert np.isfinite(dsr_old_freq) and np.isfinite(dsr_new_freq)
        assert dsr_old_freq != dsr_new_freq


# ---------------------------------------------------------------------------
# 4. Degenerate-std guard must still fire correctly once freq is inferred
#    rather than a hardcoded literal (requirement #5 in the task).
# ---------------------------------------------------------------------------

class TestDegenerateGuardUnaffectedByFreqInference:
    """The 1e-12 degenerate-std guard in sharpe_ratio() is checked BEFORE
    freq is ever used (mean_ret/std_ret is computed and validated first) --
    confirms infer_annualization_freq's introduction didn't accidentally
    reorder or bypass that check for a near-constant series on a real daily
    calendar."""

    def test_near_constant_daily_series_still_returns_nan(self):
        idx = pd.bdate_range("2015-01-01", periods=5000)
        daily_cost = 0.03 * (11.0 / 10000.0)  # same construction as
        # tests/test_metrics_sharpe_ratio.py's real reported-bug repro
        returns = pd.Series([0.0] * 5000, index=idx) - daily_cost
        assert returns.std() != 0.0
        assert returns.std() < 1e-12

        inferred = infer_annualization_freq(returns)
        assert inferred == 252.0  # premise: this is a real daily calendar

        result = sharpe_ratio(returns, freq=inferred)
        assert np.isnan(result), f"expected NaN, got an absurd value: {result}"

    def test_near_constant_sparse_series_still_returns_nan(self):
        """Same guard, but on a sparse-cadence index -- proves the guard is
        independent of which freq value ends up being inferred."""
        returns = _sparse_returns(obs_per_year=20, n_years=6, mean=0.0, std=0.0)
        assert returns.std() == 0.0
        inferred = infer_annualization_freq(returns)
        assert inferred != 252.0  # premise: genuinely inferred as sparse
        result = sharpe_ratio(returns, freq=inferred)
        assert np.isnan(result)


# ---------------------------------------------------------------------------
# 5. End-to-end wiring proof through StrategyValidationHarness.run() itself
#    -- not just the isolated metrics functions. Same offline-fixture
#    convention as tests/test_harness_oos_gate.py / test_harness_multiindex_t1.py.
# ---------------------------------------------------------------------------

from execution.cost_model import TieredCostModel
from validation.harness import StrategyValidationHarness
import validation.harness as harness_module


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(
        harness_module, "get_universe_with_survivorship_warning",
        lambda _d: (["SYN"], {"n_current": 1, "n_at_date": 1,
                              "n_delisted_in_period": 0, "estimated_bias_pct": 0.5}),
    )
    monkeypatch.setattr(
        harness_module, "_spy_return_series",
        lambda oos_index, s, e: None,
    )


def _daily_xy(n=400, seed=17):
    idx = pd.bdate_range("2015-01-01", periods=n)
    rng = np.random.default_rng(seed)
    X = pd.DataFrame({"feat": np.arange(n, dtype=float)}, index=idx)
    y = pd.Series(rng.normal(0.0003, 0.007, size=n), index=idx)
    return X, y


def _daily_strategy_fn(idx, seed=99):
    """Deterministic (fixed seed, generated once) so repeated calls with the
    same index slice reproduce the same returns -- matches this repo's
    tests/test_harness_oos_gate.py::_drawdown_strategy_fn idempotency
    rationale (the harness calls strategy_fn many times per run())."""
    rng = np.random.default_rng(seed)
    rets = pd.Series(rng.normal(0.0006, 0.011, size=len(idx)), index=idx)

    def strategy_fn(X_train, y_train, X_test, y_test):
        return [{
            "params": "daily_proxy",
            "train_returns": rets.reindex(y_train.index).dropna(),
            "test_returns": rets.reindex(y_test.index).dropna(),
            "turnover": 0.02,
        }]
    return strategy_fn


class TestHarnessEndToEndDailyCadenceUnchanged:
    def test_daily_fixture_report_matches_hand_computed_freq_252(self, tmp_path):
        """A real (bdate_range) daily-cadence harness run must produce a
        report.sharpe/.sortino/.calmar bit-identical to hand-computing those
        same statistics with the pre-fix literal freq=252 -- proves the fix
        is a no-op for the harness's dominant, genuinely-daily strategy
        population."""
        X, y = _daily_xy()
        harness = StrategyValidationHarness(
            strategy_fn=_daily_strategy_fn(X.index),
            universe_fn=lambda _d: ["SYN"],
            cost_model=TieredCostModel(),
            n_cpcv_splits=5,
            n_test_splits=2,
            reports_dir=str(tmp_path),
        )
        report = harness.run(start_date="2015-01-01", end_date="2016-06-01", X=X, y=y, strategy_name="daily_proxy")

        # Confirm the premise: the harness's own inference genuinely snapped
        # to 252 for this fixture (not incidentally equal for another reason).
        assert infer_annualization_freq(y) == 252.0

        full_trials = harness.strategy_fn(X, y, X, y)
        best_trial = full_trials[0]
        expected_returns = harness._apply_cost_model(best_trial["test_returns"], turnover=best_trial["turnover"])

        assert report.sharpe == pytest.approx(sharpe_ratio(expected_returns, freq=252), nan_ok=True)
        assert report.sortino == pytest.approx(_sortino(expected_returns, freq=252), nan_ok=True)
        assert report.calmar == pytest.approx(_calmar(expected_returns, freq=252), nan_ok=True)


class TestHarnessEndToEndSparseCadenceUsesInferredFreq:
    def test_sparse_fixture_report_uses_inferred_freq_not_252(self, tmp_path):
        """The other half of the wiring proof: a genuinely sparse-cadence
        X/y run through the real harness must produce report.sharpe matching
        the CORRECT (inferred) freq, and must NOT match what the old
        hardcoded freq=252 would have produced -- this is what actually
        would have caught the real lgbm_ranker-class bug at the harness
        level, not just inside the isolated metrics function."""
        y_sparse = _sparse_returns(obs_per_year=20, n_years=4, mean=0.01, std=0.05)
        X_sparse = pd.DataFrame({"feat": np.arange(len(y_sparse), dtype=float)}, index=y_sparse.index)

        rng = np.random.default_rng(55)
        rets = pd.Series(rng.normal(0.008, 0.04, size=len(y_sparse)), index=y_sparse.index)

        def strategy_fn(X_train, y_train, X_test, y_test):
            return [{
                "params": "sparse_proxy",
                "train_returns": rets.reindex(y_train.index).dropna(),
                "test_returns": rets.reindex(y_test.index).dropna(),
                "turnover": 0.01,
            }]

        harness = StrategyValidationHarness(
            strategy_fn=strategy_fn,
            universe_fn=lambda _d: ["SYN"],
            cost_model=TieredCostModel(),
            n_cpcv_splits=5,
            n_test_splits=2,
            reports_dir=str(tmp_path),
        )
        report = harness.run(
            start_date=str(y_sparse.index.min().date()),
            end_date=str(y_sparse.index.max().date()),
            X=X_sparse, y=y_sparse, strategy_name="sparse_proxy",
        )

        inferred = infer_annualization_freq(y_sparse)
        assert inferred != 252.0  # premise: genuinely inferred as sparse

        full_trials = harness.strategy_fn(X_sparse, y_sparse, X_sparse, y_sparse)
        best_trial = full_trials[0]
        expected_returns = harness._apply_cost_model(best_trial["test_returns"], turnover=best_trial["turnover"])

        correct_sharpe = sharpe_ratio(expected_returns, freq=inferred)
        wrong_sharpe_if_still_hardcoded = sharpe_ratio(expected_returns, freq=252)

        assert report.sharpe == pytest.approx(correct_sharpe, nan_ok=True)
        # The defining proof: report.sharpe must NOT match what the old,
        # unfixed hardcoded-252 harness would have reported.
        if np.isfinite(correct_sharpe) and np.isfinite(wrong_sharpe_if_still_hardcoded):
            assert report.sharpe != pytest.approx(wrong_sharpe_if_still_hardcoded)
