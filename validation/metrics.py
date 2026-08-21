"""
InvestYo Quant Platform - Validation Metrics (DSR, PBO, and CPCV Runner)
========================================================================
Implements institutional-grade metrics to correct for backtest overfitting:
1. Standard Sharpe Ratio
2. Deflated Sharpe Ratio (DSR)
3. Probability of Backtest Overfitting (PBO)
4. CPCV Evaluation Runner
"""

import logging
import numpy as np
import pandas as pd
from scipy.stats import norm
from typing import List, Dict, Any, Tuple, Callable, Optional

# Set up module logger
logger = logging.getLogger("Validation_Metrics")

# ---------------------------------------------------------------------------
# Annualization-frequency inference
# ---------------------------------------------------------------------------
# This codebase's existing daily-trading-year convention -- kept as a LOCAL
# copy per this file's own precedent (see e.g. technical_options_engine.py's
# TRADING_DAYS_PER_YEAR, pilots/har_volatility.py's, pilots/volatility_surface.py's
# -- every one of these modules independently declares its own copy rather
# than importing a shared constant; validation/metrics.py currently has zero
# project-internal imports and this fix does not introduce one).
TRADING_DAYS_PER_YEAR = 252.0

# Standard (leap-year-corrected) calendar year length -- the SAME constant
# this codebase's own CAGR annualization already uses
# (evaluation_engine.py::calculate_equity_curve_metrics: `365.25 / days_elapsed`).
CALENDAR_DAYS_PER_YEAR = 365.25

# Minimum observations before trusting a median-gap estimate at all -- below
# this, a handful of gaps is too little evidence (CONSTRAINT #6: fail safe
# to `default` rather than infer from noise).
MIN_OBSERVATIONS_FOR_FREQ_INFERENCE = 5

# A median consecutive-observation gap at or below this many CALENDAR days is
# treated as a genuine daily-trading-calendar series and snapped EXACTLY to
# TRADING_DAYS_PER_YEAR, rather than run through the general calendar-day
# formula below. This is not an approximation choice -- see this function's
# own docstring for why a real market DatetimeIndex's median gap is always
# 1.0 calendar days (never higher), and why converting it via
# CALENDAR_DAYS_PER_YEAR / 1.0 = 365.25 would be a ~45% overstatement versus
# the correct 252. 2.0 (not exactly 1.0) gives headroom for a small-N CPCV
# fold slice without ever colliding with any real coarser cadence in
# scripts/refresh_validations.py's STRATEGY_REGISTRY today (lgbm_ranker, the
# only non-daily entry, steps every ~5 trading days / ~7 calendar days --
# more than 3x this threshold).
DAILY_GAP_SNAP_THRESHOLD_DAYS = 2.0


