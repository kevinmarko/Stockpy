"""
InvestYo Quant Platform - Stress Deployability Gate Tests
===========================================================
Verifies the options-selling stress gate (validation/stress_scenarios.py +
ValidationReport.deployable):

- A NAKED SHORT PUT with no risk management blows up / draws down >50% in the
  shock windows -> FAILS the stress gate -> not deployable.
- An IRON CONDOR with hard stops caps its loss in every window -> PASSES the
  stress gate (and is documented to do so by construction here).
- A non-options strategy is unaffected by the stress gate entirely.
- An options-selling strategy with no stress data fails closed.
"""

import numpy as np
import pandas as pd
import pytest

from validation.stress_scenarios import (
    STRESS_SCENARIOS,
    run_stress_tests,
    passes_stress_gate,
    MAX_STRESS_DRAWDOWN,
)
from validation.harness import ValidationReport


# A set of "passing" non-stress metrics so deployability hinges only on the
# stress gate in these tests.
_GOOD_METRICS = dict(
    start_date="2008-01-01", end_date="2024-12-31",
    sharpe=1.5, sortino=2.0, calmar=1.0, max_dd=0.10, turnover=0.05,
    hit_rate=0.8, avg_trade_pct=0.001, dsr=0.99, pbo=0.10,
    bias_report={}, walk_forward_60_40=1.0, walk_forward_70_30=1.0,
    walk_forward_80_20=1.0, distribution=np.array([1.0, 1.1, 0.9]),
    paths=[], n_trials=1,
)


def _naked_short_put_returns(start: str, end: str) -> pd.Series:
    """Naked short put, no risk management: collects small premium daily, but in
    a shock window takes a single catastrophic loss that either blows up the
    account or far exceeds the 50% drawdown bar. Models the negatively-skewed
    'pennies in front of a steamroller' payoff with no protective long leg."""
    idx = pd.bdate_range(start=start, end=end)
    n = len(idx)
    rets = np.full(n, 0.002)  # +0.2%/day premium harvest
    if n >= 2:
        # Mid-window gap-down that an unhedged short put suffers in full.
        # -95% in the worst window: survives (barely) but obliterates the gate.
        rets[n // 2] = -0.95
    return pd.Series(rets, index=idx)


def _naked_short_put_blowup_returns(start: str, end: str) -> pd.Series:
    """An even more extreme unhedged book: a >100% single-day loss (e.g. short
    puts on a name that gaps down through the strike by more than the premium
    and margin) -> account blow-up -> must fail on survival, not just drawdown."""
    idx = pd.bdate_range(start=start, end=end)
    n = len(idx)
    rets = np.full(n, 0.002)
    if n >= 2:
        rets[n // 2] = -1.20  # equity goes negative: blow-up
    return pd.Series(rets, index=idx)


def _iron_condor_with_stops_returns(start: str, end: str) -> pd.Series:
    """Defined-risk iron condor with hard stops: the long wings + a stop-loss
    cap the worst-case loss. Even in a shock window the drawdown stays well
    under the 50% bar and the account always survives."""
    idx = pd.bdate_range(start=start, end=end)
    n = len(idx)
    rets = np.full(n, 0.0015)  # smaller premium than naked (paid for the wings)
    if n >= 2:
        # Stop-loss caps the shock-day loss at a defined, survivable level.
        rets[n // 2] = -0.12
    return pd.Series(rets, index=idx)


# =============================================================================
# Naked short put FAILS
# =============================================================================
def test_naked_short_put_fails_stress_gate():
    results = run_stress_tests(_naked_short_put_returns)
    assert passes_stress_gate(results) is False
    # At least one window must exceed the drawdown bar.
    assert any(r.max_drawdown >= MAX_STRESS_DRAWDOWN for r in results.values())


def test_naked_short_put_not_deployable_despite_good_metrics():
    results = run_stress_tests(_naked_short_put_returns)
    report = ValidationReport(
        name="NakedShortPut", is_options_selling=True,
        stress_test_results=results, **_GOOD_METRICS,
    )
    assert report.stress_gate_passed is False
    assert report.deployable is False  # blocked purely by the stress gate


def test_naked_short_put_blowup_fails_on_survival():
    results = run_stress_tests(_naked_short_put_blowup_returns)
    # The blow-up window must be flagged as non-survival.
    assert any(r.survived is False for r in results.values())
    assert passes_stress_gate(results) is False


# =============================================================================
# Iron condor with stops PASSES
# =============================================================================
def test_iron_condor_with_stops_passes_stress_gate():
    results = run_stress_tests(_iron_condor_with_stops_returns)
    # Documented numbers: each window's worst loss is the -12% stop, so max DD
    # is ~12% << 50% and the account always survives.
    for name, r in results.items():
        assert r.survived is True, f"{name} unexpectedly did not survive"
        assert r.max_drawdown < MAX_STRESS_DRAWDOWN, f"{name} DD {r.max_drawdown:.2%} exceeded bar"
    assert passes_stress_gate(results) is True


def test_iron_condor_deployable_when_metrics_also_pass():
    results = run_stress_tests(_iron_condor_with_stops_returns)
    report = ValidationReport(
        name="IronCondor", is_options_selling=True,
        stress_test_results=results, **_GOOD_METRICS,
    )
    assert report.stress_gate_passed is True
    assert report.deployable is True


# =============================================================================
# Gate applicability / fail-closed semantics
# =============================================================================
def test_non_options_strategy_ignores_stress_gate():
    report = ValidationReport(
        name="EquityMomentum", is_options_selling=False,
        stress_test_results=None, **_GOOD_METRICS,
    )
    assert report.stress_gate_passed is True  # gate does not apply
    assert report.deployable is True


def test_options_selling_with_no_stress_data_fails_closed():
    report = ValidationReport(
        name="UntestedOptionsSeller", is_options_selling=True,
        stress_test_results=None, **_GOOD_METRICS,
    )
    assert report.stress_gate_passed is False
    assert report.deployable is False


def test_partial_scenario_coverage_fails_closed():
    """Missing even one canonical window must fail the gate (fail-closed)."""
    full = run_stress_tests(_iron_condor_with_stops_returns)
    partial = {k: full[k] for k in list(full.keys())[:2]}  # drop two windows
    assert passes_stress_gate(partial) is False
