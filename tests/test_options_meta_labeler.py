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


def test_feature_vector_extraction():
    labeler = OptionsMetaLabeler()
    row = OptionsTradeFeatureRow(
        strategy="Put Credit Spread",
        ivr=65.0,
        vrp=0.035,
        vix=18.5,
        credit_to_width_ratio=0.33,
        short_delta=0.30,
    )
    vec = labeler._extract_feature_vector(row)
    assert len(vec) == 8
    assert vec[0] == 1.0  # is_put_spread
    assert vec[1] == 0.0  # is_call_spread
    assert vec[3] == 0.65  # ivr / 100
    assert vec[4] == 0.035  # vrp
    assert vec[6] == 0.33  # credit_to_width_ratio


def test_train_and_predict():
    with tempfile.TemporaryDirectory() as tmp_dir:
        model_path = Path(tmp_dir) / "test_meta.pkl"
        labeler = OptionsMetaLabeler(model_path=model_path)

        # Generate synthetic historical training samples
        # Bullish put spreads with high VRP and IVR tend to win
        samples = []
        for i in range(50):
            # Winning sample: high IVR, high VRP, bullish trend
            samples.append(
                OptionsTradeFeatureRow(
                    strategy="Put Credit Spread",
                    ivr=60.0 + np.random.uniform(0, 30),
                    vrp=0.03 + np.random.uniform(0, 0.03),
                    vix=18.0 + np.random.uniform(0, 5),
                    credit_to_width_ratio=0.30,
                    short_delta=0.25,
                    outcome_win=1,
                )
            )
            # Losing sample: low IVR, negative VRP, bearish trend on put spread
            samples.append(
                OptionsTradeFeatureRow(
                    strategy="Put Credit Spread",
                    ivr=10.0 + np.random.uniform(0, 15),
                    vrp=-0.02 + np.random.uniform(0, 0.01),
                    vix=35.0 + np.random.uniform(0, 10),
                    credit_to_width_ratio=0.15,
                    short_delta=0.45,
                    outcome_win=0,
                )
            )

        res = labeler.train(samples)
        assert res["samples"] == 100
        assert res["in_sample_accuracy"] >= 0.50
        assert "oos_accuracy" in res
        assert "oos_roc_auc" in res
        assert res["oos_accuracy"] >= 0.0

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

        # Test persistence
        labeler2 = OptionsMetaLabeler(model_path=model_path)
        loaded = labeler2.load_model()
        assert loaded is True
        assert labeler2.n_samples == 100
        assert labeler2.predict_probability(good_cand) == p_good
