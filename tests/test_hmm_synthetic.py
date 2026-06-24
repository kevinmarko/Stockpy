"""
InvestYo Quant Platform - HMM Regime Detector Recovery Test
================================================================
Generates synthetic data from a KNOWN 2-state Gaussian HMM (using hmmlearn's
own .sample(), so the ground truth is exact) and verifies HMMRegimeDetector
recovers the hidden states with >80% accuracy after resolving the
label-permutation ambiguity inherent to unsupervised HMM fitting (a freshly
fit model's internal state indices 0/1 need not match the generator's).
"""

import numpy as np
import pandas as pd
import pytest
from hmmlearn.hmm import GaussianHMM

from regime.hmm_regime import HMMRegimeDetector


def _generate_known_2state_hmm_data(n_samples: int = 600, seed: int = 21):
    """Builds a true 2-state GaussianHMM with well-separated, persistent
    states and draws (features, true_states) from it via .sample()."""
    true_model = GaussianHMM(n_components=2, covariance_type="diag", random_state=seed)
    true_model.startprob_ = np.array([0.5, 0.5])
    # Sticky transition matrix -- persistent regimes, not i.i.d. switching.
    true_model.transmat_ = np.array([
        [0.97, 0.03],
        [0.04, 0.96],
    ])
    # State 0: calm/bull-like (low mean/vol features). State 1: turbulent/bear-like.
    true_model.means_ = np.array([
        [0.0008, 0.10, 13.0, 0.5],
        [-0.0010, 0.35, 28.0, 0.3],
    ])
    true_model.covars_ = np.array([
        [1e-5, 0.0004, 4.0, 0.04],
        [3e-4, 0.0025, 25.0, 0.04],
    ])

    X, true_states = true_model.sample(n_samples, random_state=seed)
    dates = pd.bdate_range(end=pd.Timestamp("2024-01-01"), periods=n_samples)
    features_df = pd.DataFrame(
        X, index=dates, columns=["spy_return", "realized_vol_20d", "vix_level", "yield_curve_spread"]
    )
    return features_df, true_states


def _best_permutation_accuracy(predicted: np.ndarray, true: np.ndarray, n_states: int) -> float:
    """Resolves label-permutation ambiguity by trying all permutations of
    predicted-state -> true-state mappings and returning the best accuracy.
    Feasible here since n_states is small (2 or 3)."""
    from itertools import permutations

    best_acc = 0.0
    for perm in permutations(range(n_states)):
        remapped = np.array([perm[p] for p in predicted])
        acc = float(np.mean(remapped == true))
        best_acc = max(best_acc, acc)
    return best_acc


def test_recovers_known_2state_hmm_with_high_accuracy():
    features_df, true_states = _generate_known_2state_hmm_data(n_samples=600)

    detector = HMMRegimeDetector(n_states=2, retrain_freq_days=10_000, random_state=7)
    detector.fit(features_df)

    # Recover the dominant state at EVERY bar using the same fitted model --
    # since predict_proba returns only the last row, loop bar-by-bar over the
    # already-fit detector (no refitting -- this test is about recovery
    # accuracy of a fixed fit, not about the refit cadence).
    predicted_states = np.array([
        detector.predict_proba(features_df.iloc[:i + 1])["dominant_state"]
        for i in range(len(features_df))
    ])

    accuracy = _best_permutation_accuracy(predicted_states, true_states, n_states=2)
    assert accuracy > 0.80, f"State recovery accuracy {accuracy:.1%} did not exceed 80%."


def test_identify_states_by_vol_labels_lower_variance_state_as_bull():
    """For the known generator, state 0 (calm) has lower variance across all
    features than state 1 (turbulent) -- identify_states_by_vol() must label
    accordingly (lowest fitted variance -> 'bull' for an n_states=2 fit, since
    DEFAULT_STATE_LABELS_3 is sliced to ['bull', 'sideways'] for n_states=2)."""
    features_df, true_states = _generate_known_2state_hmm_data(n_samples=600)

    detector = HMMRegimeDetector(n_states=2, retrain_freq_days=10_000, random_state=7)
    detector.fit(features_df)

    labels = detector.state_labels
    assert set(labels.values()) == {"bull", "sideways"}

    # The state hmmlearn assigns the lowest summed diagonal variance to should
    # be labeled 'bull' -- verify this directly against the fitted covars_.
    variances = np.asarray(detector.model.covars_).reshape(detector.n_states, -1).sum(axis=1)
    lowest_var_state = int(np.argmin(variances))
    assert labels[lowest_var_state] == "bull"


def test_risk_on_probability_higher_in_calm_regime_window():
    """A window drawn entirely from the calm/bull-like generating state
    should produce a materially higher average risk_on_probability than a
    window drawn entirely from the turbulent state."""
    true_model = GaussianHMM(n_components=2, covariance_type="diag", random_state=99)
    true_model.startprob_ = np.array([1.0, 0.0])
    true_model.transmat_ = np.array([[0.995, 0.005], [0.005, 0.995]])
    true_model.means_ = np.array([
        [0.0008, 0.10, 13.0, 0.5],
        [-0.0010, 0.35, 28.0, 0.3],
    ])
    true_model.covars_ = np.array([
        [1e-5, 0.0004, 4.0, 0.04],
        [3e-4, 0.0025, 25.0, 0.04],
    ])

    n = 300
    calm_X, _ = true_model.sample(n, random_state=1)
    dates_calm = pd.bdate_range(end=pd.Timestamp("2024-01-01"), periods=n)
    calm_df = pd.DataFrame(calm_X, index=dates_calm,
                            columns=["spy_return", "realized_vol_20d", "vix_level", "yield_curve_spread"])

    means_array = np.array([
        [0.0008, 0.10, 13.0, 0.5],
        [-0.0010, 0.35, 28.0, 0.3],
    ])
    covars_array = np.array([
        [1e-5, 0.0004, 4.0, 0.04],
        [3e-4, 0.0025, 25.0, 0.04],
    ])
    turbulent_model = GaussianHMM(n_components=2, covariance_type="diag", random_state=99)
    turbulent_model.startprob_ = np.array([0.0, 1.0])
    turbulent_model.transmat_ = np.array([[0.995, 0.005], [0.005, 0.995]])
    turbulent_model.means_ = means_array
    turbulent_model.covars_ = covars_array
    turbulent_X, _ = turbulent_model.sample(n, random_state=2)
    turbulent_df = pd.DataFrame(turbulent_X, index=dates_calm,
                                 columns=["spy_return", "realized_vol_20d", "vix_level", "yield_curve_spread"])

    detector = HMMRegimeDetector(n_states=2, retrain_freq_days=10_000, random_state=7)
    detector.fit(pd.concat([calm_df, turbulent_df]))

    calm_risk_on = detector.predict_proba(calm_df)["risk_on_probability"]
    turbulent_risk_on = detector.predict_proba(turbulent_df)["risk_on_probability"]

    assert calm_risk_on > turbulent_risk_on
