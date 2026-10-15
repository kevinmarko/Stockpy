"""Tests for forecasting/bert_lla.py -- the BERT-LLA PyTorch dual-LSTM +
self-attention forecaster and its three registered ablations.

Supports both environments where torch is installed (core requirements.txt)
and lean environments where torch might be absent:

1. Pure numpy/logic helpers (build_masked_sentiment_channel,
   sentiment_coverage) run unconditionally.
2. Actual model architecture/forward-pass tests (LLAAttention,
   BertLLARegressor, fit_predict_bert_lla) run when torch is installed
   (@_skip_no_torch).
3. Absent-torch degradation checks verify that without torch, sentinel
   fallbacks and RuntimeError guards fire as expected.
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
_skip_with_torch = pytest.mark.skipif(
    TORCH_AVAILABLE, reason="torch is installed in this environment"
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
    @_skip_with_torch
    def test_torch_unavailable_in_this_environment(self):
        """Documents absent-torch state when running in an unequipped environment."""
        assert TORCH_AVAILABLE is False

    @_skip_with_torch
    def test_bert_lla_regressor_is_none_when_torch_absent(self):
        from forecasting.bert_lla import BertLLARegressor
        assert BertLLARegressor is None

    @_skip_with_torch
    def test_lla_attention_is_none_when_torch_absent(self):
        from forecasting.bert_lla import LLAAttention
        assert LLAAttention is None

    def test_fit_predict_raises_runtime_error_when_torch_absent(self, monkeypatch):
        """The caller (ForecastingEngine.run_bert_lla_forecast) is
        responsible for catching this and degrading to the zero sentinel
        -- this function itself raises rather than silently no-op'ing."""
        import forecasting.bert_lla as bert_lla
        monkeypatch.setattr(bert_lla, "TORCH_AVAILABLE", False)
        with pytest.raises(RuntimeError, match="torch is not available"):
            bert_lla.fit_predict_bert_lla(
                np.zeros((2, 5, 10)), np.zeros((2, 4)), np.zeros((1, 5, 10)),
                use_attention=True,
            )

    @_skip_no_torch
    def test_torch_available_when_installed(self):
        """Documents that torch is available and model classes are defined when installed."""
        assert TORCH_AVAILABLE is True
        from forecasting.bert_lla import BertLLARegressor, LLAAttention
        assert BertLLARegressor is not None
        assert LLAAttention is not None


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


@_skip_no_torch
class TestOpenMPCoexistence:
    def test_torch_intraop_threads_capped_to_one(self):
        """Documents that forecasting/bert_lla caps torch intra-op threads to 1
        to avoid OpenMP collisions with LightGBM on macOS (Apple Silicon)."""
        import torch
        assert torch.get_num_threads() == 1

    def test_lightgbm_and_torch_sequential_execution_no_segfault(self):
        """Verifies LightGBM and PyTorch can train and infer sequentially in the
        same process without OpenMP collision deadlocks or SIGSEGV."""
        import lightgbm as lgb
        import torch
        import torch.nn as nn

        # Run LightGBM train/predict
        X = np.random.randn(50, 5).astype(np.float32)
        y = np.random.randn(50).astype(np.float32)
        ds = lgb.Dataset(X, label=y)
        booster = lgb.train({"verbosity": -1, "num_threads": 2}, ds, num_boost_round=3)
        lgb_preds = booster.predict(X)
        assert lgb_preds.shape == (50,)

        # Run PyTorch LSTM forward pass
        lstm = nn.LSTM(5, 16, batch_first=True)
        x_tensor = torch.randn(2, 10, 5)
        out, _ = lstm(x_tensor)
        assert out.shape == (2, 10, 16)

