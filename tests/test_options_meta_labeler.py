"""Tests for ml/options_meta_labeler.py (Stage 4 Options ML Meta-Labeler)."""

import tempfile
from pathlib import Path
import numpy as np
import pytest

from ml.options_meta_labeler import (
    OptionsMetaLabeler,
    OptionsTradeFeatureRow,
    global_options_meta_labeler,
)


def _make_training_samples(n_pairs: int = 50, force_outcome: int | None = None):
    """Builds `n_pairs` winning + `n_pairs` losing synthetic training samples.

    When `force_outcome` is given (0 or 1), every sample -- winning-shaped AND
    losing-shaped -- is stamped with that same outcome, producing a degenerate
    single-class training set while keeping the underlying feature
    distributions realistic.
    """
    samples = []
    for _ in range(n_pairs):
        win_outcome = 1 if force_outcome is None else force_outcome
        loss_outcome = 0 if force_outcome is None else force_outcome
        # Winning-shaped sample: high IVR, high VRP, bullish trend
        samples.append(
            OptionsTradeFeatureRow(
                strategy="Put Credit Spread",
                ivr=60.0 + np.random.uniform(0, 30),
                vrp=0.03 + np.random.uniform(0, 0.03),
                vix=18.0 + np.random.uniform(0, 5),
                trend_bias=1.0,
                target_dte=35,
                credit_to_width_ratio=0.30,
                short_delta=0.25,
                outcome_win=win_outcome,
            )
        )
        # Losing-shaped sample: low IVR, negative VRP, bearish trend
        samples.append(
            OptionsTradeFeatureRow(
                strategy="Put Credit Spread",
                ivr=10.0 + np.random.uniform(0, 15),
                vrp=-0.02 + np.random.uniform(0, 0.01),
                vix=35.0 + np.random.uniform(0, 10),
                trend_bias=-1.0,
                target_dte=35,
                credit_to_width_ratio=0.15,
                short_delta=0.45,
                outcome_win=loss_outcome,
            )
        )
    return samples


def test_feature_vector_extraction():
    labeler = OptionsMetaLabeler()
    row = OptionsTradeFeatureRow(
        strategy="Put Credit Spread",
        ivr=65.0,
        vrp=0.035,
        vix=18.5,
        trend_bias=1.0,
        target_dte=35,
        credit_to_width_ratio=0.33,
        short_delta=0.30,
    )
    vec = labeler._extract_feature_vector(row)
    # trend_bias was dropped from the feature vector entirely -- 9 columns now.
    assert len(vec) == 9
    assert vec[0] == 1.0  # is_put_spread
    assert vec[1] == 0.0  # is_call_spread
    assert vec[2] == 0.0  # is_iron_condor
    assert vec[3] == 0.65  # ivr / 100
    assert vec[4] == 0.035  # vrp
    assert vec[5] == 18.5 / 50.0  # vix / 50
    assert vec[6] == 35 / 60.0  # target_dte / 60 (index 6 is no longer trend_bias)
    assert vec[7] == 0.33  # credit_to_width_ratio
    assert vec[8] == 0.30  # short_delta


def test_train_and_predict():
    with tempfile.TemporaryDirectory() as tmp_dir:
        model_path = Path(tmp_dir) / "test_meta.pkl"
        labeler = OptionsMetaLabeler(model_path=model_path)

        samples = _make_training_samples(n_pairs=50)

        res = labeler.train(samples)
        assert res["samples"] == 100
        assert res["accuracy"] >= 0.70
        # Minimal-fix disclosure: train() reports in-sample metrics with no
        # held-out/purged evaluation -- callers must be able to see that.
        assert res["metrics_are_in_sample"] is True

        # Predict on a high-quality candidate
        good_cand = {
            "strategy": "Put Credit Spread",
            "ivr": 75.0,
            "vrp": 0.04,
            "vix": 19.0,
            "trend_bias": 1.0,
            "target_dte": 35,
            "credit_to_width_ratio": 0.32,
            "short_delta": 0.25,
        }
        p_good = labeler.predict_probability(good_cand)
        assert p_good > 0.60

        # Predict on a poor candidate
        bad_cand = {
            "strategy": "Put Credit Spread",
            "ivr": 12.0,
            "vrp": -0.02,
            "vix": 38.0,
            "trend_bias": -1.0,
            "target_dte": 35,
            "credit_to_width_ratio": 0.12,
            "short_delta": 0.45,
        }
        p_bad = labeler.predict_probability(bad_cand)
        assert p_bad < 0.50

        # Test sizing multiplier
        mult_good = labeler.get_sizing_multiplier(p_good, min_confidence=0.52)
        assert mult_good >= 1.0

        mult_bad = labeler.get_sizing_multiplier(p_bad, min_confidence=0.52)
        assert mult_bad == 0.0

        # Test score directive
        score_info = labeler.score_option_directive(good_cand)
        assert score_info["approved"] is True
        assert score_info["prob_win"] > 0.60
        # Fully-populated, finite candidates must report their features as
        # resolved -- the new key is purely additive to this response shape.
        assert score_info["features_resolved"] is True

        bad_score_info = labeler.score_option_directive(bad_cand)
        assert bad_score_info["features_resolved"] is True

        # Test persistence
        labeler2 = OptionsMetaLabeler(model_path=model_path)
        loaded = labeler2.load_model()
        assert loaded is True
        assert labeler2.n_samples == 100
        assert labeler2.predict_probability(good_cand) == p_good


