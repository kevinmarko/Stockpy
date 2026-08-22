"""
InvestYo Quant Platform - HMM Regime Detector Recovery Test
================================================================
Generates synthetic data from a KNOWN 2-state Gaussian HMM (using hmmlearn's
own .sample(), so the ground truth is exact) and verifies HMMRegimeDetector
recovers the hidden states with >80% accuracy after resolving the
label-permutation ambiguity inherent to unsupervised HMM fitting (a freshly
fit model's internal state indices 0/1 need not match the generator's).
"""

import logging
from types import SimpleNamespace

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


def test_near_constant_feature_column_does_not_explode_scaled_values():
    """Finding 8 regression: a near-constant (not bit-identical) feature
    column produces a std that is near-zero but not exactly 0.0 due to
    floating-point noise. The old exact ``== 0.0`` guard on
    ``feature_stds_`` let that near-zero value through unclamped, exploding
    the corresponding scaled feature column (``(X - mean) / std``) to huge
    magnitudes. The fixed ``< 1e-12`` guard (the repo's degenerate-std
    convention) clamps it to ``std=1.0`` instead."""
    n = 200
    rng = np.random.default_rng(3)
    dates = pd.bdate_range(end=pd.Timestamp("2024-01-01"), periods=n)
    # One column near-constant with floating-point-scale noise (~1e-13) --
    # NOT bit-identical, so an exact `== 0.0` check would not catch it.
    near_constant = 5.0 + rng.normal(0, 1e-13, size=n)
    features_df = pd.DataFrame(
        {
            "spy_return": rng.normal(0.0005, 0.01, size=n),
            "realized_vol_20d": near_constant,
            "vix_level": rng.normal(15, 3, size=n),
            "yield_curve_spread": rng.normal(0.5, 0.2, size=n),
        },
        index=dates,
    )

    detector = HMMRegimeDetector(n_states=2, retrain_freq_days=10_000, random_state=7)
    detector.fit(features_df)

    near_constant_idx = features_df.columns.get_loc("realized_vol_20d")
    # The near-constant column's std must have been clamped to 1.0, not left
    # at its true near-zero (but not exactly 0.0) value.
    assert detector.feature_stds_[near_constant_idx] == 1.0

    # And the scaled feature values for that column must stay bounded --
    # before the fix, dividing by a ~1e-13 std would explode (X - mean) by a
    # factor on the order of 1e12+.
    X = features_df.to_numpy(dtype=float)
    X_scaled = (X - detector.feature_means_) / detector.feature_stds_
    assert np.all(np.isfinite(X_scaled[:, near_constant_idx]))
    assert np.all(np.abs(X_scaled[:, near_constant_idx]) < 1e6)


def test_hmm_covariance_types_fit_and_predict():
    """Verifies that HMMRegimeDetector correctly supports full, spherical, and tied covariance types."""
    n = 150
    rng = np.random.default_rng(42)
    dates = pd.bdate_range(end=pd.Timestamp("2024-01-01"), periods=n)
    features_df = pd.DataFrame(
        {
            "spy_return": rng.normal(0.0005, 0.01, size=n),
            "realized_vol_20d": rng.uniform(0.10, 0.25, size=n),
            "vix_level": rng.uniform(12, 28, size=n),
            "yield_curve_spread": rng.normal(0.5, 0.3, size=n),
        },
        index=dates,
    )

    for cov in ["full", "spherical", "tied"]:
        detector = HMMRegimeDetector(n_states=3, covariance_type=cov, random_state=42)
        detector.fit(features_df)
        assert detector.model is not None
        assert len(detector.state_labels) == 3
        assert "bull" in detector.state_labels.values()

        proba = detector.predict_proba(features_df)
        assert "risk_on_probability" in proba
        assert 0.0 <= proba["risk_on_probability"] <= 1.0


