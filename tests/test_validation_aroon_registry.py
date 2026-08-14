"""
InvestYo Quant Platform - Aroon Trend (STRATEGY_REGISTRY) Validation Test
===========================================================================
Tests ``scripts.refresh_validations._build_aroon_trend_adapter`` — the
production adapter registered as ``STRATEGY_REGISTRY["aroon_trend"]``.

Verifies:
  1. Registry signature, turnover (0.02), and universe (["SPY"]).
  2. Aroon indicator mathematical calculation (Up, Down, Oscillator).
  3. Adapter return shapes (X, y, precomputed dict).
  4. Lookahead freedom: future perturbations do not leak into past signals.
  5. Faber (2007) SMA-200 market trend filter correctly de-risks to cash.
  6. End-to-end StrategyValidationHarness execution.
"""

import numpy as np
import pandas as pd
import pytest

import validation.harness as harness_module
from execution.cost_model import TieredCostModel
from scripts.refresh_validations import (
    STRATEGY_REGISTRY,
    _aroon,
    _build_aroon_trend_adapter,
    _make_strategy_fn,
)
from validation.harness import StrategyValidationHarness


@pytest.fixture
def synthetic_spy_close() -> pd.Series:
    """Generates 500 business days of synthetic SPY daily close prices."""
    np.random.seed(42)
    n = 500
    dates = pd.date_range(start="2020-01-01", periods=n, freq="B")

    # Upward trending series with cyclical oscillations
    trend = np.linspace(200, 350, n)
    cycles = 15.0 * np.sin(np.linspace(0, 8 * np.pi, n))
    noise = np.cumsum(np.random.normal(0, 0.5, n))
    close = pd.Series(trend + cycles + noise, index=dates, name="Close")
    return close


def test_aroon_registry_entry():
    """Verify STRATEGY_REGISTRY registration for aroon_trend."""
    assert "aroon_trend" in STRATEGY_REGISTRY
    adapter_fn, turnover, universe = STRATEGY_REGISTRY["aroon_trend"]
    assert adapter_fn is _build_aroon_trend_adapter
    assert turnover == 0.02
    assert universe == ["SPY"]


def test_aroon_indicator_math():
    """Verify mathematical properties of the _aroon helper."""
    # 1. Monotonically increasing series (window=25)
    inc = pd.Series(np.arange(1, 51, dtype=float))
    up_inc, down_inc, osc_inc = _aroon(inc, window=25)

    # Valid values after 25-bar warmup
    valid_up = up_inc.iloc[24:]
    valid_down = down_inc.iloc[24:]
    valid_osc = osc_inc.iloc[24:]

    assert (valid_up == 100.0).all()
    assert (valid_down == 4.0).all()
    assert (valid_osc == 96.0).all()

    # 2. Monotonically decreasing series
    dec = pd.Series(np.arange(50, 0, -1, dtype=float))
    up_dec, down_dec, osc_dec = _aroon(dec, window=25)

    valid_up_dec = up_dec.iloc[24:]
    valid_down_dec = down_dec.iloc[24:]
    valid_osc_dec = osc_dec.iloc[24:]

    assert (valid_up_dec == 4.0).all()
    assert (valid_down_dec == 100.0).all()
    assert (valid_osc_dec == -96.0).all()


def test_aroon_adapter_shape_and_returns(synthetic_spy_close):
    """Verify adapter returns well-formed (X, y, precomputed)."""
    X, y, precomputed = _build_aroon_trend_adapter(synthetic_spy_close)

    assert not X.empty
    assert not y.empty
    assert isinstance(precomputed, dict)
    assert "Aroon_Trend_Gated" in precomputed

    for col in ["Aroon_Up", "Aroon_Down", "Aroon_Osc", "SMA_200"]:
        assert col in X.columns, f"Missing feature column: {col}"

    assert len(X) == len(y)
    assert len(X) == len(precomputed["Aroon_Trend_Gated"])
    assert np.isfinite(y).all()
    assert np.isfinite(precomputed["Aroon_Trend_Gated"]).all()


def test_aroon_adapter_accepts_dataframe(synthetic_spy_close):
    """Verify adapter handles DataFrame input as well as Series."""
    df = pd.DataFrame({"SPY": synthetic_spy_close})
    X, y, precomputed = _build_aroon_trend_adapter(df)
    assert not X.empty
    assert "Aroon_Trend_Gated" in precomputed


def test_aroon_adapter_lookahead_free(synthetic_spy_close):
    """Perturbing prices strictly after cutoff must not alter features or returns at/before cutoff."""
    baseline_X, baseline_y, baseline_precomputed = _build_aroon_trend_adapter(
        synthetic_spy_close
    )

    perturbed = synthetic_spy_close.copy()
    mid = len(perturbed) // 2
    cutoff_date = synthetic_spy_close.index[mid]

    # Perturb future prices drastically
    perturbed.iloc[mid + 1 :] = perturbed.iloc[mid + 1 :] * 3.0 + 1000.0

    perturbed_X, perturbed_y, perturbed_precomputed = _build_aroon_trend_adapter(
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
        baseline_precomputed["Aroon_Trend_Gated"].loc[common_pre],
        perturbed_precomputed["Aroon_Trend_Gated"].loc[common_pre],
        check_exact=False,
        rtol=1e-5,
    )


def test_aroon_trend_gate_derisks_to_cash():
    """Verify that when close < SMA-200, strategy return is 0.0."""
    n = 300
    dates = pd.date_range(start="2020-01-01", periods=n, freq="B")

    # Series crashes in second half
    prices = np.concatenate([np.linspace(100, 150, 200), np.linspace(150, 40, 100)])
    series = pd.Series(prices, index=dates)

    X, y, precomputed = _build_aroon_trend_adapter(series)
    returns = precomputed["Aroon_Trend_Gated"]

    # In final 50 bars, close is far below SMA-200
    downtrend_tail = returns.iloc[-50:]
    assert (downtrend_tail == 0.0).all()


def test_aroon_validation_harness_integration(synthetic_spy_close, tmp_path, monkeypatch):
    """Smoke test StrategyValidationHarness execution with aroon_trend adapter."""
    monkeypatch.setattr(
        harness_module,
        "get_universe_with_survivorship_warning",
        lambda _d: (["SPY"], {"n_current": 1, "n_at_date": 1, "n_delisted_in_period": 0, "estimated_bias_pct": 0.0}),
    )

    X, y, precomputed = _build_aroon_trend_adapter(synthetic_spy_close)
    strategy_fn = _make_strategy_fn(precomputed, turnover=0.02)

    cost_model = TieredCostModel()
    harness = StrategyValidationHarness(
        strategy_fn=strategy_fn,
        universe_fn=lambda _: ["SPY"],
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
        strategy_name="aroon_trend_unit_test",
    )

    summary = report.to_summary_dict()
    assert isinstance(summary["deployable"], bool)
    assert np.isfinite(summary["sharpe"])
    assert np.isfinite(summary["pbo"])
    assert np.isfinite(summary["dsr"])
    assert np.isfinite(summary["max_drawdown"])