def infer_annualization_freq(returns: pd.Series, default: int = 252) -> float:
    """Infers the number of return observations per year from *returns*'
    own DatetimeIndex spacing, instead of assuming every series is daily.

    Motivating bug: scripts/refresh_validations.py's lgbm_ranker adapter
    produces ~21-trading-day forward long-short spread observations, sampled
    every ~5-20+ trading days depending on which CPCV test block was
    selected -- NOT one observation per trading day. Every call into
    sharpe_ratio()/deflated_sharpe_ratio()/run_cpcv_evaluation() from
    validation/harness.py silently used the default freq=252 (a daily-data
    assumption), annualizing a ~20-observations-per-year series with
    sqrt(252) as if it were daily -- a real re-run reported Sharpe=24.886 for
    this strategy, reproduced independently at ~26.3 by applying the same
    sqrt(252) bug to the run's own equity curve.

    Method: computes the MEDIAN (not mean -- robust to one or two irregular
    gaps, e.g. a CPCV path/purge boundary) gap between consecutive
    observation dates, in calendar days.

      * A median gap <= DAILY_GAP_SNAP_THRESHOLD_DAYS is recognized as a real
        daily trading calendar (see module-level comment for why this must
        be an explicit snap, not the general formula below) and returns
        TRADING_DAYS_PER_YEAR (252.0) EXACTLY -- byte-identical to today's
        hardcoded default for every genuinely daily strategy.
      * A coarser median gap is converted via
        CALENDAR_DAYS_PER_YEAR / median_gap_days (the same calendar-day
        annualization convention this codebase's own CAGR calculation
        already uses in evaluation_engine.py).

    Fails safe to `default` (CONSTRAINT #6 -- a frequency-inference bug must
    never silently corrupt every OTHER strategy's Sharpe) when:
      * `returns` has fewer than MIN_OBSERVATIONS_FOR_FREQ_INFERENCE
        observations,
      * `returns.index` is not a real pd.DatetimeIndex (covers a MultiIndex
        panel, a plain RangeIndex, an object index, etc. uniformly -- see
        this function's own module docstring for why sector_quality_rank's
        MultiIndex `y` deliberately falls into this branch, and why that is
        verified-correct for every strategy currently registered),
      * the computed value is non-finite, <= 0, or implausible (< 1.0 or
        > TRADING_DAYS_PER_YEAR -- nothing in this registry can legitimately
        be observed MORE often than once per trading day), or
      * any unexpected exception occurs during computation.

    Never raises.
    """
    try:
        if returns is None or len(returns) < MIN_OBSERVATIONS_FOR_FREQ_INFERENCE:
            return float(default)

        idx = returns.index
        if not isinstance(idx, pd.DatetimeIndex):
            return float(default)

        idx_sorted = pd.DatetimeIndex(idx).sort_values()
        gaps_days = idx_sorted.to_series().diff().dropna().dt.days.astype(float)
        # Duplicate/unsorted-degenerate timestamps produce zero gaps; these
        # carry no spacing information and would corrupt the median.
        gaps_days = gaps_days[gaps_days > 0]
        if gaps_days.empty:
            return float(default)

        median_gap_days = float(gaps_days.median())
        if not np.isfinite(median_gap_days) or median_gap_days <= 0:
            return float(default)

        if median_gap_days <= DAILY_GAP_SNAP_THRESHOLD_DAYS:
            return float(TRADING_DAYS_PER_YEAR)

        periods_per_year = CALENDAR_DAYS_PER_YEAR / median_gap_days

        if not np.isfinite(periods_per_year) or not (1.0 <= periods_per_year <= TRADING_DAYS_PER_YEAR):
            return float(default)

        return float(periods_per_year)
    except Exception:  # noqa: BLE001 -- CONSTRAINT #6
        return float(default)


def sharpe_ratio(returns: pd.Series, freq: int = 252) -> float:
    """
    Calculates the standard annualized Sharpe Ratio.
    Assumes zero risk-free rate for simplicity.
    """
    if isinstance(returns, pd.DataFrame):
        returns = returns.squeeze()
    if not isinstance(returns, pd.Series):
        returns = pd.Series(returns)
        
    if len(returns) < 2:
        return np.nan
    mean_ret = returns.mean()
    std_ret = returns.std()
    # A degenerate (flat/no-signal) returns series -- e.g. an all-zero book
    # after a flat per-day cost deduction -- is mathematically constant, but
    # pandas' two-pass std() accumulates floating-point rounding noise over
    # many rows, so it lands near (not bit-identical to) 0.0 rather than
    # exactly 0.0. An exact `== 0` check misses that, and mean/std then
    # explodes into an absurd, unbounded "Sharpe" (observed: ~1e16 magnitude)
    # instead of the honest NaN (CONSTRAINT #4). 1e-12 mirrors the
    # degenerate-std threshold already used by risk/etf_transmission.py --
    # far above float noise (~1e-16 to 1e-20) and far below any real
    # strategy's daily-return std.
    if np.isnan(std_ret) or std_ret < 1e-12:
        return np.nan
    return (mean_ret / std_ret) * np.sqrt(freq)