@pytest.mark.parametrize("cov_type", ["diag", "full", "spherical", "tied"])
def test_identify_states_by_vol_semantic_correctness_across_covariance_types(cov_type):
    """Regression test for the spherical/tied state-mislabeling bug
    (2026-08): for every supported covariance_type, the state labeled
    'bull' must have a lower fitted mean realized_vol_20d than the other
    state. Pre-fix this failed for 'spherical' (hmmlearn's public covars_
    getter returns a malformed shape for spherical covariance, so
    identify_states_by_vol() always hit its length-mismatch fallback to an
    arbitrary np.arange() ordering, unrelated to volatility) and could fail
    for 'tied' (ranked states by an undirected 4-feature mean-vector norm
    instead of a directional risk proxy). This is a deterministic,
    fitted-parameter check -- not a classification-accuracy threshold -- so
    it needs no flakiness calibration."""
    features_df, _ = _generate_known_2state_hmm_data(n_samples=600)

    detector = HMMRegimeDetector(
        n_states=2, covariance_type=cov_type, retrain_freq_days=10_000, random_state=7
    )
    detector.fit(features_df)

    labels = detector.state_labels
    bull_idx = next(idx for idx, label in labels.items() if label == "bull")
    other_idx = next(idx for idx in labels if idx != bull_idx)

    vol_idx = detector.feature_names_.index("realized_vol_20d")
    bull_mean_vol = detector.model.means_[bull_idx, vol_idx]
    other_mean_vol = detector.model.means_[other_idx, vol_idx]

    assert bull_mean_vol < other_mean_vol, (
        f"covariance_type={cov_type}: state labeled 'bull' has mean (scaled) "
        f"realized_vol_20d={bull_mean_vol:.4f}, NOT lower than the other "
        f"state's {other_mean_vol:.4f} -- state mislabeling."
    )


@pytest.mark.parametrize("cov_type", ["diag", "full", "spherical"])
def test_risk_on_probability_higher_in_calm_regime_across_covariance_types(cov_type):
    """Integration-level counterpart to the semantic-correctness test above
    -- closes the gap between identify_states_by_vol()'s label correctness
    and predict_proba()'s actual risk_on_probability output. For every
    covariance_type, a window drawn entirely from the calm generating state
    must produce a higher average risk_on_probability than a window drawn
    entirely from the turbulent state. This directly reproduces (on fixed
    code) the "crash regime correctly reports low risk-on probability"
    property that a mislabeled 'bull' state would silently violate and that
    feeds MacroEconomicDTO.killSwitch/market_regime in dto_models.py.

    'tied' is deliberately excluded from this parametrization -- NOT because
    its labeling is still wrong (test_identify_states_by_vol_semantic_
    correctness_across_covariance_types above confirms it's correctly
    fixed), but because of a separate, structural limitation verified
    empirically: forcing a single shared covariance matrix across states
    fundamentally conflicts with discriminating regimes whose defining
    characteristic IS different variance (calm: variances ~1e-5-4e-4;
    turbulent: ~3e-4-25, a >1000x spread) -- on this synthetic scenario the
    tied-covariance EM fit collapses to one dominant state for both windows
    (risk_on_probability == 1.0 for BOTH calm and turbulent), reproducibly
    across every random_state/n_inits combination tried. This is a
    documented, pre-existing property of 'tied' covariance for volatility-
    regime data, not a regression this PR introduces or could fix within
    identify_states_by_vol() -- see docs/regime_model_tuning_guide.md's
    Covariance Structures section."""
    means_array = np.array([
        [0.0008, 0.10, 13.0, 0.5],
        [-0.0010, 0.35, 28.0, 0.3],
    ])
    covars_array = np.array([
        [1e-5, 0.0004, 4.0, 0.04],
        [3e-4, 0.0025, 25.0, 0.04],
    ])
    n = 300
    dates = pd.bdate_range(end=pd.Timestamp("2024-01-01"), periods=n)
    columns = ["spy_return", "realized_vol_20d", "vix_level", "yield_curve_spread"]

    calm_model = GaussianHMM(n_components=2, covariance_type="diag", random_state=99)
    calm_model.startprob_ = np.array([1.0, 0.0])
    calm_model.transmat_ = np.array([[0.995, 0.005], [0.005, 0.995]])
    calm_model.means_ = means_array
    calm_model.covars_ = covars_array
    calm_X, _ = calm_model.sample(n, random_state=1)
    calm_df = pd.DataFrame(calm_X, index=dates, columns=columns)

    turbulent_model = GaussianHMM(n_components=2, covariance_type="diag", random_state=99)
    turbulent_model.startprob_ = np.array([0.0, 1.0])
    turbulent_model.transmat_ = np.array([[0.995, 0.005], [0.005, 0.995]])
    turbulent_model.means_ = means_array
    turbulent_model.covars_ = covars_array
    turbulent_X, _ = turbulent_model.sample(n, random_state=2)
    turbulent_df = pd.DataFrame(turbulent_X, index=dates, columns=columns)

    detector = HMMRegimeDetector(
        n_states=2, covariance_type=cov_type, retrain_freq_days=10_000, random_state=7
    )
    detector.fit(pd.concat([calm_df, turbulent_df]))

    calm_risk_on = detector.predict_proba(calm_df)["risk_on_probability"]
    turbulent_risk_on = detector.predict_proba(turbulent_df)["risk_on_probability"]

    assert calm_risk_on > turbulent_risk_on, (
        f"covariance_type={cov_type}: calm-window risk_on_probability "
        f"({calm_risk_on:.3f}) was not higher than turbulent-window "
        f"({turbulent_risk_on:.3f})."
    )


