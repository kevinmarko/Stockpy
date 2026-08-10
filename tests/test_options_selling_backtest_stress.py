"""
InvestYo Quant Platform - VRP Options-Selling Backtest x Stress Gate
=======================================================================
Exercises the REAL ``validation.options_selling_backtest.
simulate_vrp_iron_condor_returns`` sliced to each of the four dated
``validation.stress_scenarios.STRESS_SCENARIOS`` windows (OCT_2008,
FEB_2018, MAR_2020, AUG_2024) — real historical SPY data downloaded live for
each window, real leg construction/mark-to-market where the gate opens.

This is distinct from ``tests/test_stress_gate.py``, which tests the GATE
MECHANISM itself (``run_stress_tests``/``passes_stress_gate``) against
synthetic, hand-authored return-generator fixtures — that file's
``_naked_short_put_returns``/``_iron_condor_with_stops_returns`` fixtures
stay exactly as-is; this file complements them with the real strategy this
platform actually ships.

Deliberately does NOT assert ``passes_stress_gate(...)`` is True or False —
that is a genuine, currently-unknown empirical question this test exists to
surface (see ``docs/signals/vrp_premium_selling.md``'s Backtest Validation
section for the actual measured verdict once the ``strategy-validation``
skill's workflow has run), not something to hardcode an expected answer for.
What IS asserted is that the real simulation runs end-to-end for every
dated window without raising, and produces well-formed (finite, no
``error``-unless-genuinely-no-data) ``StressResult``s — the structural
contract ``passes_stress_gate`` depends on to fail closed correctly rather
than silently.
"""

import numpy as np
import pandas as pd
import pytest

from validation.options_selling_backtest import simulate_vrp_iron_condor_returns
from validation.stress_scenarios import (
    STRESS_SCENARIOS,
    run_stress_tests,
    passes_stress_gate,
)

# Downloads real SPY price history live from Yahoo Finance for each dated
# shock window — network-dependent, deselected in CI via `pytest -m "not
# network"`.
pytestmark = pytest.mark.network


@pytest.mark.parametrize("scenario_name", list(STRESS_SCENARIOS.keys()))
def test_scenario_window_produces_well_formed_returns(scenario_name):
    scenario = STRESS_SCENARIOS[scenario_name]
    returns = simulate_vrp_iron_condor_returns(scenario.start, scenario.end, ticker="SPY")
    # Never raises regardless of gate state (CONSTRAINT #6); a genuinely
    # gate-closed-throughout window degrades to an all-zero series, which is
    # itself well-formed (finite, real index), not empty/NaN.
    assert isinstance(returns, pd.Series)
    if not returns.empty:
        assert np.isfinite(returns).all()
        assert returns.index.is_monotonic_increasing


def test_full_stress_gate_runs_end_to_end_against_the_real_strategy():
    """The actual, real stress-gate evaluation this platform would run
    before ever marking `vrp_premium_selling` deployable. No result is
    hardcoded -- this test's job is to prove the pipeline produces a
    genuine, well-formed verdict (never a crash, never a fabricated
    number), whatever that verdict honestly turns out to be.
    """
    results = run_stress_tests(
        lambda start, end: simulate_vrp_iron_condor_returns(start, end, ticker="SPY")
    )
    assert set(results.keys()) == set(STRESS_SCENARIOS.keys())

    for name, result in results.items():
        # Every window has REAL SPY data available (all four dated windows
        # postdate SPY's 1993 inception), so none should hit the
        # no-data-in-window error path.
        assert result.error is None, f"{name}: unexpected data-gap error: {result.error}"
        assert np.isfinite(result.max_drawdown)
        assert isinstance(result.survived, bool)

    gate_result = passes_stress_gate(results)
    assert isinstance(gate_result, bool)
