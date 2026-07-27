"""Tests for forecasting/bert_lla.py -- the BERT-LLA PyTorch dual-LSTM +
self-attention forecaster and its three registered ablations.

torch is NOT installed in this environment (it's an optional dependency,
requirements-optional.txt) -- TORCH_AVAILABLE is genuinely False here. Two
tiers of coverage accordingly:

1. Pure numpy/logic helpers (build_masked_sentiment_channel,
   sentiment_coverage) and the absent-dependency degradation path run
   unconditionally -- they need no torch import at all.
2. Actual model architecture/forward-pass tests (LLAAttention,
   BertLLARegressor, fit_predict_bert_lla) are skipped here
   (@pytest.mark.skipif) rather than faked with a hand-rolled tensor mock --
   a fake mock would not validate real matrix arithmetic (attention softmax,
   LSTM state) and would be closer to pretending to test than honest
   coverage. These tests are correct and will run in an environment with
   torch installed; see the PR's own risk register for this sandbox
   limitation.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from forecasting.bert_lla import (
    ABLATIONS,
    TORCH_AVAILABLE,
    build_masked_sentiment_channel,
    sentiment_coverage,
)

_skip_no_torch = pytest.mark.skipif(
    not TORCH_AVAILABLE, reason="torch not installed in this environment"
)


class TestAblationsConstant:
    def test_three_ablations_registered(self):
        assert ABLATIONS == ("lstm_baseline", "lstm_attention", "bert_lla")


class TestBuildMaskedSentimentChannel:
    def test_all_observed(self):
        dates = ["2026-07-20", "2026-07-21"]
        sentiment_by_day = {
            "2026-07-20": {"s_t": 0.3},
            "2026-07-21": {"s_t": -0.1},
        }
        s_filled, s_mask = build_masked_sentiment_channel(dates, sentiment_by_day)
        np.testing.assert_allclose(s_filled, [0.3, -0.1])
        np.testing.assert_allclose(s_mask, [1.0, 1.0])

    def test_all_unobserved_never_fabricates_nonzero(self):
        """CONSTRAINT #4: an entirely-missing day must yield s_filled=0.0
        AND s_observed_mask=0.0 -- 0.0 here is a masked placeholder, never
        a claimed real sentiment reading."""
        dates = ["2026-07-20", "2026-07-21"]
        s_filled, s_mask = build_masked_sentiment_channel(dates, {})
        np.testing.assert_allclose(s_filled, [0.0, 0.0])
        np.testing.assert_allclose(s_mask, [0.0, 0.0])

    def test_mixed_observed_and_unobserved(self):
        dates = ["2026-07-19", "2026-07-20", "2026-07-21"]
        sentiment_by_day = {"2026-07-20": {"s_t": 0.6}}
        s_filled, s_mask = build_masked_sentiment_channel(dates, sentiment_by_day)
        np.testing.assert_allclose(s_filled, [0.0, 0.6, 0.0])
        np.testing.assert_allclose(s_mask, [0.0, 1.0, 0.0])

    def test_day_present_but_s_t_none_treated_as_unobserved(self):
        """A day entry that exists but carries s_t=None (e.g. both news and
        review unavailable that day, per signals.sentiment_index's own
        degradation) must not be treated as an observed zero."""
        dates = ["2026-07-21"]
        sentiment_by_day = {"2026-07-21": {"s_t": None}}
        s_filled, s_mask = build_masked_sentiment_channel(dates, sentiment_by_day)
        np.testing.assert_allclose(s_filled, [0.0])
        np.testing.assert_allclose(s_mask, [0.0])

    def test_empty_dates_returns_empty_arrays(self):
        s_filled, s_mask = build_masked_sentiment_channel([], {})
        assert len(s_filled) == 0
        assert len(s_mask) == 0


class TestSentimentCoverage:
    def test_full_coverage(self):
        assert sentiment_coverage(np.array([1.0, 1.0, 1.0])) == pytest.approx(1.0)

    def test_zero_coverage(self):
        assert sentiment_coverage(np.array([0.0, 0.0, 0.0])) == pytest.approx(0.0)

    def test_partial_coverage(self):
        assert sentiment_coverage(np.array([1.0, 0.0, 1.0, 0.0])) == pytest.approx(0.5)

    def test_empty_mask_is_zero_not_fabricated(self):
        """CONSTRAINT #4: an empty window must report 0.0 coverage, never a
        fabricated 'fully covered' or undefined value."""
        assert sentiment_coverage(np.array([])) == pytest.approx(0.0)


class TestAbsentTorchDegradation:
    def test_torch_unavailable_in_this_environment(self):
        """Documents the actual sandbox state this test suite runs
        under -- see this module's docstring."""
        assert TORCH_AVAILABLE is False

    def test_bert_lla_regressor_is_none_when_torch_absent(self):
        from forecasting.bert_lla import BertLLARegressor
        assert BertLLARegressor is None

    def test_lla_attention_is_none_when_torch_absent(self):
        from forecasting.bert_lla import LLAAttention
        assert LLAAttention is None

    def test_fit_predict_raises_runtime_error_when_torch_absent(self):
        """The caller (ForecastingEngine.run_bert_lla_forecast) is
        responsible for catching this and degrading to the zero sentinel
        -- this function itself raises rather than silently no-op'ing."""
        from forecasting.bert_lla import fit_predict_bert_lla
        with pytest.raises(RuntimeError):
            fit_predict_bert_lla(
                np.zeros((2, 5, 10)), np.zeros((2, 4)), np.zeros((1, 5, 10)),
                use_attention=True,
            )