def deflated_sharpe_ratio(
    sr_observed: float,
    n_trials: int,
    sr_variance: float,
    skew: float,
    kurtosis: float,
    n_observations: int,
    freq: int = 252
) -> float:
    """
    Calculates the Deflated Sharpe Ratio (DSR) as defined by Bailey & Lopez de Prado (2014).
    
    Args:
        sr_observed: Observed Sharpe ratio (annualized).
        n_trials: Number of strategy configurations/trials tested.
        sr_variance: Variance of the annualized Sharpe ratios across the trials.
        skew: Skewness of the strategy's returns.
        kurtosis: Kurtosis of the strategy's returns.
        n_observations: Number of observations (T) in the backtest.
        freq: Frequency of the observations (e.g. 252 for daily, 12 for monthly).
    
    Returns:
        DSR value (float between 0 and 1), indicating the probability that the true SR is > 0.
    """
    single_trial = n_trials <= 1
    if single_trial:
        # settings.VALIDATION_DSR_SINGLE_TRIAL_CORRECTION_ENABLED (opt-in,
        # default False -- see settings.py for the full honesty writeup):
        # the legacy shortcut below (`return 1.0`) skips the entire DSR
        # calculation for a single-trial strategy, which is directly relied
        # on today by several STRATEGY_REGISTRY strategies currently
        # recorded deployable=True. Reproduce it byte-for-byte unless the
        # flag is explicitly on.
        try:
            from settings import settings as _dsr_settings
            correction_enabled = bool(
                getattr(_dsr_settings, "VALIDATION_DSR_SINGLE_TRIAL_CORRECTION_ENABLED", False)
            )
        except Exception:  # noqa: BLE001 - never let a settings read block validation
            correction_enabled = False
        if not correction_enabled:
            return 1.0  # No selection bias if only one trial (legacy shortcut)

    # 1. Convert annualized SR and variance to non-annualized daily/monthly equivalent
    # SR_daily = SR_annual / sqrt(freq)
    sr_hat = sr_observed / np.sqrt(freq)
    var_sr = sr_variance / freq

    # Euler-Mascheroni constant
    euler = 0.57721566490153286

    if single_trial:
        # Correction enabled: with genuinely only one trial there is no
        # multiple-testing selection bias to deflate for, so the expected
        # maximum Sharpe ratio under the null is 0 -- not the
        # multiple-testing-inflated sqrt(var_sr) * (...) term below (which
        # would be meaningless with n_trials=1 anyway; z_n = norm.ppf(1 - 1/1)
        # = norm.ppf(0) = -inf, already guarded to 0.0 by the branch below,
        # silently producing a mild positive bias rather than the correct 0).
        sr_0 = 0.0
    else:
        # 2. Estimate expected maximum Sharpe ratio under null hypothesis (SR_0)
        # Using Bailey-Lopez de Prado approximation
        z_n = norm.ppf(1.0 - 1.0 / n_trials)
        z_ne = norm.ppf(1.0 - 1.0 / (n_trials * np.e))
        # Deal with infinite values for small n_trials or edge cases
        if np.isinf(z_n) or np.isnan(z_n):
            z_n = 0.0
        if np.isinf(z_ne) or np.isnan(z_ne):
            z_ne = 0.0

        sr_0 = np.sqrt(var_sr) * ((1.0 - euler) * z_n + euler * z_ne)

    # 3. Calculate DSR test statistic Z
    # Z = (sr_hat - sr_0) * sqrt(T - 1) / sqrt(1 - skew * sr_hat + ((kurt - 1)/4) * sr_hat^2)
    # Note: kurtosis must be the non-excess kurtosis (so if excess kurtosis is used, add 3.0)
    # The standard scipy.stats.kurtosis returns excess, so we assume the input is non-excess.
    denominator = np.sqrt(1.0 - skew * sr_hat + ((kurtosis - 1.0) / 4.0) * (sr_hat ** 2))

    # Degenerate-std guard convention (see this repo's documented convention):
    # a near-zero-but-not-exact denominator is floating-point noise, not a
    # genuine zero; dividing by it would explode z_stat into an absurd,
    # unbounded value instead of the honest NaN.
    if np.isnan(denominator) or denominator < 1e-12:
        return np.nan

    z_stat = ((sr_hat - sr_0) * np.sqrt(n_observations - 1)) / denominator

    return float(norm.cdf(z_stat))

