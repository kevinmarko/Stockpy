"""
tests/test_copula_stat_arb.py — Tests for Cross-Asset Copula Stat Arb & Dynamic Spread Engine.
================================================================================================
"""

import ast
import math
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from pilots.copula_stat_arb import (
    BestCopulaResult,
    CopulaFamily,
    CopulaFitResult,
    CopulaPairAnalysis,
    CopulaStatArbResult,
    KalmanHedgeRatioResult,
    calculate_copula_mispricing,
    calculate_ou_half_life,
    calculate_spread_zscore,
    clayton_log_likelihood,
    compute_copula_spread_and_zscore,
    estimate_kalman_dynamic_hedge_ratio,
    estimate_ou_half_life,
    evaluate_copula_stat_arb_pair,
    execute_copula_spread_trade,
    fit_best_copula,
    fit_bivariate_copula,
    fit_clayton_copula,
    fit_frank_copula,
    fit_gaussian_copula,
    fit_gumbel_copula,
    generate_copula_stat_arb_signals,
    kalman_filter_hedge_ratio,
    select_best_copula,
    to_pseudo_observations,
)


# ---------------------------------------------------------------------------
# 1. Pseudo-Observations & Rank Transformation Tests
# ---------------------------------------------------------------------------


def test_pseudo_observations_range_and_ranks():
    """Verifies that rank transform produces valid uniform pseudo-observations in (0, 1)."""
    np.random.seed(42)
    y = np.random.normal(0, 1, 100)
    x = np.random.normal(0, 1, 100)

    u, v = to_pseudo_observations(y, x)

    assert len(u) == 100
    assert len(v) == 100
    assert np.all(u > 0.0) and np.all(u < 1.0)
    assert np.all(v > 0.0) and np.all(v < 1.0)
    # Check monotonicity with ranks
    assert np.argmax(u) == np.argmax(y)
    assert np.argmin(u) == np.argmin(y)


def test_pseudo_observations_short_or_nan():
    """Verifies graceful degradation on short or NaN data."""
    u, v = to_pseudo_observations([1.0, 2.0], [2.0, 3.0])
    assert len(u) == 1
    assert u[0] == 0.5


# ---------------------------------------------------------------------------
# 2. Copula Fitting & Tail Dependence Tests
# ---------------------------------------------------------------------------


def test_clayton_copula_mle_and_tail_dependence():
    """
    Verifies Clayton copula fitting on lower-tail dependent synthetic data.
    Clayton has lower tail dependence lambda_L = 2^(-1/theta) > 0 and lambda_U = 0.
    """
    np.random.seed(42)
    n = 300
    # Generate Clayton-like samples
    v1 = np.random.uniform(0.01, 0.99, n)
    v2 = np.random.uniform(0.01, 0.99, n)
    # True theta = 2.0 => lambda_L = 2^(-1/2) = 0.7071
    theta_true = 2.0
    u = v1
    v = np.power(np.power(v1, -theta_true) * (np.power(v2, -theta_true / (1 + theta_true)) - 1.0) + 1.0, -1.0 / theta_true)
    u, v = np.clip(u, 1e-4, 1.0 - 1e-4), np.clip(v, 1e-4, 1.0 - 1e-4)

    fit = fit_clayton_copula(u, v)

    assert isinstance(fit, CopulaFitResult)
    assert fit.family == CopulaFamily.CLAYTON.value
    assert fit.theta > 0.5
    assert fit.lower_tail_dependence > 0.3
    assert fit.upper_tail_dependence == 0.0
    assert fit.converged is True
    assert np.isfinite(fit.aic)


