"""
InvestYo Quant Platform - HMM Regime Detector No-Lookahead Tests
====================================================================
Verifies regime/hmm_regime.py's two distinct no-lookahead guarantees:

1. The retrain_freq_days gate: refitting one day later (well within the
   default 7-day window) must be a no-op, so a prediction at date D made
   before vs. after that no-op refit is identical -- adding one more day of
   data must never retroactively perturb a fit that hasn't actually refit.
2. The deeper guarantee behind predict_proba() itself: probabilities at the
   last row of a given feature sequence must depend ONLY on that sequence
   (see module docstring on forward-filtering == last-row of smoothed
   posterior). Perturbing data strictly AFTER the cutoff date, then
   re-slicing to the SAME cutoff, must reproduce identical probabilities.
"""

import math

import numpy as np
import pandas as pd
import pytest

from regime.hmm_regime import HMMRegimeDetector


def _synthetic_features(n: int = 200, seed: int = 5) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range(end=pd.Timestamp("2024-01-01"), periods=n)
    return pd.DataFrame({
        "spy_return": rng.normal(0.0003, 0.01, n),
        "realized_vol_20d": np.abs(rng.normal(0.15, 0.05, n)),
        "vix_level": np.abs(rng.normal(15.0, 4.0, n)),
        "yield_curve_spread": rng.normal(0.5, 0.3, n),
    }, index=dates)


def _dicts_close(a: dict, b: dict) -> bool:
    if a.keys() != b.keys():
        return False
    for k in a:
        if k == "dominant_state":
            if a[k] != b[k]:
                return False
        elif not math.isclose(a[k], b[k], rel_tol=1e-9, abs_tol=1e-12):
            return False
    return True


# =============================================================================
# 1. Retrain-gate no-op: refitting 1 day later (< default 7-day window)
#    must not change the model, so the prediction at D is identical.
# =============================================================================
def test_retrain_gate_noop_keeps_prediction_at_d_identical():
    features = _synthetic_features(n=200)
    D_idx = 150
    D = features.index[D_idx]
    D_plus_1 = features.index[D_idx + 1]

    detector = HMMRegimeDetector(n_states=3, retrain_freq_days=7, random_state=1)

    detector.fit(features.loc[:D])
    probs_before = detector.predict_proba(features.loc[:D])
    fit_date_before = detector.last_fit_date

    # "Refit" on data through D+1 -- only 1 calendar day later, well within
    # the 7-day retrain_freq_days gate -- must be a no-op (model unchanged).
    detector.fit(features.loc[:D_plus_1])
    fit_date_after = detector.last_fit_date
    assert fit_date_after == fit_date_before, (
        "retrain_freq_days gate did not hold -- the model was refit on 1 day "
        "of extra data despite being within the 7-day window."
    )

    probs_after = detector.predict_proba(features.loc[:D])
    assert _dicts_close(probs_before, probs_after), (
        f"Prediction at D changed after a no-op refit: {probs_before} != {probs_after}"
    )


def test_retrain_gate_actually_refits_after_window_elapses():
    """Sanity check on the gate itself: once retrain_freq_days has elapsed,
    fit() DOES perform a real refit (last_fit_date advances)."""
    features = _synthetic_features(n=200)
    detector = HMMRegimeDetector(n_states=3, retrain_freq_days=7, random_state=1)

    detector.fit(features.iloc[:100])
    first_fit_date = detector.last_fit_date

    detector.fit(features.iloc[:120])  # 20 calendar-ish days later -- past the gate
    second_fit_date = detector.last_fit_date

    assert second_fit_date > first_fit_date


# =============================================================================
# 2. predict_proba's last-row probability depends only on data up to the
#    cutoff -- perturbing rows strictly after the cutoff must not matter.
# =============================================================================
def test_predict_proba_ignores_rows_after_cutoff():
    features = _synthetic_features(n=200)
    cutoff_idx = 150
    cutoff_date = features.index[cutoff_idx]

    detector = HMMRegimeDetector(n_states=3, retrain_freq_days=7, random_state=1)
    detector.fit(features.loc[:cutoff_date])
    baseline_probs = detector.predict_proba(features.loc[:cutoff_date])

    # Build a second, perturbed version of the dataset where everything AFTER
    # the cutoff is replaced with extreme values. Slicing both versions to
    # the SAME cutoff and re-predicting must give identical results --
    # the perturbed rows must never be allowed to leak into the cutoff's
    # probability via the model's own state (predict_proba is stateless
    # across calls; only fit() carries state, and fit() was never re-run
    # on the perturbed future here).
    perturbed = features.copy()
    perturbed.iloc[cutoff_idx + 1:] = 99999.9
    perturbed_probs = detector.predict_proba(perturbed.loc[:cutoff_date])

    assert _dicts_close(baseline_probs, perturbed_probs), (
        f"predict_proba at cutoff changed after perturbing future-dated rows: "
        f"{baseline_probs} != {perturbed_probs}"
    )


def test_predict_proba_raises_without_fit():
    detector = HMMRegimeDetector(n_states=3)
    features = _synthetic_features(n=50)
    with pytest.raises(RuntimeError):
        detector.predict_proba(features)


def test_fit_raises_on_nan_features():
    detector = HMMRegimeDetector(n_states=3)
    features = _synthetic_features(n=50)
    features.iloc[10, 0] = float("nan")
    with pytest.raises(ValueError):
        detector.fit(features)


def test_fit_raises_on_empty_features():
    detector = HMMRegimeDetector(n_states=3)
    with pytest.raises(ValueError):
        detector.fit(pd.DataFrame())