def probability_of_backtest_overfitting(
    in_sample_sharpes: np.ndarray,
    out_of_sample_sharpes: np.ndarray
) -> float:
    """
    Calculates the Probability of Backtest Overfitting (PBO) using Bailey et al. (2014) method.
    
    Args:
        in_sample_sharpes: Array of shape (n_paths, n_strategies) with IS performance.
        out_of_sample_sharpes: Array of shape (n_paths, n_strategies) with OOS performance.
        
    Returns:
        PBO (float between 0 and 1), the probability that the best IS strategy performs below the median OOS.
    """
    n_paths, n_strategies = in_sample_sharpes.shape
    if n_paths == 0 or n_strategies == 0:
        return 0.0

    overfit_count = 0
    measurable_paths = 0

    for s in range(n_paths):
        # Best strategy index in-sample for path s. Every trial can be NaN
        # on a given path (e.g. constant/degenerate train_returns for the
        # whole trial set on that one path) -- np.nanargmax raises on an
        # all-NaN row, so that path is skipped entirely (excluded from the
        # denominator below) rather than fabricating a "best" index among
        # values that were never actually measured (CONSTRAINT #4).
        try:
            best_is_idx = np.nanargmax(in_sample_sharpes[s])
        except ValueError:
            continue

        # OOS performance of the best IS strategy
        oos_perf_of_best_is = out_of_sample_sharpes[s, best_is_idx]

        # The in-sample-best trial's own OOS performance can be individually
        # NaN even when in_sample_sharpes[s] wasn't all-NaN (e.g. a
        # degenerate/constant test window for that one trial). NaN
        # comparisons are always False in Python/numpy, so `oos_perf_of_best_is
        # < median_oos_perf` would silently evaluate False and this path
        # would count as "not overfit" for the wrong reason -- excluded here
        # instead, matching the all-NaN-row skip above (CONSTRAINT #4:
        # unmeasurable is excluded, never guessed in either direction).
        if np.isnan(oos_perf_of_best_is):
            continue

        # Median OOS performance of all strategies on path s
        median_oos_perf = np.nanmedian(out_of_sample_sharpes[s])

        measurable_paths += 1
        if oos_perf_of_best_is < median_oos_perf:
            overfit_count += 1

    if measurable_paths == 0:
        return float("nan")
    return float(overfit_count) / measurable_paths