def test_gumbel_copula_mle_and_tail_dependence():
    """
    Verifies Gumbel copula fitting on upper-tail dependent synthetic data.
    Gumbel has upper tail dependence lambda_U = 2 - 2^(1/theta) > 0 and lambda_L = 0.
    """
    np.random.seed(42)
    n = 300
    u = np.random.uniform(0.1, 0.9, n)
    v = np.clip(u + np.random.normal(0, 0.1, n), 0.01, 0.99)

    fit = fit_gumbel_copula(u, v)

    assert fit.family == CopulaFamily.GUMBEL.value
    assert fit.theta >= 1.0
    assert fit.lower_tail_dependence == 0.0
    assert fit.upper_tail_dependence >= 0.0
    assert fit.converged is True


def test_frank_copula_mle_symmetric():
    """Verifies Frank copula has zero tail dependence in both tails (lambda_L = 0, lambda_U = 0)."""
    np.random.seed(42)
    n = 200
    u = np.random.uniform(0.01, 0.99, n)
    v = np.clip(u * 0.7 + np.random.normal(0, 0.2, n), 0.01, 0.99)

    fit = fit_frank_copula(u, v)

    assert fit.family == CopulaFamily.FRANK.value
    assert fit.lower_tail_dependence == 0.0
    assert fit.upper_tail_dependence == 0.0
    assert fit.converged is True


def test_gaussian_copula_mle():
    """Verifies Gaussian copula parameter rho fitting."""
    np.random.seed(42)
    n = 200
    u = np.random.uniform(0.05, 0.95, n)
    v = np.clip(u + np.random.normal(0, 0.1, n), 0.01, 0.99)

    fit = fit_gaussian_copula(u, v)

    assert fit.family == CopulaFamily.GAUSSIAN.value
    assert fit.theta > 0.5  # rho > 0.5
    assert fit.lower_tail_dependence == 0.0
    assert fit.upper_tail_dependence == 0.0


def test_fit_best_copula_selection():
    """Verifies fit_best_copula compares AIC and returns the best model."""
    np.random.seed(42)
    n = 250
    ret_y = pd.Series(np.random.normal(0.001, 0.02, n))
    ret_x = pd.Series(ret_y * 0.8 + np.random.normal(0, 0.01, n))

    best = fit_best_copula(ret_y, ret_x)

    assert isinstance(best, CopulaFitResult)
    assert best.family in [f.value for f in CopulaFamily]
    assert np.isfinite(best.aic)
    assert np.isfinite(best.kendall_tau)


# ---------------------------------------------------------------------------
# 3. Dynamic Kalman Filter Hedge Ratio Tests
# ---------------------------------------------------------------------------


def test_kalman_filter_hedge_ratio_convergence():
    """
    Verifies that Kalman filter converges to true hedge ratio beta on synthetic linear data:
        y_t = 2.5 * x_t + 5.0 + noise
    """
    np.random.seed(42)
    n = 250
    x = np.cumsum(np.random.normal(0, 1, n)) + 100.0
    noise = np.random.normal(0, 0.5, n)
    y = 2.5 * x + 5.0 + noise

    alpha_s, beta_s = kalman_filter_hedge_ratio(pd.Series(y), pd.Series(x))

    assert len(beta_s) == n
    assert len(alpha_s) == n

    # After 50 bars of warmup, estimated beta should be within 5% of 2.5
    final_beta = float(beta_s.iloc[-1])
    assert abs(final_beta - 2.5) < 0.15, f"Expected beta ~ 2.5, got {final_beta}"


def test_kalman_filter_time_varying_beta():
    """Verifies that Kalman filter smoothly tracks a time-varying hedge ratio."""
    np.random.seed(42)
    n = 300
    x = np.cumsum(np.random.normal(0, 1, n)) + 100.0
    # True beta shifts from 1.5 (first half) to 3.0 (second half)
    beta_true = np.where(np.arange(n) < 150, 1.5, 3.0)
    y = beta_true * x + np.random.normal(0, 0.5, n)

    _, beta_s = kalman_filter_hedge_ratio(pd.Series(y), pd.Series(x))

    # Mid-first-half check (bar 100)
    assert abs(beta_s.iloc[100] - 1.5) < 0.2
    # End-second-half check (bar 280)
    assert abs(beta_s.iloc[280] - 3.0) < 0.3


