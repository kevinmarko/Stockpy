"""
InvestYo Quant Platform - HMM Regime Detector State Persistence Test
========================================================================
With a fixed (non-refitting) model and a low retrain frequency that keeps it
that way across the test window, the dominant-state classification should be
fairly persistent day-to-day -- Hamilton (1989) regime-switching models are
typically fit with a diagonal-dominant transition matrix (states are
"sticky"), so a well-behaved fit should not flicker between states on
consecutive bars more than a small fraction of the time.
"""

import numpy as np
import pandas as pd

from regime.hmm_regime import HMMRegimeDetector


def _regime_switching_features(n: int = 400, seed: int = 11) -> pd.DataFrame:
    """Synthetic data with genuine, persistent regime structure: long runs of
    a 'calm' state and a 'turbulent' state (not i.i.d. noise), so a fitted
    HMM has real persistence to recover -- a pure-noise series would make
    this test meaningless regardless of the detector's own stability.
    """
    rng = np.random.RandomState(seed)
    states = np.zeros(n, dtype=int)
    current = 0
    i = 0
    while i < n:
        run_length = rng.randint(20, 60)  # long, sticky runs
        states[i:i + run_length] = current
        current = 1 - current
        i += run_length

    spy_return = np.where(states == 0, rng.normal(0.0008, 0.006, n), rng.normal(-0.0005, 0.022, n))
    realized_vol_20d = np.where(states == 0, np.abs(rng.normal(0.10, 0.02, n)), np.abs(rng.normal(0.35, 0.05, n)))
    vix_level = np.where(states == 0, np.abs(rng.normal(13.0, 2.0, n)), np.abs(rng.normal(28.0, 5.0, n)))
    yield_curve_spread = rng.normal(0.5, 0.2, n)

    dates = pd.bdate_range(end=pd.Timestamp("2024-06-01"), periods=n)
    return pd.DataFrame({
        "spy_return": spy_return,
        "realized_vol_20d": realized_vol_20d,
        "vix_level": vix_level,
        "yield_curve_spread": yield_curve_spread,
    }, index=dates)


def test_dominant_state_does_not_flicker_excessively():
    """With retrain_freq_days large enough that the model does not refit
    across the evaluation window, day-over-day dominant_state changes
    must occur less than 15% of the time."""
    features = _regime_switching_features(n=400)

    fit_cutoff_idx = 200
    detector = HMMRegimeDetector(n_states=3, retrain_freq_days=10_000, random_state=3)
    detector.fit(features.iloc[:fit_cutoff_idx])

    dominant_states = []
    for i in range(fit_cutoff_idx, len(features)):
        result = detector.predict_proba(features.iloc[:i + 1])
        dominant_states.append(result["dominant_state"])

    dominant_states = np.array(dominant_states)
    flips = np.sum(dominant_states[1:] != dominant_states[:-1])
    flip_rate = flips / (len(dominant_states) - 1)

    assert flip_rate < 0.15, (
        f"Dominant state flickered on {flip_rate:.1%} of consecutive bars "
        f"(expected < 15% for a persistent regime-switching fit)."
    )


def test_state_labels_are_stable_across_predict_calls_without_refit():
    """identify_states_by_vol()'s labeling must not change between
    predict_proba() calls when the model itself hasn't refit."""
    features = _regime_switching_features(n=300)
    detector = HMMRegimeDetector(n_states=3, retrain_freq_days=10_000, random_state=3)
    detector.fit(features.iloc[:150])

    labels_before = dict(detector.state_labels)
    for i in range(150, 200):
        detector.predict_proba(features.iloc[:i + 1])
    labels_after = dict(detector.state_labels)

    assert labels_before == labels_after