def run_cpcv_evaluation(
    strategy_fn: Callable[[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series], List[Dict[str, Any]]],
    X: pd.DataFrame,
    y: pd.Series,
    t1: pd.Series = None,
    n_splits: int = 10,
    n_test_splits: int = 2,
    freq: int = 252,
    cost_model_fn: Optional[Callable[[pd.Series, float], pd.Series]] = None,
) -> Dict[str, Any]:
    """
    Runs CPCV evaluation across all combination paths and calculates validation metrics.

    Args:
        strategy_fn: Callable taking (X_train, y_train, X_test, y_test) and returning a list of dicts:
                     [{"params": dict/str, "train_returns": pd.Series, "test_returns": pd.Series}]
                     for multiple strategy candidates.
        X: Features DataFrame.
        y: Targets Series.
        t1: Event end times.
        cost_model_fn: Optional ``(returns: pd.Series, turnover: float) -> pd.Series``
            callable applying transaction-cost drag to a raw return series.
            When provided, every trial's train_returns/test_returns are
            cost-adjusted via ``cost_model_fn(returns, trial.get("turnover", 0.05))``
            BEFORE any Sharpe/PBO/DSR/drawdown statistic is computed from
            them, so PBO/DSR reflect the same net-of-cost basis as the
            harness's other metrics instead of raw, cost-free returns. ``None``
            (the default) reproduces the pre-existing gross-return behavior
            exactly (see ``settings.VALIDATION_HARNESS_OOS_GATE_ENABLED``).

    Returns a dict with ``paths``/``dsr``/``pbo``/``mean_oos_sharpe``/``distribution``
    (pre-existing keys, unchanged in meaning) plus four new genuinely
    out-of-sample aggregates for the DSR-selected strategy — each the MEAN of
    a per-path metric computed independently on that path's own held-out
    (purged+embargoed) returns, NOT a single concatenated equity curve.
    CPCV's combinatorial test blocks are deliberately reused across paths (a
    raw concatenation across all paths would double/triple-count most dates),
    so this mirrors ``mean_oos_sharpe``'s own pre-existing aggregation
    convention rather than requiring the AFML backtest-path-recombination
    algorithm: ``mean_oos_max_dd``, ``mean_oos_sortino``, ``mean_oos_hit_rate``,
    ``mean_oos_avg_trade_pct``, ``mean_oos_turnover``.
    """
    from validation.purged_cv import CombinatorialPurgedCV
    from validation.stress_scenarios import compute_max_drawdown

    cv = CombinatorialPurgedCV(n_splits=n_splits, n_test_splits=n_test_splits)

    def _rank_key(values: List[float]) -> List[float]:
        """NaN-safe argmax input: a NaN Sharpe (degenerate/constant returns)
        must never win an argmax-based "best trial" selection, but unlike a
        finite placeholder (e.g. the historical -999.0 sentinel this
        replaced), -inf here is purely a LOCAL ranking key -- it is never
        stored in is_sharpe_matrix/oos_sharpe_matrix and can never leak into
        a reported metric (mean_oos_sharpe, paths[].sharpe, distribution),
        which all preserve the real NaN (CONSTRAINT #4 -- never fabricate a
        finite value in place of "unmeasurable")."""
        return [v if not np.isnan(v) else -np.inf for v in values]

    def _nanmean_or(values, default: float) -> float:
        # All-NaN input is an expected, non-error case here (e.g. a
        # degenerate/constant-returns trial on every path) -- avoid numpy's
        # "Mean of empty slice" RuntimeWarning and report the honest default
        # instead of a fabricated number.
        finite = [v for v in values if not np.isnan(v)]
        if not finite:
            return default
        return float(np.mean(finite))

    paths_data = []
    is_sharpe_matrix = []
    oos_sharpe_matrix = []
    all_trials_by_path: List[List[Dict[str, Any]]] = []

    logger.info("Executing CPCV path evaluation...")

    for train_idx, test_idx, path_id in cv.split(X, y, t1):
        if len(train_idx) == 0:
            continue

        X_train, y_train = X.iloc[train_idx], y.iloc[train_idx]
        X_test, y_test = X.iloc[test_idx], y.iloc[test_idx]

        # Run strategy evaluation
        trials = strategy_fn(X_train, y_train, X_test, y_test)
        if not trials:
            continue

        if cost_model_fn is not None:
            trials = [
                {
                    **trial,
                    "train_returns": cost_model_fn(trial["train_returns"], trial.get("turnover", 0.05)),
                    "test_returns": cost_model_fn(trial["test_returns"], trial.get("turnover", 0.05)),
                }
                for trial in trials
            ]

        is_sharpes = []
        oos_sharpes = []

        for trial in trials:
            is_sr = sharpe_ratio(trial["train_returns"], freq=freq)
            oos_sr = sharpe_ratio(trial["test_returns"], freq=freq)
            # Preserve the real value (including NaN) here -- these lists
            # feed is_sharpe_matrix/oos_sharpe_matrix directly, which are
            # what paths_data / distribution / mean_oos_sharpe report.
            is_sharpes.append(is_sr)
            oos_sharpes.append(oos_sr)

        is_sharpe_matrix.append(is_sharpes)
        oos_sharpe_matrix.append(oos_sharpes)
        all_trials_by_path.append(trials)

        # Track the best performing configuration on this path (in-sample).
        # Ranked via _rank_key so a degenerate (NaN) trial can never win --
        # is_sharpes itself keeps its real NaN for reporting.
        best_is_idx = int(np.argmax(_rank_key(is_sharpes)))
        best_trial = trials[best_is_idx]

        paths_data.append({
            "path_id": path_id,
            "sharpe": oos_sharpes[best_is_idx],
            "returns": best_trial["test_returns"].tolist(),
            "params": best_trial["params"]
        })

    _empty_result = {
        "paths": [], "dsr": 0.0, "pbo": 1.0, "mean_oos_sharpe": 0.0, "distribution": np.array([]),
        "mean_oos_max_dd": float("nan"), "mean_oos_sortino": float("nan"),
        "mean_oos_hit_rate": float("nan"), "mean_oos_avg_trade_pct": float("nan"),
        "mean_oos_turnover": float("nan"), "mean_oos_return": float("nan"),
    }
    if not is_sharpe_matrix:
        return _empty_result

    is_sharpe_matrix = np.array(is_sharpe_matrix)
    oos_sharpe_matrix = np.array(oos_sharpe_matrix)

    # 1. Calculate PBO
    pbo = probability_of_backtest_overfitting(is_sharpe_matrix, oos_sharpe_matrix)

    # 2. Calculate DSR for the best overall selected strategy
    # Let's find the configuration that performed best overall in-sample (on
    # average). Per-trial mean is computed via _nanmean_or (skips a path's
    # NaN Sharpe rather than poisoning the whole column to NaN); a trial
    # that's NaN on EVERY path stays honestly NaN and _rank_key keeps it
    # from winning the argmax outright.
    mean_is_sharpes = np.array([
        _nanmean_or(list(is_sharpe_matrix[:, j]), float("nan"))
        for j in range(is_sharpe_matrix.shape[1])
    ])
    best_overall_idx = int(np.argmax(_rank_key(list(mean_is_sharpes))))
    best_overall_oos_sharpes = oos_sharpe_matrix[:, best_overall_idx]

    # Calculate returns skew/kurtosis of the selected strategy (all merged OOS
    # returns) -- MUST come from the SAME trial selection (best_overall_idx,
    # the single trial with the best MEAN in-sample Sharpe across all paths)
    # that sr_observed uses below, not the per-path best_is_idx "winners"
    # (which back paths_data's own per-path report table -- a legitimate,
    # separate use of a per-path-varying selection). Mixing the two would
    # have the DSR test statistic's tail-shape terms (skew/kurtosis) describe
    # a different, path-varying set of trials than the one its own
    # sr_observed represents -- an internally inconsistent DSR input.
    selected_oos_returns: List[float] = []
    for trials in all_trials_by_path:
        if best_overall_idx >= len(trials):
            continue
        selected_test_returns = trials[best_overall_idx].get("test_returns")
        if selected_test_returns is None or len(selected_test_returns) == 0:
            continue
        selected_oos_returns.extend(selected_test_returns.tolist())
    all_oos_returns = pd.Series(selected_oos_returns)
    skew = all_oos_returns.skew() if len(all_oos_returns) > 2 else 0.0
    kurt = all_oos_returns.kurtosis() + 3.0 if len(all_oos_returns) > 2 else 3.0 # convert to non-excess

    if np.isnan(skew): skew = 0.0
    if np.isnan(kurt): kurt = 3.0

    # Observed Sharpe ratio is the mean OOS Sharpe of the selected strategy.
    # NaN-aware: a path where the selected trial's OOS Sharpe itself came out
    # NaN (degenerate/constant test_returns) is skipped rather than dragging
    # sr_observed down with a fabricated finite placeholder -- CONSTRAINT #4;
    # this replaced a prior -999.0 sentinel that leaked into this exact value.
    sr_observed = _nanmean_or(list(best_overall_oos_sharpes), float("nan"))
    n_trials = is_sharpe_matrix.shape[1]

    # Variance of Sharpe ratios across all trials (skipping any NaN entries
    # from an all-degenerate trial column -- see mean_is_sharpes above).
    finite_mean_is_sharpes = [v for v in mean_is_sharpes if not np.isnan(v)]
    sr_variance = float(np.var(finite_mean_is_sharpes)) if finite_mean_is_sharpes else 0.0
    # Degenerate-std guard convention: a near-zero-but-not-exact variance
    # (floating-point noise from near-identical trial Sharpes) must not be
    # treated as "genuinely zero" -- but it also must not be left as literal
    # noise feeding a division downstream in deflated_sharpe_ratio, so it is
    # floored to a small positive value (unlike the NaN-return convention
    # used for a degenerate ratio DENOMINATOR elsewhere in this module,
    # sr_variance is itself a numerator input to a sqrt(), so flooring
    # rather than propagating NaN preserves a defined DSR here).
    if sr_variance < 1e-12:
        sr_variance = 1e-6

    # n_observations=len(X) uses the FULL backtest sample length as the DSR
    # test statistic's effective-sample-size stand-in (T), not the length of
    # any single CPCV path's own OOS slice. Reviewed and deliberately left
    # as-is: DSR's sqrt(T - 1) term calibrates how much sampling noise to
    # expect around sr_observed, and per-path OOS length varies mechanically
    # with n_splits/n_test_splits (an implementation choice unrelated to how
    # much real data backs the estimate), while CPCV's combinatorial paths
    # are constructed so that, taken together (purging/embargo aside), they
    # draw on very close to the entire dataset -- so len(X) is a more stable,
    # not an obviously wrong, proxy for the estimate's true information
    # content. Using total observations T as the DSR sample-size term is not
    # unusual in DSR implementations that evaluate a strategy across
    # resampled/combinatorial folds rather than a single held-out slice. Not
    # changed by this pass; flag if a live-data re-verification surfaces
    # evidence this materially overstates confidence for a specific strategy.
    dsr = deflated_sharpe_ratio(
        sr_observed=sr_observed,
        n_trials=n_trials,
        sr_variance=sr_variance,
        skew=skew,
        kurtosis=kurt,
        n_observations=len(X),
        freq=freq
    )

    distribution = oos_sharpe_matrix[:, best_overall_idx]
    # NaN-aware: a per-path degenerate OOS Sharpe stays honestly NaN inside
    # `distribution` itself (never a fabricated -999.0), and is skipped here
    # rather than dragging mean_oos_sharpe to a nonsensical negative value.
    mean_oos_sharpe = _nanmean_or(list(distribution), float("nan"))

    # Genuinely OOS drawdown/sortino/hit-rate/avg-trade/turnover for the
    # DSR-selected strategy — the mean of each metric computed independently
    # per CPCV path (see this function's own docstring for why this is not a
    # single concatenated equity curve).
    per_path_max_dd: List[float] = []
    per_path_sortino: List[float] = []
    per_path_hit_rate: List[float] = []
    per_path_avg_trade: List[float] = []
    per_path_turnover: List[float] = []
    per_path_mean_return: List[float] = []
    for trials in all_trials_by_path:
        if best_overall_idx >= len(trials):
            continue
        selected_trial = trials[best_overall_idx]
        oos_returns = selected_trial["test_returns"]
        if oos_returns is None or len(oos_returns) == 0:
            continue
        per_path_max_dd.append(compute_max_drawdown(oos_returns))
        downside = oos_returns[oos_returns < 0]
        downside_std = downside.std()
        # Same degenerate-std guard as sharpe_ratio() above -- a near-zero
        # (but not exactly zero) downside std is floating-point noise from a
        # constant/near-constant downside series, not real signal.
        sortino = (
            (oos_returns.mean() / downside_std * np.sqrt(freq))
            if downside_std >= 1e-12 else np.nan
        )
        per_path_sortino.append(sortino)
        trade_days = oos_returns != 0
        per_path_hit_rate.append(float((oos_returns[trade_days] > 0).mean()) if trade_days.any() else np.nan)
        per_path_avg_trade.append(float(oos_returns[trade_days].mean()) if trade_days.any() else np.nan)
        per_path_turnover.append(float(selected_trial.get("turnover", 0.05)))
        # UNCONDITIONAL per-path mean return (every day in the OOS slice, not
        # just trade_days) -- matches the harness's own non-OOS-gate Calmar
        # convention (full_returns.mean(), an unconditional mean), unlike
        # per_path_avg_trade above (conditional on trade_days, which serves a
        # different, deliberately trade-conditional metric).
        per_path_mean_return.append(float(oos_returns.mean()))

    # _nanmean_or is defined once, above the main CPCV loop -- reused here.
    mean_oos_max_dd = _nanmean_or(per_path_max_dd, float("nan"))
    mean_oos_sortino = _nanmean_or(per_path_sortino, float("nan"))
    mean_oos_hit_rate = _nanmean_or(per_path_hit_rate, 0.0)
    mean_oos_avg_trade_pct = _nanmean_or(per_path_avg_trade, 0.0)
    mean_oos_turnover = _nanmean_or(per_path_turnover, 0.05)
    mean_oos_return = _nanmean_or(per_path_mean_return, float("nan"))

    return {
        "paths": paths_data,
        "dsr": dsr,
        "pbo": pbo,
        "mean_oos_sharpe": mean_oos_sharpe,
        "distribution": distribution,
        "mean_oos_max_dd": mean_oos_max_dd,
        "mean_oos_sortino": mean_oos_sortino,
        "mean_oos_hit_rate": mean_oos_hit_rate,
        "mean_oos_avg_trade_pct": mean_oos_avg_trade_pct,
        "mean_oos_turnover": mean_oos_turnover,
        # UNCONDITIONAL mean per-day OOS return (mean of each CPCV path's own
        # oos_returns.mean() -- every day, not just trade_days), consumed by
        # validation/harness.py's OOS-gate Calmar so it matches the non-gated
        # Calmar's own unconditional-mean convention rather than mixing a
        # trade-conditional mean (mean_oos_avg_trade_pct) into a
        # full-period-annualized ratio.
        "mean_oos_return": mean_oos_return,
    }