def test_nan_ivr_declines_to_score_instead_of_predicting_confidently():
    """A required feature that is PRESENT in the dict but NaN (the real shape
    ``execution/options_paper_executor.py::get_actionable_directives`` produces
    for an unresolvable ``True_IVR``) must never sail through as a normal
    value into the model. It must decline to score (neutral 0.65 / 1.0x
    sizing), not produce a confident, possibly INCREASED prediction."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        model_path = Path(tmp_dir) / "test_meta.pkl"
        labeler = OptionsMetaLabeler(model_path=model_path)
        labeler.train(_make_training_samples(n_pairs=50))

        # Shaped like an objectively strong "good" candidate in every field
        # EXCEPT ivr, which is unresolved (NaN, not absent).
        candidate = {
            "strategy": "Put Credit Spread",
            "ivr": float("nan"),
            "vrp": 0.05,
            "vix": 17.0,
            "target_dte": 35,
            "credit_to_width_ratio": 0.35,
            "short_delta": 0.20,
        }

        prob = labeler.predict_probability(candidate)
        assert prob == 0.65

        mult = labeler.get_sizing_multiplier(prob)
        assert mult == 1.0

        score_info = labeler.score_option_directive(candidate)
        assert score_info["features_resolved"] is False


def test_none_valued_feature_also_declines_to_score():
    """Proves the "present but None" path is caught too, not just
    "present but NaN" -- both are explicitly-unresolved, not omitted."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        model_path = Path(tmp_dir) / "test_meta.pkl"
        labeler = OptionsMetaLabeler(model_path=model_path)
        labeler.train(_make_training_samples(n_pairs=50))

        candidate = {
            "strategy": "Put Credit Spread",
            "ivr": 75.0,
            "vrp": 0.05,
            "vix": None,
            "target_dte": 35,
            "credit_to_width_ratio": 0.35,
            "short_delta": 0.20,
        }

        prob = labeler.predict_probability(candidate)
        assert prob == 0.65

        mult = labeler.get_sizing_multiplier(prob)
        assert mult == 1.0

        score_info = labeler.score_option_directive(candidate)
        assert score_info["features_resolved"] is False


def test_degenerate_single_class_training_never_produces_unclipped_confidence():
    """A degenerate (single-outcome) training run collapses `self.model` to
    a bare ("baseline", 0.0 or 1.0) tuple. predict_probability must clip that
    the same way the other two model branches already do -- never return an
    unclipped 0.0/1.0 that would map to a 1.5x (or 0.0x) sizing decision off
    a training set that never actually observed both outcomes."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        model_path = Path(tmp_dir) / "test_meta.pkl"
        labeler = OptionsMetaLabeler(model_path=model_path)

        # Force every sample (win-shaped and loss-shaped alike) to outcome=1.
        samples = _make_training_samples(n_pairs=25, force_outcome=1)
        res = labeler.train(samples)
        assert res["metrics_are_in_sample"] is True

        bad_candidate = {
            "strategy": "Put Credit Spread",
            "ivr": 5.0,
            "vrp": -0.05,
            "vix": 45.0,
            "target_dte": 35,
            "credit_to_width_ratio": 0.05,
            "short_delta": 0.50,
        }
        good_candidate = {
            "strategy": "Put Credit Spread",
            "ivr": 95.0,
            "vrp": 0.08,
            "vix": 12.0,
            "target_dte": 35,
            "credit_to_width_ratio": 0.45,
            "short_delta": 0.15,
        }

        p_bad = labeler.predict_probability(bad_candidate)
        p_good = labeler.predict_probability(good_candidate)

        assert 0.05 <= p_bad <= 0.95
        assert 0.05 <= p_good <= 0.95


def test_score_option_directive_reports_features_resolved_key():
    """Sanity check that the new `features_resolved` key is present and
    correctly True for the existing, fully-populated fixtures."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        model_path = Path(tmp_dir) / "test_meta.pkl"
        labeler = OptionsMetaLabeler(model_path=model_path)
        labeler.train(_make_training_samples(n_pairs=50))

        good_cand = {
            "strategy": "Put Credit Spread",
            "ivr": 75.0,
            "vrp": 0.04,
            "vix": 19.0,
            "target_dte": 35,
            "credit_to_width_ratio": 0.32,
            "short_delta": 0.25,
        }
        info = labeler.score_option_directive(good_cand)
        assert "features_resolved" in info
        assert info["features_resolved"] is True
