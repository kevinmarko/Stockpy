"""
InvestYo Quant Platform - Signal Module Lookahead Perturbation Tests
=====================================================================
Extends tests/lookahead_check.py's verify_no_lookahead() perturbation
harness to signal modules that previously had no dedicated no-lookahead
coverage: GrahamValueSignal and DividendQualitySignal. Both are purely
row-wise (each row's score depends only on that row's own
current_price/graham_number or dividend_yield/is_dividend_sustainable
values, with no rolling window or cross-row state), so these tests confirm
that structural property mathematically rather than by inspection.

Deliberately NOT covered here (see each signal's own reasoning):

  - RegimeMultiplierSignal: compute() never reads its `row`/df argument at
    all -- its output depends solely on context.macro.hmm_risk_on_probability
    (see signals/regime_multiplier.py's module docstring). A per-row
    perturbation test is structurally inapplicable to a signal with no row
    dependency; its real contract (score always 0.0, confidence carries the
    HMM probability, neutral 1.0 default when unavailable) is already
    covered by tests/test_regime_multiplier.py using real SignalContext/DTO
    construction. An earlier version of this file called
    signal.compute_vectorized(data, None) with context=None wrapped in a
    broad `except Exception: return 0.0` -- context=None makes
    context.macro raise AttributeError unconditionally, so both the
    original and perturbed runs silently returned the same 0.0 sentinel and
    the assertion passed without ever exercising real signal logic.

  - CrossSectionalMomentumSignal: its own no-lookahead guarantee lives in
    main_orchestrator.compute_xsec_momentum_ranks()'s shift(22)/shift(252)
    computation (see signals/cross_sectional_momentum.py's module
    docstring), already covered by
    tests/test_xsec_momentum.py::test_no_lookahead_12m_skips_recent_month.
    compute_vectorized() has no override here either (it falls back to the
    base class's per-row `compute()`, which reads only
    context.xsec_percentile_ranks -- pre-computed once per cycle by
    pre_compute(), not touched by a per-row perturbation at all) and hits
    the identical context=None AttributeError-swallowed-to-0.0 problem
    described above.
"""

import numpy as np
import pandas as pd

from signals.dividend_quality import DividendQualitySignal
from signals.graham_value import GrahamValueSignal
from tests.lookahead_check import verify_no_lookahead


def test_graham_value_lookahead():
    dates = pd.date_range("2026-01-01", periods=100)
    df = pd.DataFrame({
        "current_price": np.random.uniform(50, 150, 100),
        "graham_number": np.random.uniform(50, 150, 100)
    }, index=dates)

    signal = GrahamValueSignal()

    def func(data, t):
        out = signal.compute_vectorized(data, None)
        return out["score"].iloc[t]

    assert verify_no_lookahead(func, df, 50)


def test_dividend_quality_lookahead():
    dates = pd.date_range("2026-01-01", periods=100)
    df = pd.DataFrame({
        "dividend_yield": np.random.uniform(0.01, 0.05, 100),
        "payout_ratio": np.random.uniform(0.2, 0.8, 100)
    }, index=dates)

    signal = DividendQualitySignal()

    def func(data, t):
        out = signal.compute_vectorized(data, None)
        return out["score"].iloc[t]

    assert verify_no_lookahead(func, df, 50)