@_skip_no_torch
class TestLLAAttentionArchitecture:
    def test_alpha_sums_to_one_across_sequence(self):
        import torch
        from forecasting.bert_lla import LLAAttention

        attn = LLAAttention(hidden_dim=8)
        lstm_outputs = torch.randn(3, 22, 8)  # batch=3, seq_len=22, hidden=8
        context, alpha = attn(lstm_outputs)
        assert alpha.shape == (3, 22, 1)
        sums = alpha.sum(dim=1).squeeze(-1)
        for s in sums.tolist():
            assert math.isclose(s, 1.0, abs_tol=1e-5)
        assert context.shape == (3, 8)

    def test_alpha_is_nonnegative(self):
        import torch
        from forecasting.bert_lla import LLAAttention

        attn = LLAAttention(hidden_dim=4)
        lstm_outputs = torch.randn(1, 10, 4)
        _, alpha = attn(lstm_outputs)
        assert bool((alpha >= 0).all())


@_skip_no_torch
class TestBertLLARegressorArchitecture:
    def test_forward_shape_with_attention(self):
        import torch
        from forecasting.bert_lla import BertLLARegressor

        model = BertLLARegressor(input_dim=10, use_attention=True, output_dim=4)
        x = torch.randn(2, 22, 10)  # batch=2, window=22, features=10
        pred, alpha = model(x)
        assert pred.shape == (2, 4)
        assert alpha is not None
        assert alpha.shape == (2, 22, 1)

    def test_forward_shape_without_attention(self):
        import torch
        from forecasting.bert_lla import BertLLARegressor

        model = BertLLARegressor(input_dim=10, use_attention=False, output_dim=4)
        x = torch.randn(2, 22, 10)
        pred, alpha = model(x)
        assert pred.shape == (2, 4)
        assert alpha is None

    def test_bert_lla_input_dim_is_technical_plus_two_sentiment_cols(self):
        """The 'bert_lla' ablation's feature width is the shared 10
        technical columns plus the 2-channel masked sentiment encoding."""
        import torch
        from forecasting.bert_lla import BertLLARegressor

        model = BertLLARegressor(input_dim=12, use_attention=True, output_dim=4)
        x = torch.randn(1, 22, 12)
        pred, alpha = model(x)
        assert pred.shape == (1, 4)


@_skip_no_torch
class TestFitPredictBertLLA:
    def test_predictions_and_alpha_shapes(self):
        from forecasting.bert_lla import fit_predict_bert_lla

        n_samples, window, n_features, n_horizons = 20, 22, 10, 4
        X_train = np.random.rand(n_samples, window, n_features).astype(np.float32)
        Y_train = np.random.rand(n_samples, n_horizons).astype(np.float32)
        last_window = np.random.rand(1, window, n_features).astype(np.float32)

        pred, alpha = fit_predict_bert_lla(
            X_train, Y_train, last_window, use_attention=True, epochs=2,
        )
        assert pred.shape == (n_horizons,)
        assert alpha.shape == (window,)
        assert math.isclose(float(alpha.sum()), 1.0, abs_tol=1e-4)

    def test_no_attention_returns_none_alpha(self):
        from forecasting.bert_lla import fit_predict_bert_lla

        n_samples, window, n_features, n_horizons = 20, 22, 10, 4
        X_train = np.random.rand(n_samples, window, n_features).astype(np.float32)
        Y_train = np.random.rand(n_samples, n_horizons).astype(np.float32)
        last_window = np.random.rand(1, window, n_features).astype(np.float32)

        pred, alpha = fit_predict_bert_lla(
            X_train, Y_train, last_window, use_attention=False, epochs=2,
        )
        assert pred.shape == (n_horizons,)
        assert alpha is None