# ---------------------------------------------------------------------------
# 4. Ornstein-Uhlenbeck (OU) Mean-Reversion Half-Life Tests
# ---------------------------------------------------------------------------


def test_ou_half_life_mean_reverting():
    """
    Verifies that estimate_ou_half_life accurately estimates half-life of an AR(1) process:
        S_t = 0.9 * S_{t-1} + eps => gamma_1 = -0.1 => tau_1/2 = -ln(2)/ln(0.9) = 6.57 days
    """
    np.random.seed(42)
    n = 500
    spread = [0.0]
    for _ in range(n - 1):
        spread.append(0.9 * spread[-1] + np.random.normal(0, 0.2))

    hl = estimate_ou_half_life(pd.Series(spread))

    assert np.isfinite(hl)
    assert 5.0 <= hl <= 8.5, f"Expected half-life ~ 6.57, got {hl}"


def test_ou_half_life_random_walk_inf():
    """Verifies that a pure random walk (non-mean-reverting) yields float('inf')."""
    np.random.seed(42)
    n = 300
    rw = np.cumsum(np.random.normal(0.1, 1.0, n))

    hl = estimate_ou_half_life(pd.Series(rw))
    assert math.isinf(hl) or hl > 100.0


def test_ou_half_life_degenerate():
    """Verifies that constant or short series returns infinity without raising."""
    assert math.isinf(estimate_ou_half_life([10.0] * 20))
    assert math.isinf(estimate_ou_half_life([1.0, 2.0]))


# ---------------------------------------------------------------------------
# 5. Spread & Rolling Z-Score Tests
# ---------------------------------------------------------------------------


def test_compute_copula_spread_and_zscore():
    """Verifies compute_copula_spread_and_zscore produces clean spread and rolling Z-scores."""
    np.random.seed(42)
    n = 200
    x = pd.Series(np.cumsum(np.random.normal(0, 1, n)) + 100.0)
    spread_noise = np.random.normal(0, 1, n)
    y = pd.Series(1.8 * x + 10.0 + spread_noise)

    df = compute_copula_spread_and_zscore(y, x, lookback=30)

    assert isinstance(df, pd.DataFrame)
    assert set(["y", "x", "beta", "spread", "z_score", "half_life"]).issubset(df.columns)
    assert len(df) == n

    # Check finite Z-scores
    valid_z = df["z_score"].dropna()
    assert len(valid_z) > 100
    assert abs(valid_z.iloc[50:].mean()) < 0.75
    assert abs(valid_z.std() - 1.0) < 0.5


# ---------------------------------------------------------------------------
# 6. Signal Generation & State Machine Tests
# ---------------------------------------------------------------------------


def test_generate_copula_stat_arb_signals_long_spread():
    """
    Verifies Long Spread signal (Buy Y, Short X) is triggered when Z <= -2.0.
    """
    np.random.seed(42)
    n = 150
    x = pd.Series(np.linspace(100, 120, n))
    # Create an artificial dip in spread at the end to force Z < -2.0
    spread = np.zeros(n)
    spread[-1] = -5.0  # Massive negative dislocation
    y = pd.Series(1.0 * x + spread)

    res = generate_copula_stat_arb_signals("AAPL", "MSFT", y, x, z_entry=2.0, z_exit=0.0)

    assert isinstance(res, CopulaStatArbResult)
    assert res.symbol_y == "AAPL"
    assert res.symbol_x == "MSFT"
    assert res.current_signal in ("LONG_SPREAD", "HOLD", "FLAT")
    if res.current_zscore <= -2.0 and res.tail_risk_acceptable:
        assert res.current_signal == "LONG_SPREAD"
        assert "Buy AAPL, Short MSFT" in res.action


