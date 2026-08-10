"""
InvestYo Quant Platform - VRP Premium Selling (STRATEGY_REGISTRY) Validation Test
===================================================================================
Runs ``scripts.refresh_validations._build_vrp_premium_selling_adapter`` — the
production adapter registered as ``STRATEGY_REGISTRY["vrp_premium_selling"]``
and joined to the ``vrp-premium-selling`` Pilot's ``validation_strategy_id``
— over real historical SPY price data, and verifies the
``StrategyValidationHarness`` produces a well-formed report end-to-end,
including the options-selling stress-gate wiring
(``is_options_selling=True``/``stress_returns_fn``).

Modeled on ``tests/test_validation_lgbm_ranker_registry.py`` (closest
precedent for a network-marked registry integration test), adapted for this
adapter's ``Dict[str, pd.Series]`` (not callable ``strategy_fn``) shape.

Uses a window (2015-2024) long enough for the VRP gate to genuinely open at
least once (verified during development: real gate-open cycles occur
around 2022-04 and 2022-06 in this exact window) — a shorter/more recent
window risks a test that only ever exercises the "gate stayed closed all
period" branch, which is real but far less informative for a smoke test of
the actual leg-construction/mark-to-market code path.
"""

import numpy as np
import pandas as pd
import pytest
import yfinance as yf

from execution.cost_model import TieredCostModel
from scripts.refresh_validations import (
    _build_vrp_premium_selling_adapter,
    _make_strategy_fn,
    _resolve_options_selling_stress_fn,
)
from validation.harness import StrategyValidationHarness

# Downloads real price history live from Yahoo Finance in its module-scoped
# fixture — network-dependent, deselected in CI via `pytest -m "not network"`.
pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def spy_close() -> pd.Series:
    df = yf.download("SPY", start="2015-01-01", end="2024-12-31", progress=False)
    assert df is not None and not df.empty, "Failed to download SPY history"
    close = df["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    close.index = pd.to_datetime(close.index)
    return close.dropna()


def test_adapter_returns_three_items(spy_close):
    X, y, precomputed = _build_vrp_premium_selling_adapter(spy_close)
    assert not X.empty
    assert not y.empty
    assert isinstance(precomputed, dict)
    assert "VRP_IronCondor" in precomputed
    assert X.index.is_unique
    assert y.index.is_unique


def test_adapter_produces_finite_returns(spy_close):
    X, y, precomputed = _build_vrp_premium_selling_adapter(spy_close)
    returns = precomputed["VRP_IronCondor"]
    assert not returns.empty
    assert np.isfinite(returns).all()
    # A real strategy run over 10 years must have at least SOME nonzero
    # trading days -- an all-zero series would mean the gate never opened
    # at all in this window, which is not what was verified during
    # development (see module docstring).
    assert (returns != 0.0).any()


def test_registry_entry_matches_adapter():
    from scripts.refresh_validations import STRATEGY_REGISTRY

    adapter_fn, turnover, universe = STRATEGY_REGISTRY["vrp_premium_selling"]
    assert adapter_fn is _build_vrp_premium_selling_adapter
    assert universe == ["SPY"]
    assert 0.0 < turnover < 1.0


def test_options_selling_stress_fn_resolves_for_this_strategy_only():
    from validation.options_selling_backtest import simulate_vrp_iron_condor_returns

    assert _resolve_options_selling_stress_fn("vrp_premium_selling") is simulate_vrp_iron_condor_returns
    # Every pre-existing entry must be completely unaffected (today's exact
    # is_options_selling=False behavior).
    assert _resolve_options_selling_stress_fn("rsi2_mean_reversion") is None
    assert _resolve_options_selling_stress_fn("garch_vol_target") is None
    assert _resolve_options_selling_stress_fn("lgbm_ranker") is None


def test_vrp_premium_selling_validation_harness_runs(spy_close, tmp_path):
    """Smoke-tests StrategyValidationHarness end-to-end on the production
    adapter, WITH the real is_options_selling/stress_returns_fn wiring —
    asserts a well-formed report (finite numbers, deployable is a bool,
    stress_gate_passed is present) rather than deployability itself. The
    production registry entry's own real measured numbers belong in
    docs/signals/vrp_premium_selling.md, not this test.
    """
    X, y, precomputed = _build_vrp_premium_selling_adapter(spy_close)
    assert not X.empty and not y.empty

    strategy_fn = _make_strategy_fn(precomputed, turnover=0.05)
    stress_fn = _resolve_options_selling_stress_fn("vrp_premium_selling")
    assert stress_fn is not None

    cost_model = TieredCostModel()
    harness = StrategyValidationHarness(
        strategy_fn=strategy_fn,
        universe_fn=lambda _: ["SPY"],
        cost_model=cost_model,
        n_cpcv_splits=6,   # reduced from the production default (10) purely
        n_test_splits=2,   # for test runtime.
        reports_dir=str(tmp_path),
        is_options_selling=True,
        stress_returns_fn=stress_fn,
    )

    report = harness.run(
        start_date=str(X.index[0].date()),
        end_date=str(X.index[-1].date()),
        X=X,
        y=y,
        strategy_name="vrp_premium_selling_test",
    )

    summary = report.to_summary_dict()
    assert isinstance(summary["deployable"], bool)
    assert np.isfinite(summary["sharpe"])
    assert np.isfinite(summary["pbo"])
    assert np.isfinite(summary["dsr"])
    assert np.isfinite(summary["max_drawdown"])
    # The options-selling flag must genuinely reach the report -- a silent
    # False here would mean the stress gate never actually ran despite this
    # test passing is_options_selling=True above.
    assert summary["is_options_selling"] is True
    assert "stress_gate_passed" in summary
