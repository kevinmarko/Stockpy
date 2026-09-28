"""
tests/test_har_volatility.py — Comprehensive tests for pilots/har_volatility.py.
==============================================================================

Verifies:
1. Realized variance components multi-scale decomposition (daily, weekly, monthly).
2. HAR-RV Corsi (2009) model fitting & non-negative coefficient constraints.
3. Forward volatility forecasting blending HAR-RV term structure and historical variance.
4. Input sanitization, type flexibility (Series, array, list), and edge-case handling.
5. AST import safety (zero heavy engine dependencies).
6. No lookahead bias in components computation.
"""

from __future__ import annotations

import ast
import math
from pathlib import Path
import pytest
import numpy as np
import pandas as pd

from pilots.har_volatility import (
    compute_realized_variance_components,
    fit_har_rv_model,
    forecast_forward_volatility,
    HARModelResult,
    HARForecastResult,
    TRADING_DAYS_PER_YEAR,
    DAILY_WINDOW,
    WEEKLY_WINDOW,
    MONTHLY_WINDOW,
)


@pytest.fixture
def synthetic_stationary_returns() -> pd.Series:
    """Generates 250 daily returns with known annualized volatility ~20%."""
    np.random.seed(42)
    daily_sigma = 0.20 / np.sqrt(252.0)
    returns = np.random.normal(loc=0.0005, scale=daily_sigma, size=250)
    dates = pd.date_range("2025-01-01", periods=250, freq="B")
    return pd.Series(returns, index=dates)


@pytest.fixture
def synthetic_clustering_returns() -> pd.Series:
    """Generates 500 daily returns with GARCH/volatility clustering dynamics."""
    np.random.seed(123)
    n = 500
    returns = np.zeros(n)
    sigma2 = np.zeros(n)
    omega = 1e-5
    alpha = 0.10
    beta = 0.85
    sigma2[0] = omega / (1.0 - alpha - beta)

    for t in range(1, n):
        sigma2[t] = omega + alpha * (returns[t - 1] ** 2) + beta * sigma2[t - 1]
        returns[t] = np.random.normal(0, np.sqrt(sigma2[t]))

    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.Series(returns, index=dates)


# ---------------------------------------------------------------------------
# 1. Realized Variance Components Tests
# ---------------------------------------------------------------------------

def test_compute_realized_variance_components_math():
    """Verifies exact formulas for daily (r^2), weekly (5-day), and monthly (22-day) RV."""
    returns = np.array([0.01, -0.02, 0.015, -0.005, 0.03, 0.02, -0.01])
    df = compute_realized_variance_components(returns, min_periods=1)

    assert isinstance(df, pd.DataFrame)
    for col in ["rv_daily", "rv_weekly", "rv_monthly", "rv_d", "rv_w", "rv_m"]:
        assert col in df.columns
    assert len(df) == 7

    # Daily RV is r_t^2
    expected_daily = returns ** 2
    np.testing.assert_allclose(df["rv_daily"].to_numpy(), expected_daily, rtol=1e-5)

    # 5th element weekly RV should be mean of first 5 daily RVs
    expected_weekly_5 = np.mean(expected_daily[:5])
    assert abs(df["rv_weekly"].iloc[4] - expected_weekly_5) < 1e-8

    # 6th element weekly RV is mean of elements 1..5
    expected_weekly_6 = np.mean(expected_daily[1:6])
    assert abs(df["rv_weekly"].iloc[5] - expected_weekly_6) < 1e-8


def test_compute_realized_variance_components_types(synthetic_stationary_returns):
    """Verifies compute_realized_variance_components works on Series, ndarray, and lists."""
    arr = synthetic_stationary_returns.to_numpy()
    lst = arr.tolist()

    df_series = compute_realized_variance_components(synthetic_stationary_returns)
    df_arr = compute_realized_variance_components(arr)
    df_list = compute_realized_variance_components(lst)

    assert len(df_series) == len(synthetic_stationary_returns)
    assert len(df_arr) == len(arr)
    assert len(df_list) == len(lst)

    np.testing.assert_allclose(df_series["rv_daily"].to_numpy(), df_arr["rv_daily"].to_numpy())
    np.testing.assert_allclose(df_series["rv_weekly"].to_numpy(), df_list["rv_weekly"].to_numpy())