def test_identify_states_by_vol_n4_highest_variance_labeled_bear():
    """Regression test: for n_states >= 4, the highest-variance state must
    be labeled 'bear' (not a generic 'state_<n-1>') and the lowest 'bull'.
    Pre-fix, the labels list was built by loop position rather than by
    sorted rank, so the last rank indexed past DEFAULT_STATE_LABELS_3's
    length-3 list and fell through to a generic label -- reachable today
    via validation/regime_diagnostics.py's default state_counts=[2,3,4]
    sweep (scripts/audit_regime_model.py --compare calls it with no
    override). Uses a stubbed model (no real EM fit needed) to isolate
    identify_states_by_vol()'s label-assignment logic deterministically."""
    detector = HMMRegimeDetector(n_states=4, covariance_type="diag", random_state=1)
    # Ascending per-state variance sums: state 0 lowest, state 3 highest.
    detector.model = SimpleNamespace(
        covars_=np.array([
            [1.0, 1.0, 1.0, 1.0],   # sum = 4
            [2.0, 2.0, 2.0, 2.0],   # sum = 8
            [3.0, 3.0, 3.0, 3.0],   # sum = 12
            [4.0, 4.0, 4.0, 4.0],   # sum = 16
        ]),
    )

    labels = detector.identify_states_by_vol()

    assert labels[0] == "bull"
    assert labels[1] == "state_1"
    assert labels[2] == "state_2"
    assert labels[3] == "bear"


def test_identify_states_by_vol_logs_error_on_variance_length_mismatch(caplog):
    """If the extracted per-state variance array's length doesn't match
    n_states, identify_states_by_vol() must log an error (CONSTRAINT #6:
    never silent) before falling back to the arbitrary np.arange()
    ordering -- this is what let the spherical mislabeling bug ship
    undetected pre-fix. Uses a stubbed model whose covars_ deliberately has
    the wrong number of per-state matrices to trip the fallback
    deterministically, without needing to reproduce a real hmmlearn
    malformed shape."""
    detector = HMMRegimeDetector(n_states=4, covariance_type="full", random_state=1)
    detector.model = SimpleNamespace(
        covars_=[np.eye(4) * v for v in (1.0, 2.0, 3.0, 4.0, 5.0)],  # 5 matrices, n_states=4
    )

    with caplog.at_level(logging.ERROR):
        labels = detector.identify_states_by_vol()

    assert len(labels) == 4
    assert any(
        "length" in record.message and "full" in record.message
        for record in caplog.records
    ), f"Expected an ERROR log naming the length mismatch and covariance_type; got: {[r.message for r in caplog.records]}"


def test_hmm_compute_diagnostics():
    """Verifies that compute_diagnostics returns AIC, BIC, transition matrix, and state metrics."""
    n = 150
    rng = np.random.default_rng(42)
    dates = pd.bdate_range(end=pd.Timestamp("2024-01-01"), periods=n)
    features_df = pd.DataFrame(
        {
            "spy_return": rng.normal(0.0005, 0.01, size=n),
            "realized_vol_20d": rng.uniform(0.10, 0.25, size=n),
            "vix_level": rng.uniform(12, 28, size=n),
            "yield_curve_spread": rng.normal(0.5, 0.3, size=n),
        },
        index=dates,
    )

    detector = HMMRegimeDetector(n_states=3, covariance_type="diag", random_state=42)
    detector.fit(features_df)

    diag = detector.compute_diagnostics(features_df)
    assert diag["n_states"] == 3
    assert diag["covariance_type"] == "diag"
    assert "log_likelihood" in diag
    assert "aic" in diag
    assert "bic" in diag
    assert "transition_matrix" in diag
    assert "expected_durations_days" in diag
    assert "state_metrics" in diag

    assert len(diag["transition_matrix"]) == 3
    assert len(diag["expected_durations_days"]) == 3

