"""
InvestYo Quant Platform - Pairs Trading (STRATEGY_REGISTRY) Validation Test
=============================================================================
Tests ``scripts.refresh_validations._build_pairs_trading_adapter`` — the
production adapter registered as ``STRATEGY_REGISTRY["pairs_trading"]``.

Verifies:
  1. Registry signature, turnover (0.04), and universe (["SPY", "XOM", "CVX"]).
  2. Adapter return shapes (X, y, precomputed dict).
  3. Lookahead freedom: future perturbations do not leak into past signals.
  4. Faber (2007) SMA-200 market trend filter correctly de-risks to cash.
  5. End-to-end StrategyValidationHarness execution.
"""

import numpy as np
import pandas as pd
import pytest

import validation.harness as harness_module
from execution.cost_model import TieredCostModel
from scripts.refresh_validations import (
    STRATEGY_REGISTRY,
    _build_pairs_trading_adapter,
    _make_strategy_fn,
)
from validation.harness import StrategyValidationHarness


@pytest.fixture
def synthetic_pairs_closes() -> pd.DataFrame:
    """Generates 500 business days of synthetic cointegrated prices for XOM/CVX and benchmark SPY."""
    np.random.seed(42)
    n = 500
    dates = pd.date_range(start="2020-01-01", periods=n, freq="B")

    # Benchmark SPY in an overall rising trend
    spy_trend = np.linspace(100, 180, n)
    spy_noise = np.cumsum(np.random.normal(0, 0.5, n))
    spy = spy_trend + spy_noise

    # Asset X (CVX)
    cvx = np.cumsum(np.random.normal(0, 0.6, n)) + 80.0

    # Stationary spread
    spread = [0.0]
    for _ in range(n - 1):
        spread.append(0.85 * spread[-1] + np.random.normal(0, 0.15))
    spread = np.array(spread)

    # Asset Y (XOM) cointegrated with CVX
    xom = 1.1 * cvx + 5.0 + spread

    return pd.DataFrame(
        {
            "SPY": spy,
            "XOM": xom,
            "CVX": cvx,
        },
        index=dates,
    )


def test_pairs_trading_registry_entry():
    """Verify STRATEGY_REGISTRY registration for pairs_trading."""
    assert "pairs_trading" in STRATEGY_REGISTRY
    adapter_fn, turnover, universe = STRATEGY_REGISTRY["pairs_trading"]
    assert adapter_fn is _build_pairs_trading_adapter
    assert turnover == 0.04
    assert universe == ["SPY", "XOM", "CVX"]


def test_pairs_trading_adapter_shape_and_returns(synthetic_pairs_closes):
    """Verify adapter returns well-formed (X, y, precomputed)."""
    X, y, precomputed = _build_pairs_trading_adapter(synthetic_pairs_closes)

    assert not X.empty
    assert not y.empty
    assert isinstance(precomputed, dict)
    assert "Pairs_MeanReversion_DynamicHedge" in precomputed

    # Check required feature columns
    for col in ["Z_Score", "Spread", "Beta", "Alpha", "Rolling_P", "SPY_SMA_200"]:
        assert col in X.columns, f"Missing feature column: {col}"

    assert len(X) == len(y)
    assert len(X) == len(precomputed["Pairs_MeanReversion_DynamicHedge"])
    assert np.isfinite(y).all()
    assert np.isfinite(precomputed["Pairs_MeanReversion_DynamicHedge"]).all()


def test_pairs_trading_adapter_lookahead_free(synthetic_pairs_closes):
    """Perturbing prices strictly after a cutoff must not alter features or returns at/before cutoff."""
    baseline_X, baseline_y, baseline_precomputed = _build_pairs_trading_adapter(
        synthetic_pairs_closes
    )

    perturbed = synthetic_pairs_closes.copy()
    mid = len(perturbed) // 2
    cutoff_date = synthetic_pairs_closes.index[mid]

    # Perturb future prices drastically
    perturbed.iloc[mid + 1 :, :] = perturbed.iloc[mid + 1 :, :] * 5.0 + 500.0

    perturbed_X, perturbed_y, perturbed_precomputed = _build_pairs_trading_adapter(
        perturbed
    )

    common_idx = baseline_X.index.intersection(perturbed_X.index)
    common_pre = common_idx[common_idx <= cutoff_date]
    assert len(common_pre) > 50

    pd.testing.assert_frame_equal(
        baseline_X.loc[common_pre],
        perturbed_X.loc[common_pre],
        check_exact=False,
        rtol=1e-5,
    )
    pd.testing.assert_series_equal(
        baseline_precomputed["Pairs_MeanReversion_DynamicHedge"].loc[common_pre],
        perturbed_precomputed["Pairs_MeanReversion_DynamicHedge"].loc[common_pre],
        check_exact=False,
        rtol=1e-5,
    )


def test_pairs_trading_trend_gate_derisks_to_cash():
    """Verify that when SPY is in a deep downtrend below SMA-200, daily returns are forced to 0.0."""
    n = 300
    dates = pd.date_range(start="2020-01-01", periods=n, freq="B")

    # SPY crashes below SMA-200 in the second half
    spy = np.concatenate([np.linspace(150, 160, 200), np.linspace(160, 50, 100)])
    cvx = np.cumsum(np.random.normal(0, 0.5, n)) + 100.0
    spread = np.sin(np.linspace(0, 10, n)) * 5.0
    xom = cvx + spread

    closes = pd.DataFrame({"SPY": spy, "XOM": xom, "CVX": cvx}, index=dates)
    X, y, precomputed = _build_pairs_trading_adapter(closes)

    returns = precomputed["Pairs_MeanReversion_DynamicHedge"]
    # In the final 50 bars, SPY is far below SMA-200, so trend gate must force returns to 0.0
    downtrend_tail = returns.iloc[-50:]
    assert (downtrend_tail == 0.0).all()


def test_pairs_trading_validation_harness_integration(synthetic_pairs_closes, tmp_path, monkeypatch):
    """Smoke test StrategyValidationHarness execution with pairs_trading adapter."""
    monkeypatch.setattr(
        harness_module,
        "get_universe_with_survivorship_warning",
        lambda _d: (["XOM", "CVX"], {"n_current": 2, "n_at_date": 2, "n_delisted_in_period": 0, "estimated_bias_pct": 0.5}),
    )

    X, y, precomputed = _build_pairs_trading_adapter(synthetic_pairs_closes)
    strategy_fn = _make_strategy_fn(precomputed, turnover=0.04)

    cost_model = TieredCostModel()
    harness = StrategyValidationHarness(
        strategy_fn=strategy_fn,
        universe_fn=lambda _: ["XOM", "CVX"],
        cost_model=cost_model,
        n_cpcv_splits=5,
        n_test_splits=2,
        reports_dir=str(tmp_path),
    )

    report = harness.run(
        start_date=str(X.index[0].date()),
        end_date=str(X.index[-1].date()),
        X=X,
        y=y,
        strategy_name="pairs_trading_unit_test",
    )

    summary = report.to_summary_dict()
    assert isinstance(summary["deployable"], bool)
    assert np.isfinite(summary["sharpe"])
    assert np.isfinite(summary["pbo"])
    assert np.isfinite(summary["dsr"])
    assert np.isfinite(summary["max_drawdown"])