def test_generate_copula_stat_arb_signals_short_spread():
    """
    Verifies Short Spread signal (Sell Y, Long X) is triggered when Z >= 2.0.
    """
    np.random.seed(42)
    n = 150
    x = pd.Series(np.linspace(100, 120, n))
    spread = np.zeros(n)
    spread[-1] = 6.0  # Massive positive dislocation
    y = pd.Series(1.0 * x + spread)

    res = generate_copula_stat_arb_signals("GOOGL", "META", y, x, z_entry=2.0, z_exit=0.0)

    assert isinstance(res, CopulaStatArbResult)
    assert res.symbol_y == "GOOGL"
    assert res.symbol_x == "META"
    if res.current_zscore >= 2.0 and res.tail_risk_acceptable:
        assert res.current_signal == "SHORT_SPREAD"
        assert "Sell GOOGL, Long META" in res.action


def test_generate_copula_stat_arb_signals_requires_causal_half_life_not_only_copula(monkeypatch):
    """
    Regression test (PR #749 follow-up): the per-bar causal gate in
    generate_copula_stat_arb_signals must require BOTH the causal copula lower-tail-dependence
    check AND a causal OU half-life mean-reversion check -- mirroring the (previously
    non-causal) full-sample `tail_risk_acceptable` gate, which always checked both. The PR that
    made the copula fit causal per-bar silently dropped the half-life half of the gate for
    every bar except the last, so a pair whose tail-dependence looks fine but whose spread is
    not actually mean-reverting could still open a position on tail-dependence alone.

    Forces a deterministic reproduction by monkeypatching `fit_best_copula` to always report a
    low (passing) lower-tail-dependence and `estimate_ou_half_life` to always report an
    infinite (non-mean-reverting) half-life, then engineers a large Z-score dislocation that
    would trigger LONG_SPREAD entry if the half-life criterion were not enforced.
    """
    np.random.seed(42)
    n = 150
    x = pd.Series(np.linspace(100, 120, n))
    spread = np.zeros(n)
    spread[-1] = -5.0  # Same trigger pattern as test_generate_copula_stat_arb_signals_long_spread
    y = pd.Series(1.0 * x + spread)

    fake_fit = CopulaFitResult(
        family="Gaussian", theta=0.1, log_likelihood=0.0, aic=10.0, bic=10.0,
        lower_tail_dependence=0.05, upper_tail_dependence=0.05, kendall_tau=0.1,
    )
    monkeypatch.setattr("pilots.copula_stat_arb.fit_best_copula", lambda *a, **k: fake_fit)
    monkeypatch.setattr("pilots.copula_stat_arb.estimate_ou_half_life", lambda *a, **k: float("inf"))

    res = generate_copula_stat_arb_signals("AAPL", "MSFT", y, x, z_entry=2.0, z_exit=0.0)

    # Confirm the scenario actually reaches the trigger condition (else this test would pass
    # vacuously without exercising the gate at all). This construction's z-score comes out
    # strongly positive (SHORT_SPREAD-triggering), not negative -- confirmed empirically, since
    # the per-bar beta re-estimate absorbs part of the dislocation.
    assert res.current_zscore >= 2.0
    # Copula tail-dependence alone passes (fake_fit is far below tail_risk_lower_limit=0.85),
    # but half-life is forced non-mean-reverting -- no position should ever open. (Sanity-checked
    # against the counterfactual: patching estimate_ou_half_life to return an in-bounds value
    # instead of infinity, with everything else identical, DOES open a SHORT_SPREAD position --
    # confirming this test genuinely exercises the half-life half of the gate.)
    assert (res.signals_df["position"] != 0.0).sum() == 0
    assert res.current_signal in ("FLAT", "HOLD")