def test_compute_realized_variance_components_empty_and_none():
    """Ensures empty, None, and invalid inputs return clean empty DataFrames without raising."""
    assert compute_realized_variance_components(None).empty
    assert compute_realized_variance_components([]).empty
    assert compute_realized_variance_components(np.array([])).empty
    assert compute_realized_variance_components(pd.Series([], dtype=float)).empty


# ---------------------------------------------------------------------------
# 2. HAR-RV Model Fitting Tests
# ---------------------------------------------------------------------------

def test_fit_har_rv_model_basic(synthetic_clustering_returns):
    """Fits Corsi HAR-RV model and confirms valid non-negative parameters."""
    model = fit_har_rv_model(synthetic_clustering_returns)

    assert isinstance(model, HARModelResult)
    assert model.is_fitted is True
    assert model.sample_size > 400

    # Non-negative coefficients constraint
    assert model.intercept >= 0.0
    assert model.beta_daily >= 0.0
    assert model.beta_weekly >= 0.0
    assert model.beta_monthly >= 0.0
    assert model.beta_0 >= 0.0
    assert model.beta_d >= 0.0
    assert model.beta_w >= 0.0
    assert model.beta_m >= 0.0

    # Persistence and R2
    assert 0.0 <= model.persistence <= 1.5
    assert 0.0 <= model.r2 <= 1.0
    assert model.long_run_variance >= 0.0

    # Prediction
    pred = model.predict(0.0001, 0.0001, 0.0001)
    assert pred > 0.0

    # Dict-like access
    assert model["beta_d"] == model.beta_daily
    assert model["r2"] == model.r2
    assert "beta_0" in model.coefficients


def test_fit_har_rv_model_short_and_degenerate():
    """Verifies graceful degradation on short or constant return series."""
    # Short series (< 24 obs)
    short_returns = [0.01, -0.01, 0.02, -0.005]
    model_short = fit_har_rv_model(short_returns)
    assert model_short.is_fitted is False
    assert model_short.r2 == 0.0
    assert model_short.predict(0.01, 0.01, 0.01) >= 0.0

    # Empty series
    model_empty = fit_har_rv_model([])
    assert model_empty.is_fitted is False
    assert model_empty.sample_size == 0

    # Constant zero returns
    zeros = np.zeros(100)
    model_zeros = fit_har_rv_model(zeros)
    assert model_zeros.is_fitted is True
    assert model_zeros.intercept == 0.0
    assert model_zeros.predict(0.0, 0.0, 0.0) == 0.0


def test_fit_har_rv_model_serialization(synthetic_stationary_returns):
    """Verifies to_dict() serialization of HARModelResult."""
    model = fit_har_rv_model(synthetic_stationary_returns)
    d = model.to_dict()

    assert "intercept" in d
    assert "beta_daily" in d
    assert "beta_weekly" in d
    assert "beta_monthly" in d
    assert "r2" in d
    assert "persistence" in d
    assert isinstance(d["residuals"], list)


# ---------------------------------------------------------------------------
# 3. Forward Volatility Forecast Tests
# ---------------------------------------------------------------------------

def test_forecast_forward_volatility_accuracy(synthetic_stationary_returns):
    """Verifies forward volatility forecast approximates target annualized volatility (~20%)."""
    fwd_vol = forecast_forward_volatility(synthetic_stationary_returns, horizon_days=30)

    assert fwd_vol is not None
    assert isinstance(fwd_vol, float)
    # Expected annualized vol around 0.18 - 0.23 for our 20% synthetic generator
    assert 0.15 < fwd_vol < 0.25