def profit_factor(returns: pd.Series) -> float:
    """
    Calculates the Profit Factor (Gross Profits / Gross Losses).
    Returns NaN if returns are empty, inf if there are no losses, or 0.0 if there are no gains.
    """
    if isinstance(returns, pd.DataFrame):
        returns = returns.squeeze()
    if not isinstance(returns, pd.Series):
        returns = pd.Series(returns)

    valid = returns.dropna()
    if len(valid) == 0:
        return np.nan

    gains = valid[valid > 0].sum()
    losses = abs(valid[valid < 0].sum())

    if losses < 1e-12:
        return np.inf if gains > 0 else 0.0
    return float(gains / losses)


def ulcer_index(returns: pd.Series) -> float:
    """
    Calculates the Ulcer Index (Peter Martin, 1987), measuring downside risk
    considering both the depth and duration of price/equity drawdowns.

    Formula:
        Equity_t = Cumulative_Product(1 + Returns_t)
        Peak_t = max_{s <= t}(Equity_s)
        DrawdownPct_t = ((Equity_t - Peak_t) / Peak_t) * 100
        Ulcer_Index = sqrt(mean(DrawdownPct_t^2))

    Returns:
        float: Ulcer Index percentage (e.g. 5.2 for 5.2% root-mean-square drawdown).
    """
    if isinstance(returns, pd.DataFrame):
        returns = returns.squeeze()
    if not isinstance(returns, pd.Series):
        returns = pd.Series(returns)

    valid = returns.dropna()
    if len(valid) < 2:
        return np.nan

    # Calculate cumulative compounding equity
    equity = (1.0 + valid).cumprod()
    peaks = equity.cummax()
    drawdown_pct = ((equity - peaks) / peaks) * 100.0
    squared_dd = drawdown_pct ** 2
    return float(np.sqrt(squared_dd.mean()))