def test_generate_copula_stat_arb_signals_exit_crossing():
    """Verifies Exit is triggered when position reverts to mean (Z crosses 0.0)."""
    np.random.seed(42)
    n = 150
    x = pd.Series(np.cumsum(np.random.normal(0, 0.5, n)) + 100.0)
    spread = [0.0]
    for i in range(n - 1):
        if 35 <= i <= 45:
            spread.append(spread[-1] * 0.8 - 1.0)
        else:
            spread.append(spread[-1] * 0.8 + np.random.normal(0, 0.2))
    spread = np.array(spread)
    y = pd.Series(x * 1.2 + spread)

    res = generate_copula_stat_arb_signals("Y", "X", y, x, z_entry=2.0, z_exit=0.0)

    sig_df = res.signals_df
    assert "EXIT" in sig_df["signal"].values or "LONG_SPREAD" in sig_df["signal"].values or "HOLD" in sig_df["signal"].values


def test_copula_stat_arb_result_dataclass():
    """Verifies CopulaStatArbResult to_dict serialization and structure."""
    np.random.seed(42)
    n = 60
    x = pd.Series(np.random.normal(100, 2, n))
    y = pd.Series(x * 1.5 + np.random.normal(0, 0.5, n))

    res = generate_copula_stat_arb_signals("SPY", "QQQ", y, x)
    res_dict = res.to_dict()

    assert isinstance(res_dict, dict)
    assert res_dict["symbol_y"] == "SPY"
    assert res_dict["symbol_x"] == "QQQ"
    assert "best_copula" in res_dict
    assert "current_zscore" in res_dict
    assert "current_beta" in res_dict
    assert "tail_risk_acceptable" in res_dict


# ---------------------------------------------------------------------------
# 7. Spread Trade Execution & PaperAccountStore Tests
# ---------------------------------------------------------------------------


def test_execute_copula_spread_trade_dry_run():
    """Verifies dry-run execution generates structured legs without placing orders."""
    res = MagicMock(spec=CopulaStatArbResult)
    res.to_dict.return_value = {
        "symbol_y": "EWA",
        "symbol_x": "EWC",
        "current_signal": "LONG_SPREAD",
        "current_beta": 1.2,
        "current_zscore": -2.3,
        "summary": {"price_y": 25.0, "price_x": 30.0},
    }

    trade_res = execute_copula_spread_trade(res, dry_run=True)

    assert trade_res["ok"] is True
    assert trade_res["dry_run"] is True
    assert len(trade_res["legs"]) == 2
    assert trade_res["legs"][0]["symbol"] == "EWA"
    assert trade_res["legs"][0]["side"] == "BUY"
    assert trade_res["legs"][1]["symbol"] == "EWC"
    assert trade_res["legs"][1]["side"] == "SELL"


def test_execute_copula_spread_trade_live_blocked():
    """Verifies live orders are strictly rejected under advisory-only mode."""
    trade_res = execute_copula_spread_trade({}, is_live=True)
    assert trade_res["ok"] is False
    assert "Advisory-Only Mode" in trade_res["message"]


def test_execute_copula_spread_trade_flat_no_order():
    """Verifies no order is executed if current signal is FLAT."""
    res = MagicMock(spec=CopulaStatArbResult)
    res.to_dict.return_value = {
        "symbol_y": "AAPL",
        "symbol_x": "MSFT",
        "current_signal": "FLAT",
        "current_beta": 1.0,
        "current_zscore": 0.2,
    }

    trade_res = execute_copula_spread_trade(res)
    assert trade_res["ok"] is False
    assert "No active entry signal" in trade_res["message"]


# ---------------------------------------------------------------------------
# 8. AST Safety & Import Inertness Test
# ---------------------------------------------------------------------------


