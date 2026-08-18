"""
tests/test_regime_diagnostics.py
================================
Unit tests for validation/regime_diagnostics.py and HMM model comparison.
"""

import math
import numpy as np
import pandas as pd
import pytest

from regime.hmm_regime import HMMRegimeDetector, build_feature_matrix
from validation.regime_diagnostics import (
    compare_model_configurations,
    evaluate_state_performance,
    run_walk_forward_evaluation,
)


@pytest.fixture
def sample_feature_df():
    """Generates synthetic price and macro series for 200 trading days."""
    np.random.seed(42)
    dates = pd.date_range(start="2023-01-01", periods=200, freq="B")

    # Calm regime for first 100 days, volatile for next 100 days
    calm_rets = np.random.normal(0.0008, 0.005, 100)
    turb_rets = np.random.normal(-0.0005, 0.020, 100)
    spy_return = np.concatenate([calm_rets, turb_rets])

    prices = 100.0 * np.cumprod(1.0 + spy_return)
    spy_price_df = pd.DataFrame({"Close": prices}, index=dates)

    vix = np.concatenate([np.random.uniform(12, 16, 100), np.random.uniform(22, 35, 100)])
    vix_series = pd.Series(vix, index=dates)

    yc = np.concatenate([np.random.uniform(0.5, 1.2, 100), np.random.uniform(-0.5, 0.2, 100)])
    yc_series = pd.Series(yc, index=dates)

    return build_feature_matrix(spy_price_df, vix_series, yc_series)


def test_walk_forward_evaluation_schema_and_length(sample_feature_df):
    """Walk forward should produce records for each row after min_fit_rows."""
    min_rows = 50
    wf_df = run_walk_forward_evaluation(
        sample_feature_df,
        n_states=3,
        covariance_type="diag",
        retrain_freq_days=7,
        min_fit_rows=min_rows,
        random_state=42,
    )

    expected_len = len(sample_feature_df) - min_rows
    assert len(wf_df) == expected_len
    assert "dominant_state" in wf_df.columns
    assert "dominant_label" in wf_df.columns
    assert "risk_on_probability" in wf_df.columns
    assert "spy_return" in wf_df.columns

    # Probabilities must be in [0, 1]
    assert (wf_df["risk_on_probability"] >= 0.0).all()
    assert (wf_df["risk_on_probability"] <= 1.0).all()


def test_evaluate_state_performance_metrics(sample_feature_df):
    """Verifies state performance calculation produces valid Sharpe, Vol, and MaxDD."""
    wf_df = run_walk_forward_evaluation(
        sample_feature_df,
        n_states=3,
        covariance_type="diag",
        min_fit_rows=50,
        random_state=42,
    )

    perf = evaluate_state_performance(wf_df, return_column="spy_return")

    assert "total_days" in perf
    assert perf["total_days"] == len(wf_df)
    assert "state_metrics" in perf

    for state, m in perf["state_metrics"].items():
        assert m["days_count"] > 0
        assert m["annualized_volatility"] >= 0.0
        assert -1.0 <= m["max_drawdown"] <= 0.0
        assert 0.0 <= m["win_rate"] <= 1.0


def test_compare_model_configurations_ranks_by_aic(sample_feature_df):
    """Verifies model comparison ranks candidate configurations by AIC ascending."""
    comp = compare_model_configurations(
        sample_feature_df,
        state_counts=[2, 3],
        covariance_types=["diag", "full"],
        random_state=42,
    )

    assert len(comp) == 4
    # Check sorted ascending by AIC
    aics = [c["aic"] for c in comp]
    assert aics == sorted(aics)

    for item in comp:
        assert "n_states" in item
        assert "covariance_type" in item
        assert "log_likelihood" in item
        assert "aic" in item
        assert "bic" in item
        assert item["n_parameters"] > 0


def test_extreme_market_conditions():
    """Verify that degenerate/extreme conditions do not cause crashes or NaN outputs."""
    np.random.seed(42)
    dates = pd.date_range(start="2023-01-01", periods=150, freq="B")
    
    spy_return = np.random.normal(0.0008, 0.005, 150)
    # 1. Flash crash: -15% return on day 100
    spy_return[100] = -0.15
    # 2. Zero volatility period: Days 110-120 exactly 0 return
    spy_return[110:120] = 0.0
    
    prices = 100.0 * np.cumprod(1.0 + spy_return)
    spy_price_df = pd.DataFrame({"Close": prices}, index=dates)
    
    # 3. Inverted yield curve: strongly negative
    yc = np.random.uniform(0.5, 1.2, 150)
    yc[130:140] = -2.5
    yc_series = pd.Series(yc, index=dates)
    
    vix = np.random.uniform(12, 16, 150)
    # VIX spike
    vix[100:105] = 80.0
    vix_series = pd.Series(vix, index=dates)
    
    features = build_feature_matrix(spy_price_df, vix_series, yc_series)
    
    wf_df = run_walk_forward_evaluation(
        features,
        n_states=3,
        covariance_type="diag",
        retrain_freq_days=7,
        min_fit_rows=50,
        random_state=42,
    )
    
    assert not wf_df["risk_on_probability"].isna().any()
    assert (wf_df["risk_on_probability"] >= 0.0).all()
    assert (wf_df["risk_on_probability"] <= 1.0).all()
