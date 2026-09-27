"""
tests/test_garch_extraction_equivalence.py
==========================================
Step 3d moved GJR-GARCH (``volatility/garch.py``) and the Aroon/Coppock/
Chandelier indicators (``trend_indicators.py``) out of
``technical_options_engine`` so the equity path stops depending on the
options desk. ``GARCH_Vol`` drives cold-start Kelly sizing on both the
pipeline and advisory paths, so the move had to be numerically invisible.

The golden values below were captured by running the PRE-MOVE
``TechnicalOptionsEngine`` methods on these exact synthetic series (commit
d77db873). The moved code must keep reproducing them. GARCH values use a
1e-4 relative tolerance: the SLSQP maximum-likelihood fit lands on slightly
different bits per platform/BLAS build (CI's Linux runners measured ~8e-6
relative off the macOS goldens), while a real regression -- a different
model spec, return scaling or bounds -- moves the result by orders of
magnitude more. TestDelegatesMatchCore in tests/test_volatility_garch.py
keeps the same-process comparison exact. The indicator values are plain
rolling arithmetic and use 1e-9.

The full before/after pipeline comparison (one cycle on frozen inputs,
``state_snapshot``-level columns plus advisory recommendations) was run
once for the PR; this file keeps the load-bearing numeric half under test.
"""

import numpy as np
import pandas as pd
import pytest

from trend_indicators import calculate_trend_exit_indicators
from volatility.garch import GarchVolatilityEstimator

_GOLDEN = {
    (11, 400): {
        "vol": 0.23485573502918491,
        "ts": {1: 0.23485573502918491, 10: 0.24597994401100348, 30: 0.2571464679562099,
               60: 0.262656171403904, 90: 0.2647031719483651},
        "ind": {"Aroon_Oscillator": 71.42857142857143, "Chandelier_Long": 89.62867790874184,
                "Chandelier_Short": 94.38112752486967, "Coppock_Curve": 4.217081462290717},
    },
    (12, 120): {
        "vol": 0.30172040044972237,
        "ts": {1: 0.30172040044972237, 10: 0.3430272248775601, 30: 0.36985710663891136,
               60: 0.3790915220029559, 90: 0.38218330851660354},
        "ind": {"Aroon_Oscillator": 49.999999999999986, "Chandelier_Long": 82.83301813490324,
                "Chandelier_Short": 87.68619158635396, "Coppock_Curve": 6.929089510699807},
    },
    (13, 30): {
        "vol": 0.7789747572395497,
        "ts": {1: 0.7789747572395498, 10: 0.8474736236062079, 30: 0.9827456599983603,
               60: 1.156354299750584, 90: 1.3071042425367994},
        "ind": {"Aroon_Oscillator": 50.0, "Chandelier_Long": 118.88604636953711,
                "Chandelier_Short": 117.09795804000863, "Coppock_Curve": 20.372889762352077},
    },
}


def _series(seed: int, n: int) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    r = rng.normal(0.0003, 0.015, n)
    r[n // 2:n // 2 + 20] *= 3.0
    c = 100.0 * np.exp(np.cumsum(r))
    idx = pd.date_range("2022-01-03", periods=n, freq="B")
    return pd.DataFrame(
        {"Open": c * 0.999, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": np.full(n, 1e6)},
        index=idx,
    )


@pytest.mark.parametrize("key", sorted(_GOLDEN))
def test_garch_day_ahead_vol_matches_pre_move_golden(key):
    got = GarchVolatilityEstimator().estimate_gjr_garch_volatility(_series(*key))
    assert got == pytest.approx(_GOLDEN[key]["vol"], rel=1e-4)


@pytest.mark.parametrize("key", sorted(_GOLDEN))
def test_garch_term_structure_matches_pre_move_golden(key):
    got = GarchVolatilityEstimator().estimate_gjr_garch_volatility_term_structure(
        _series(*key), horizons=(1, 10, 30, 60, 90)
    )
    assert set(got) == set(_GOLDEN[key]["ts"])
    for h, expected in _GOLDEN[key]["ts"].items():
        assert got[h] == pytest.approx(expected, rel=1e-4), f"horizon {h}"


@pytest.mark.parametrize("key", sorted(_GOLDEN))
def test_trend_exit_indicators_match_pre_move_golden(key):
    got = calculate_trend_exit_indicators(_series(*key))
    assert set(got) == set(_GOLDEN[key]["ind"])
    for name, expected in _GOLDEN[key]["ind"].items():
        assert got[name] == pytest.approx(expected, rel=1e-9), name


def test_equity_path_modules_do_not_import_the_options_engine():
    """The point of the move: the pipeline, advisory, forecasting and
    strategy engines no longer import technical_options_engine or iv_engine."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for rel in ("main_orchestrator.py", "main.py", "engine/advisory.py", "forecasting_engine.py",
                "strategy_engine.py", "pipeline/production_steps.py", "desktop/daemon_runtime.py",
                "volatility/garch.py", "trend_indicators.py"):
        src = (root / rel).read_text(encoding="utf-8")
        for banned in ("import technical_options_engine", "from technical_options_engine",
                       "from volatility.iv_engine", "import volatility.iv_engine"):
            assert banned not in src, f"{rel} still has {banned!r}"