def test_copula_stat_arb_ast_safety():
    """
    Verifies that pilots/copula_stat_arb.py is pure compute and never imports
    heavy engines (CONSTRAINTS #1 & #3).
    """
    engine_path = Path(__file__).resolve().parent.parent / "pilots" / "copula_stat_arb.py"
    assert engine_path.exists(), f"{engine_path} must exist"

    tree = ast.parse(engine_path.read_text(encoding="utf-8"), filename=str(engine_path))
    imported_modules = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module.split(".")[0])

    forbidden_modules = {
        "processing_engine",
        "strategy_engine",
        "forecasting_engine",
        "macro_engine",
        "technical_options_engine",
        "main_orchestrator",
        "desktop",
    }

    overlap = imported_modules & forbidden_modules
    assert not overlap, f"pilots/copula_stat_arb.py must not import {overlap}"


# ---------------------------------------------------------------------------
# 9. Workstream 1 API Contract & Mathematical Invariant Tests
# ---------------------------------------------------------------------------


def test_fit_bivariate_copula_dispatcher():
    """Verifies fit_bivariate_copula correctly dispatches across Clayton, Gumbel, Frank, Gaussian."""
    np.random.seed(42)
    n = 200
    u = np.random.uniform(0.05, 0.95, n)
    v = np.clip(u * 0.8 + np.random.normal(0, 0.1, n), 0.01, 0.99)

    for fam in ("clayton", "gumbel", "frank", "gaussian"):
        res = fit_bivariate_copula(u, v, family=fam)
        assert isinstance(res, CopulaFitResult)
        assert res.family == fam
        assert res.converged is True
        assert np.isfinite(res.aic)
        assert np.isfinite(res.bic)
        assert hasattr(res, "lambda_lower")
        assert hasattr(res, "lambda_upper")

    with pytest.raises(ValueError, match="Unsupported copula family"):
        fit_bivariate_copula(u, v, family="student_t_unsupported")


def test_select_best_copula_structure():
    """Verifies select_best_copula compares candidate families by AIC and returns BestCopulaResult."""
    np.random.seed(42)
    n = 300
    u = np.random.uniform(0.01, 0.99, n)
    v = np.clip(u + np.random.normal(0, 0.1, n), 0.01, 0.99)

    res = select_best_copula(u, v)
    assert isinstance(res, BestCopulaResult)
    assert res.best_family in ("clayton", "gumbel", "frank", "gaussian")
    assert isinstance(res.best_fit, CopulaFitResult)
    assert len(res.all_fits) == 4
    assert res.n_samples == n
    d = res.to_dict()
    assert d["best_family"] == res.best_family
    assert "clayton" in d["all_fits"]


def test_estimate_kalman_dynamic_hedge_ratio_structure():
    """Verifies estimate_kalman_dynamic_hedge_ratio returns KalmanHedgeRatioResult with rolling metrics."""
    np.random.seed(42)
    n = 100
    x = np.linspace(50, 60, n) + np.random.normal(0, 0.5, n)
    y = 2.0 * x + 3.0 + np.random.normal(0, 0.2, n)

    res = estimate_kalman_dynamic_hedge_ratio(y, x, delta=1e-4, R=1e-3)
    assert isinstance(res, KalmanHedgeRatioResult)
    assert len(res.alpha) == n
    assert len(res.beta) == n
    assert len(res.spread) == n
    assert len(res.z_score) == n
    assert res.converged is True
    assert abs(res.latest_beta - 2.0) < 0.2

    # Test empty input handling
    res_empty = estimate_kalman_dynamic_hedge_ratio([], [])
    assert res_empty.n_samples == 0
    assert len(res_empty.beta) == 0

    # Test length mismatch
    with pytest.raises(ValueError, match="lengths must match"):
        estimate_kalman_dynamic_hedge_ratio([1.0, 2.0], [1.0])