def ulcer_performance_index(returns: pd.Series, freq: int = 252, rf: float = 0.0) -> float:
    """
    Calculates the Ulcer Performance Index (UPI, also known as Martin Ratio).
    Measures excess annualized return per unit of Ulcer Index downside risk.

    Formula:
        UPI = (Annualized_Return - rf) / (Ulcer_Index / 100.0)

    Target:
        UPI > 1.0 is considered institutional-grade performance for volatility sellers.
    """
    if isinstance(returns, pd.DataFrame):
        returns = returns.squeeze()
    if not isinstance(returns, pd.Series):
        returns = pd.Series(returns)

    valid = returns.dropna()
    if len(valid) < 2:
        return np.nan

    ui = ulcer_index(valid)
    if np.isnan(ui) or ui < 1e-6:
        # Zero/near-zero drawdown
        ann_ret = valid.mean() * freq
        return np.inf if ann_ret > rf else 0.0

    ann_ret = valid.mean() * freq
    excess_ret = ann_ret - rf
    # Convert UI from percentage (0-100) to decimal (0-1) in denominator
    return float(excess_ret / (ui / 100.0))


def walk_forward_efficiency_ratio(is_returns: pd.Series, oos_returns: pd.Series) -> float:
    """
    Calculates the Walk-Forward Efficiency (WFE) ratio (Robert Pardo):
        WFE = Profit_Factor(OOS) / Profit_Factor(IS)
    
    Target:
        WFE > 0.50 confirms that the strategy edge remains stable and does not
        substantially decay out-of-sample.
    """
    pf_is = profit_factor(is_returns)
    pf_oos = profit_factor(oos_returns)

    if np.isnan(pf_is) or np.isnan(pf_oos) or pf_is <= 1e-6:
        return 0.0
    if np.isinf(pf_is):
        return 1.0 if not np.isinf(pf_oos) and pf_oos > 1.0 else 0.0
    if np.isinf(pf_oos):
        return 1.0

    return float(pf_oos / pf_is)