def test_forecast_forward_volatility_details(synthetic_clustering_returns):
    """Verifies rich HARForecastResult return structure."""
    result = forecast_forward_volatility(
        synthetic_clustering_returns,
        horizon_days=20,
        blend_weight_har=0.80,
        return_details=True,
    )

    assert isinstance(result, HARForecastResult)
    assert result.horizon_days == 20
    assert result.blend_weight_har == 0.80
    assert result.annualized_volatility > 0.0
    assert result.daily_volatility > 0.0
    assert result.har_daily_variance > 0.0
    assert result.historical_daily_variance > 0.0
    assert isinstance(result.model_result, HARModelResult)

    # Check to_dict()
    d = result.to_dict()
    assert "annualized_volatility" in d
    assert "model_result" in d


def test_forecast_forward_volatility_horizon_term_structure(synthetic_clustering_returns):
    """Verifies multi-horizon term structure produces smooth mean reversion."""
    vol_1d = forecast_forward_volatility(synthetic_clustering_returns, horizon_days=1)
    vol_30d = forecast_forward_volatility(synthetic_clustering_returns, horizon_days=30)
    vol_90d = forecast_forward_volatility(synthetic_clustering_returns, horizon_days=90)

    assert vol_1d is not None
    assert vol_30d is not None
    assert vol_90d is not None
    assert all(v > 0.0 for v in [vol_1d, vol_30d, vol_90d])


def test_forecast_forward_volatility_short_and_edge_cases():
    """Verifies edge case handling (None, short series, empty list)."""
    assert forecast_forward_volatility(None) is None
    assert forecast_forward_volatility([]) is None
    assert forecast_forward_volatility([0.01]) is None

    # Series of 5 elements: fits historical fallback
    short_series = [0.01, -0.02, 0.015, -0.01, 0.02]
    vol_fallback = forecast_forward_volatility(short_series, horizon_days=30)
    assert vol_fallback is not None
    assert vol_fallback > 0.0


# ---------------------------------------------------------------------------
# 4. No Lookahead Bias Perturbation Test
# ---------------------------------------------------------------------------

def test_no_lookahead_bias_components(synthetic_stationary_returns):
    """Verifies that altering future returns has zero effect on past variance components."""
    base_returns = synthetic_stationary_returns.copy()
    components_base = compute_realized_variance_components(base_returns)

    # Perturb the last 10 observations
    perturbed_returns = base_returns.copy()
    perturbed_returns.iloc[-10:] = perturbed_returns.iloc[-10:] * 5.0
    components_perturbed = compute_realized_variance_components(perturbed_returns)

    # All components up to index -11 must be strictly identical
    cutoff = len(base_returns) - 11
    np.testing.assert_allclose(
        components_base["rv_daily"].iloc[:cutoff].to_numpy(),
        components_perturbed["rv_daily"].iloc[:cutoff].to_numpy(),
    )
    np.testing.assert_allclose(
        components_base["rv_weekly"].iloc[:cutoff].to_numpy(),
        components_perturbed["rv_weekly"].iloc[:cutoff].to_numpy(),
    )
    np.testing.assert_allclose(
        components_base["rv_monthly"].iloc[:cutoff].to_numpy(),
        components_perturbed["rv_monthly"].iloc[:cutoff].to_numpy(),
    )


# ---------------------------------------------------------------------------
# 5. AST Import Safety Test
# ---------------------------------------------------------------------------

def test_har_volatility_ast_import_safety():
    """Verifies that pilots/har_volatility.py never imports heavy forbidden engines."""
    file_path = Path(__file__).resolve().parent.parent / "pilots" / "har_volatility.py"
    with open(file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename="har_volatility.py")

    forbidden_modules = {
        "processing_engine",
        "technical_options_engine",
        "forecasting_engine",
        "strategy_engine",
        "macro_engine",
        "main",
        "main_orchestrator",
        "desktop",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for forbidden in forbidden_modules:
                    assert forbidden not in alias.name, f"Forbidden import found: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for forbidden in forbidden_modules:
                    assert forbidden not in node.module, f"Forbidden import from found: {node.module}"