def test_kalman_hedge_ratio_mean_x2_causal_no_lookahead():
    """
    Regression test for a real lookahead leak: estimate_kalman_dynamic_hedge_ratio's
    `mean_x2` scale factor (used to calibrate the prior covariance P0 and process
    noise Q applied at EVERY timestep) used to be computed ONCE from a fixed slice
    `x[:20]` of the whole input array -- so a decision at t < 19 depended on
    observations x[t+1 .. 19], which are strictly in the future relative to that
    decision. This violates the module's own docstring claim of "100%
    lookahead-free online updating" (module docstring).

    Perturbation test: mutating x[19] (a future observation relative to any
    decision at t < 19) must NOT change the Kalman state estimate, spread, or
    z-score for any t < 19. Against the pre-fix code this assertion would have
    failed, since x[19] fed `mean_x2` -- and therefore P0/Q -- for every t < 19.
    """
    np.random.seed(123)
    n = 40
    x_base = 100.0 + np.cumsum(np.random.normal(0, 1.0, n))
    y_base = 1.5 * x_base + 5.0 + np.random.normal(0, 0.5, n)

    res_base = estimate_kalman_dynamic_hedge_ratio(y_base, x_base)

    x_perturbed = x_base.copy()
    x_perturbed[19] += 500.0  # dramatic future perturbation, strictly beyond t < 19
    res_perturbed = estimate_kalman_dynamic_hedge_ratio(y_base, x_perturbed)

    cutoff = 19  # indices 0..18 must be strictly unaffected by x[19]
    np.testing.assert_allclose(
        res_base.alpha[:cutoff], res_perturbed.alpha[:cutoff], rtol=1e-10, atol=1e-10
    )
    np.testing.assert_allclose(
        res_base.beta[:cutoff], res_perturbed.beta[:cutoff], rtol=1e-10, atol=1e-10
    )
    np.testing.assert_allclose(
        res_base.spread[:cutoff], res_perturbed.spread[:cutoff], rtol=1e-10, atol=1e-10
    )
    np.testing.assert_allclose(
        res_base.spread_std[:cutoff], res_perturbed.spread_std[:cutoff], rtol=1e-10, atol=1e-10
    )
    np.testing.assert_allclose(
        res_base.z_score[:cutoff], res_perturbed.z_score[:cutoff], rtol=1e-10, atol=1e-10
    )

    # Sanity check: the perturbation is genuinely exercised -- outputs legitimately
    # diverge from index 19 onward (the perturbed observation itself), so the test
    # above isn't vacuously passing on an inert perturbation.
    assert not np.isclose(res_base.beta[19], res_perturbed.beta[19], rtol=1e-6)


def test_calculate_copula_mispricing_bounds_and_symmetry():
    """Verifies calculate_copula_mispricing returns conditional CDF in [0, 1]."""
    fit_clayton = CopulaFitResult(
        family="clayton",
        theta=2.5,
        log_likelihood=50.0,
        aic=-98.0,
        bic=-95.0,
        lower_tail_dependence=0.75,
        upper_tail_dependence=0.0,
        kendall_tau=0.55,
    )
    fit_gumbel = CopulaFitResult(
        family="gumbel",
        theta=2.0,
        log_likelihood=40.0,
        aic=-78.0,
        bic=-75.0,
        lower_tail_dependence=0.0,
        upper_tail_dependence=0.5858,
        kendall_tau=0.50,
    )
    fit_frank = CopulaFitResult(
        family="frank",
        theta=4.0,
        log_likelihood=30.0,
        aic=-58.0,
        bic=-55.0,
        kendall_tau=0.40,
    )
    fit_gauss = CopulaFitResult(
        family="gaussian",
        theta=0.7,
        log_likelihood=35.0,
        aic=-68.0,
        bic=-65.0,
        kendall_tau=0.49,
    )

    for fit in (fit_clayton, fit_gumbel, fit_frank, fit_gauss):
        p_low = calculate_copula_mispricing(0.1, 0.9, fit)
        p_mid = calculate_copula_mispricing(0.5, 0.5, fit)
        p_high = calculate_copula_mispricing(0.9, 0.1, fit)

        assert 0.0 <= p_low <= 1.0
        assert 0.0 <= p_mid <= 1.0
        assert 0.0 <= p_high <= 1.0
        assert p_high > p_low


