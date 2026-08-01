"""
tests/test_sf_garch_lstm.py
============================
Tests for ml/models/sf_garch_lstm.py's real GJR-GARCH + optional sentiment
+ LSTM (ridge-fallback) implementation -- see that module's docstring for
the full architecture. Covers: real GARCH parameter fitting, the rolling-std
fallback when ``arch`` is unavailable/fails, the optional sentiment channel,
graceful ridge-only degradation when TensorFlow is unavailable (true in
this sandbox -- no live TF install), and zero-lookahead perturbation tests
for the two causal building blocks (AGENTS.md: "write a perturbation test"
for anything time-series-shaped).
"""
import numpy as np
import pandas as pd
import pytest

from ml.models.sf_garch_lstm import (
    SFGarchLSTMModel,
    _gjr_garch_conditional_vol,
    _make_sequences,
    _build_feature_matrix,
)


def _returns_df(n=120, seed=0, with_sentiment=False):
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({"returns": rng.normal(0, 0.01, n)})
    if with_sentiment:
        df["sentiment_score"] = rng.uniform(-1, 1, n)
    return df


class TestGjrGarchConditionalVolCausality:
    def test_sigma_at_t_unaffected_by_return_at_t(self):
        """Perturbing returns[t] must not change sigma2[t] (it's a forecast
        made BEFORE returns[t] is observed) -- only sigma2[t+1:] may change."""
        rng = np.random.default_rng(1)
        returns = rng.normal(0, 1, 50)
        params = dict(mu=0.01, omega=0.05, alpha=0.05, gamma=0.03, beta=0.85)

        base = _gjr_garch_conditional_vol(returns.copy(), **params)
        perturbed_returns = returns.copy()
        perturbed_returns[25] += 1000.0  # a huge shock at t=25
        perturbed = _gjr_garch_conditional_vol(perturbed_returns, **params)

        np.testing.assert_array_equal(base[:26], perturbed[:26])
        # Downstream values (t=26 onward) must differ given such a large shock.
        assert not np.array_equal(base[26:], perturbed[26:])

    def test_sigma_positive_and_finite(self):
        rng = np.random.default_rng(2)
        returns = rng.normal(0, 2, 200)
        vol = _gjr_garch_conditional_vol(returns, mu=0.0, omega=0.1, alpha=0.05, gamma=0.05, beta=0.85)
        assert np.all(np.isfinite(vol))
        assert np.all(vol > 0)


class TestMakeSequencesCausality:
    def test_future_row_never_affects_earlier_window(self):
        features = np.arange(20 * 2, dtype=float).reshape(20, 2)
        seq_len = 5
        base = _make_sequences(features, seq_len)

        perturbed = features.copy()
        perturbed[-1] = 999999.0  # perturb only the LAST row
        perturbed_windows = _make_sequences(perturbed, seq_len)

        # Every window except the very last one is untouched by the
        # perturbation of the final row.
        np.testing.assert_array_equal(base[:-1], perturbed_windows[:-1])
        assert not np.array_equal(base[-1], perturbed_windows[-1])

    def test_window_shape(self):
        features = np.random.rand(30, 3)
        windows = _make_sequences(features, sequence_length=10)
        assert windows.shape == (21, 10, 3)  # 30 - 10 + 1 = 21 windows

    def test_too_few_rows_returns_empty(self):
        features = np.random.rand(3, 2)
        windows = _make_sequences(features, sequence_length=10)
        assert windows.shape[0] == 0


class TestFeatureMatrix:
    def test_includes_sentiment_channel_when_present(self):
        df = _returns_df(with_sentiment=True)
        features = _build_feature_matrix(df, garch_params=None)
        assert features.shape[1] == 3

    def test_two_channels_without_sentiment(self):
        df = _returns_df(with_sentiment=False)
        features = _build_feature_matrix(df, garch_params=None)
        assert features.shape[1] == 2

    def test_uses_rolling_std_fallback_when_garch_params_none(self):
        df = _returns_df()
        features = _build_feature_matrix(df, garch_params=None)
        assert np.all(np.isfinite(features))


class TestSFGarchLSTMModelReal:
    def test_fit_produces_real_garch_params(self):
        """arch is a required dependency (requirements.txt) -- the GARCH
        component must actually fit, not silently no-op."""
        model = SFGarchLSTMModel()
        df = _returns_df(n=150)
        model.fit(df, df["returns"])
        assert model.garch_params is not None
        for key in ("mu", "omega", "alpha", "gamma", "beta"):
            assert key in model.garch_params
            assert np.isfinite(model.garch_params[key])

    def test_predict_degrades_to_ridge_without_tensorflow(self):
        """No live TensorFlow install in this sandbox -- fit()/predict()
        must degrade to the documented ridge fallback, never raise
        (CONSTRAINT #6), and never return NaN/fabricated values."""
        model = SFGarchLSTMModel(sequence_length=5)
        df = _returns_df(n=150)
        model.fit(df, df["returns"])
        assert model.lstm_weights is None  # TF genuinely unavailable here
        preds = model.predict(df)
        assert isinstance(preds, np.ndarray)
        assert len(preds) == len(df)
        assert np.all(np.isfinite(preds))

    def test_sentiment_column_changes_predictions(self):
        """A real third input channel: predictions must differ when
        sentiment_score is present vs absent for the same returns."""
        base_df = _returns_df(n=150, with_sentiment=False)
        model_no_sent = SFGarchLSTMModel(sequence_length=5)
        model_no_sent.fit(base_df, base_df["returns"])
        preds_no_sent = model_no_sent.predict(base_df)

        sent_df = base_df.copy()
        sent_df["sentiment_score"] = np.random.default_rng(3).uniform(-1, 1, len(sent_df))
        model_sent = SFGarchLSTMModel(sequence_length=5)
        model_sent.fit(sent_df, sent_df["returns"])
        preds_sent = model_sent.predict(sent_df)

        assert not np.allclose(preds_no_sent, preds_sent)

    def test_save_load_predict_roundtrip_exact(self, tmp_path):
        model = SFGarchLSTMModel(sequence_length=5)
        df = _returns_df(n=150)
        model.fit(df, df["returns"])
        preds = model.predict(df)

        path = tmp_path / "model.pkl"
        model.save(path)
        reloaded = SFGarchLSTMModel.load(path)
        assert reloaded.is_fitted
        np.testing.assert_array_equal(reloaded.predict(df), preds)

    def test_insufficient_history_raises(self):
        model = SFGarchLSTMModel(sequence_length=10)
        df = _returns_df(n=3)
        with pytest.raises(ValueError, match="Insufficient history"):
            model.fit(df, df["returns"])

    def test_unfitted_predict_returns_zeros(self):
        model = SFGarchLSTMModel()
        df = _returns_df(n=20)
        preds = model.predict(df)
        assert np.all(preds == 0.0)
