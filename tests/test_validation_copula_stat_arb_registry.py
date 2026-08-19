"""
InvestYo Quant Platform - Copula Statistical Arbitrage (STRATEGY_REGISTRY) Validation Test
=============================================================================================
Tests ``scripts.refresh_validations._build_copula_stat_arb_adapter`` — the
production adapter registered as ``STRATEGY_REGISTRY["copula_stat_arb"]``.

Verifies:
  1. Registry signature, turnover (0.04), and universe (["KO", "PEP"]).
  2. Adapter return shapes (X, y, precomputed dict).
  3. Lookahead freedom: future perturbations do not leak into past signals.
  4. Adapter calls the real production entry point
     (``pilots.copula_stat_arb.generate_copula_stat_arb_signals``), not a
     re-implementation.
  5. End-to-end StrategyValidationHarness execution.
"""

import numpy as np
import pandas as pd
import pytest

import validation.harness as harness_module
from execution.cost_model import TieredCostModel
from scripts.refresh_validations import (
    STRATEGY_REGISTRY,
    _build_copula_stat_arb_adapter,
    _make_strategy_fn,
)
from validation.harness import StrategyValidationHarness


@pytest.fixture
def synthetic_copula_closes() -> pd.DataFrame:
    """Generates 500 business days of synthetic cointegrated prices for a KO/PEP-style pair."""
    np.random.seed(7)
    n = 500
    dates = pd.date_range(start="2020-01-01", periods=n, freq="B")

    # Asset X (PEP)
    pep = np.cumsum(np.random.normal(0, 0.4, n)) + 130.0

    # Stationary mean-reverting spread
    spread = [0.0]
    for _ in range(n - 1):
        spread.append(0.85 * spread[-1] + np.random.normal(0, 0.20))
    spread = np.array(spread)

    # Asset Y (KO) cointegrated with PEP
    ko = 0.5 * pep + 5.0 + spread

    return pd.DataFrame({"KO": ko, "PEP": pep}, index=dates)


def test_copula_stat_arb_registry_entry():
    """Verify STRATEGY_REGISTRY registration for copula_stat_arb."""
    assert "copula_stat_arb" in STRATEGY_REGISTRY
    adapter_fn, turnover, universe = STRATEGY_REGISTRY["copula_stat_arb"]
    assert adapter_fn is _build_copula_stat_arb_adapter
    assert turnover == 0.04
    assert universe == ["KO", "PEP"]


def test_copula_stat_arb_adapter_shape_and_returns(synthetic_copula_closes):
    """Verify adapter returns well-formed (X, y, precomputed)."""
    X, y, precomputed = _build_copula_stat_arb_adapter(synthetic_copula_closes)

    assert not X.empty
    assert not y.empty
    assert isinstance(precomputed, dict)
    assert "Copula_StatArb_DynamicHedge" in precomputed

    for col in ["Z_Score", "Spread", "Beta", "Position"]:
        assert col in X.columns, f"Missing feature column: {col}"

    assert len(X) == len(y)
    assert len(X) == len(precomputed["Copula_StatArb_DynamicHedge"])
    assert np.isfinite(y).all()
    assert np.isfinite(precomputed["Copula_StatArb_DynamicHedge"]).all()


def test_copula_stat_arb_adapter_calls_production_entry_point(monkeypatch, synthetic_copula_closes):
    """The adapter must call the real pilots.copula_stat_arb entry point, not a re-implementation."""
    called_with = {}

    import pilots.copula_stat_arb as copula_module

    original = copula_module.generate_copula_stat_arb_signals

    def spy(symbol_y, symbol_x, prices_y, prices_x, *args, **kwargs):
        called_with["symbol_y"] = symbol_y
        called_with["symbol_x"] = symbol_x
        return original(symbol_y, symbol_x, prices_y, prices_x, *args, **kwargs)

    monkeypatch.setattr(copula_module, "generate_copula_stat_arb_signals", spy)

    _build_copula_stat_arb_adapter(synthetic_copula_closes)

    assert called_with.get("symbol_y") == "KO"
    assert called_with.get("symbol_x") == "PEP"


def test_copula_stat_arb_adapter_lookahead_free(synthetic_copula_closes):
    """Perturbing prices strictly after a cutoff must not alter features or returns at/before cutoff."""
    baseline_X, baseline_y, baseline_precomputed = _build_copula_stat_arb_adapter(
        synthetic_copula_closes
    )

    perturbed = synthetic_copula_closes.copy()
    mid = len(perturbed) // 2
    cutoff_date = synthetic_copula_closes.index[mid]

    perturbed.iloc[mid + 1 :, :] = perturbed.iloc[mid + 1 :, :] * 5.0 + 500.0

    perturbed_X, perturbed_y, perturbed_precomputed = _build_copula_stat_arb_adapter(
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
        baseline_precomputed["Copula_StatArb_DynamicHedge"].loc[common_pre],
        perturbed_precomputed["Copula_StatArb_DynamicHedge"].loc[common_pre],
        check_exact=False,
        rtol=1e-5,
    )


def test_copula_stat_arb_adapter_requires_two_assets():
    """A single-column DataFrame must raise, not silently misbehave."""
    n = 100
    dates = pd.date_range(start="2020-01-01", periods=n, freq="B")
    closes = pd.DataFrame({"KO": np.linspace(50, 60, n)}, index=dates)
    with pytest.raises(RuntimeError):
        _build_copula_stat_arb_adapter(closes)


def test_copula_stat_arb_adapter_rejects_series_input():
    """A bare Series (not a DataFrame) must raise a clear error, matching the sibling
    pairs_trading adapter's contract."""
    n = 50
    dates = pd.date_range(start="2020-01-01", periods=n, freq="B")
    series = pd.Series(np.linspace(50, 60, n), index=dates, name="KO")
    with pytest.raises(ValueError):
        _build_copula_stat_arb_adapter(series)


def test_copula_stat_arb_validation_harness_integration(synthetic_copula_closes, tmp_path, monkeypatch):
    """Smoke test StrategyValidationHarness execution with the copula_stat_arb adapter."""
    monkeypatch.setattr(
        harness_module,
        "get_universe_with_survivorship_warning",
        lambda _d: (["KO", "PEP"], {"n_current": 2, "n_at_date": 2, "n_delisted_in_period": 0, "estimated_bias_pct": 0.5}),
    )

    X, y, precomputed = _build_copula_stat_arb_adapter(synthetic_copula_closes)
    strategy_fn = _make_strategy_fn(precomputed, turnover=0.04)

    cost_model = TieredCostModel()
    harness = StrategyValidationHarness(
        strategy_fn=strategy_fn,
        universe_fn=lambda _: ["KO", "PEP"],
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
        strategy_name="copula_stat_arb_unit_test",
    )

    summary = report.to_summary_dict()
    assert isinstance(summary["deployable"], bool)
    assert np.isfinite(summary["sharpe"])
    assert np.isfinite(summary["pbo"])
    assert np.isfinite(summary["dsr"])
    assert np.isfinite(summary["max_drawdown"])