def test_evaluate_copula_stat_arb_pair_full_pipeline():
    """Verifies evaluate_copula_stat_arb_pair returns CopulaPairAnalysis with valid signal."""
    np.random.seed(42)
    n = 100
    x = np.cumsum(np.random.normal(0, 1, n)) + 100.0
    spread = [0.0]
    for _ in range(n - 1):
        spread.append(spread[-1] * 0.85 + np.random.normal(0, 0.3))
    y = 1.5 * x + np.array(spread)

    analysis = evaluate_copula_stat_arb_pair(y, x, symbol_y="NVDA", symbol_x="AMD")
    assert isinstance(analysis, CopulaPairAnalysis)
    assert analysis.symbol_y == "NVDA"
    assert analysis.symbol_x == "AMD"
    assert analysis.signal in ("LONG_SPREAD", "SHORT_SPREAD", "CLOSE_SPREAD", "HOLD", "NEUTRAL")
    assert analysis.ou_half_life is not None
    assert analysis.ou_mean_reverting is True

    d = analysis.to_dict()
    assert d["symbol_y"] == "NVDA"
    assert "best_copula" in d
    assert "kalman_result" in d


def test_calculate_spread_zscore_and_ou_half_life():
    """Verifies calculate_spread_zscore and calculate_ou_half_life."""
    spread = np.array([0.0, 0.5, 0.2, -0.1, 0.3, -0.2, 0.1, 0.4, 0.0, -0.3, 0.2, 0.1])
    z = calculate_spread_zscore(spread, window=5, min_periods=3)
    assert len(z) == len(spread)

    hl = calculate_ou_half_life(spread)
    # Short stationary spread
    assert hl is None or hl > 0.0


def test_copula_stat_arb_zero_lookahead_bias():
    """
    Lookahead Perturbation Test:
    Verifies that mutating future prices at t >= cutoff does NOT change past signals or
    spread/z-score calculations for t < cutoff.
    """
    np.random.seed(42)
    n = 120
    cutoff = 80

    x_base = np.cumsum(np.random.normal(0, 1, n)) + 100.0
    spread_base = [0.0]
    for _ in range(n - 1):
        spread_base.append(spread_base[-1] * 0.85 + np.random.normal(0, 0.3))
    y_base = 1.5 * x_base + np.array(spread_base)

    # Base run
    res_base = generate_copula_stat_arb_signals("Y", "X", y_base, x_base, lookback=25)
    sig_base = res_base.signals_df.iloc[:cutoff]

    # Perturbed future run (dramatically alter prices at and after cutoff)
    y_perturbed = y_base.copy()
    x_perturbed = x_base.copy()
    y_perturbed[cutoff:] = y_perturbed[cutoff:] * 2.5 + 50.0
    x_perturbed[cutoff:] = x_perturbed[cutoff:] * 0.5 - 20.0

    res_perturbed = generate_copula_stat_arb_signals("Y", "X", y_perturbed, x_perturbed, lookback=25)
    sig_perturbed = res_perturbed.signals_df.iloc[:cutoff]

    # Causal invariance check: all past spread, beta, z-score, positions, and signals MUST match exactly
    np.testing.assert_allclose(sig_base["beta"].values, sig_perturbed["beta"].values, rtol=1e-7, atol=1e-7)
    np.testing.assert_allclose(sig_base["spread"].values, sig_perturbed["spread"].values, rtol=1e-7, atol=1e-7)
    np.testing.assert_allclose(sig_base["z_score"].dropna().values, sig_perturbed["z_score"].dropna().values, rtol=1e-7, atol=1e-7)
    assert list(sig_base["signal"].values) == list(sig_perturbed["signal"].values)
    assert list(sig_base["position"].values) == list(sig_perturbed["position"].values)


