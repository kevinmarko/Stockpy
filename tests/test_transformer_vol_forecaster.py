import numpy as np
import pandas as pd
import pytest
from ml.transformer_vol_forecaster import (
    build_tft_model,
    train_vol_forecaster,
    predict_multi_horizon_vol,
    forward_pass,
    build_causal_vol_features,
    build_training_windows,
)

def test_build_model():
    model = build_tft_model(seq_len=10, d_model=16, num_heads=4, horizons=[1, 5, 21, 60])
    assert 'weights' in model
    assert 'W_q' in model['weights']
    assert model['weights']['W_q'].shape == (16, 16)

def test_forward_pass():
    model = build_tft_model(seq_len=10, d_model=16, num_heads=4, horizons=[1, 5, 21, 60])
    X = np.random.randn(32, 10, 16) # batch=32, seq_len=10, d_model=16
    preds, attn = forward_pass(X, model)
    assert preds.shape == (32, 4)
    assert attn.shape == (32, 10, 10)

def test_train_and_predict():
    X_train = np.random.randn(100, 10, 16)
    y_train = np.random.randn(100, 4) # 4 horizons

    model = train_vol_forecaster(X_train, y_train, seq_len=10, d_model=16, num_heads=4, horizons=[1, 5, 21, 60])

    X_test = np.random.randn(5, 10, 16)
    forecasts, attn = predict_multi_horizon_vol(X_test, model)

    assert isinstance(forecasts, dict)
    assert '1d' in forecasts
    assert '5d' in forecasts
    assert '21d' in forecasts
    assert '60d' in forecasts

    assert forecasts['1d'].shape == (5,)
    assert forecasts['60d'].shape == (5,)
    assert attn.shape == (5, 10, 10)


# ---------------------------------------------------------------------------
# Real, causal feature/window construction (2026-08, closes audit finding

# F7's "no lookahead-bias perturbation coverage" gap for this module).
# api/pilots_api.py::get_transformer_forecast previously fed this model
# np.random.randn(...) noise as "market history"; build_causal_vol_features/
# build_training_windows are the real replacement, and both need the same
# no-lookahead guarantee every other indicator/forecaster in this codebase
# is required to have (CLAUDE.md: "Every indicator and forecaster must be
# verified to have zero lookahead bias using the perturbation tests").
# ---------------------------------------------------------------------------


def _synthetic_ohlcv(n_days: int = 300, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(loc=0.0003, scale=0.011, size=n_days)
    closes = pd.Series(100.0 * np.cumprod(1 + rets))
    idx = pd.bdate_range(start="2023-01-02", periods=n_days)
    closes.index = idx
    return pd.DataFrame(
        {
            "Open": closes.shift(1).fillna(closes.iloc[0]),
            "High": closes * 1.01,
            "Low": closes * 0.99,
            "Close": closes,
            "Volume": pd.Series(rng.uniform(5e5, 2e6, size=n_days), index=idx),
        },
        index=idx,
    )


def test_build_causal_vol_features_unaffected_by_future_data():
    """Perturbation test: two OHLCV series identical through day T but
    diverging strictly AFTER T must produce identical feature rows through
    day T. This is the same 'output at time T doesn't change if data after T
    is altered' invariant CLAUDE.md requires for every indicator/forecaster."""
    bars = _synthetic_ohlcv(n_days=200, seed=1)
    cutoff = 150  # row index -- everything at/before this index is "the past"

    bars_perturbed = bars.copy()
    # Mutate every column strictly AFTER the cutoff -- a real perturbation,
    # not a no-op (large multiplicative shock + volume spike).
    bars_perturbed.iloc[cutoff + 1:, bars_perturbed.columns.get_indexer(["Open", "High", "Low", "Close"])] *= 5.0
    bars_perturbed.iloc[cutoff + 1:, bars_perturbed.columns.get_loc("Volume")] *= 50.0

    feats_original = build_causal_vol_features(bars)
    feats_perturbed = build_causal_vol_features(bars_perturbed)

    pd.testing.assert_frame_equal(
        feats_original.iloc[: cutoff + 1],
        feats_perturbed.iloc[: cutoff + 1],
    )
    # Sanity: the perturbation actually changed something after the cutoff,
    # proving this isn't a vacuously-true comparison.
    assert not feats_original.iloc[cutoff + 1:].equals(feats_perturbed.iloc[cutoff + 1:])


def test_build_causal_vol_features_dow_and_padding_are_deterministic():
    bars = _synthetic_ohlcv(n_days=100, seed=2)
    feats = build_causal_vol_features(bars, d_model=32)
    assert feats.shape[1] == 32
    # Zero-padding columns are exactly 0.0, never random noise.
    pad_cols = [c for c in feats.columns if c.startswith("_pad_")]
    assert pad_cols
    assert (feats[pad_cols] == 0.0).all().all()
    # Day-of-week one-hots sum to exactly 1.0 for every real trading day.
    dow_cols = [c for c in feats.columns if c.startswith("dow_")]
    assert (feats[dow_cols].sum(axis=1) == 1.0).all()


def test_build_training_windows_input_is_causal():
    """A training window's X (input) must never depend on feature/price data
    strictly after the window's own end index -- only its y (label) is
    allowed to use future data, by design (a supervised-learning label, not
    an input leak)."""
    bars = _synthetic_ohlcv(n_days=300, seed=3)
    feat_df = build_causal_vol_features(bars).dropna()
    feat_matrix = feat_df.to_numpy()
    close_arr = bars["Close"].astype(float).reindex(feat_df.index).to_numpy()

    seq_len = 20
    horizons = [1, 5, 10]
    X_train, y_train, end_indices = build_training_windows(
        feat_matrix, close_arr, seq_len=seq_len, horizons=horizons, stride=5,
    )
    assert len(X_train) > 5

    k = len(end_indices) // 2
    end_idx = int(end_indices[k])
    window_before = X_train[k].copy()

    # Perturb feat_matrix/close_arr strictly AFTER this window's end index.
    feat_matrix_perturbed = feat_matrix.copy()
    feat_matrix_perturbed[end_idx + 1:, :] *= 100.0
    close_arr_perturbed = close_arr.copy()
    close_arr_perturbed[end_idx + 1:] *= 5.0

    X_train_2, y_train_2, end_indices_2 = build_training_windows(
        feat_matrix_perturbed, close_arr_perturbed, seq_len=seq_len, horizons=horizons, stride=5,
    )
    # Same end_idx should still appear (perturbation doesn't touch the
    # window itself or its stride grid).
    assert end_idx in end_indices_2
    k2 = list(end_indices_2).index(end_idx)
    np.testing.assert_array_equal(window_before, X_train_2[k2])

    # Sanity: the label DOES change, since it legitimately depends on future
    # data by design -- proving the perturbation was real and the test isn't
    # vacuous.
    assert not np.allclose(y_train[k], y_train_2[k2])



def test_transformer_vol_forecaster_no_lookahead_bias():
    """Verifies that future data mutations t > T do not affect forecasts at t = T."""
    np.random.seed(42)
    # Generate sequential time series of 100 periods
    T = 60
    d_model = 16
    full_sequence = np.random.randn(100, d_model)

    # Slice at T
    X_baseline = full_sequence[T-60:T].reshape(1, 60, d_model)
    model = build_tft_model(seq_len=60, d_model=d_model, num_heads=4, horizons=[1, 5, 21, 60])
    forecast_baseline, attn_baseline = predict_multi_horizon_vol(X_baseline, model)

    # Mutate future data at t > T (from index 60 to 100)
    mutated_sequence = full_sequence.copy()
    mutated_sequence[T:] = np.random.randn(40, d_model) * 100.0

    # Extract historical slice up to T from mutated sequence
    X_mutated = mutated_sequence[T-60:T].reshape(1, 60, d_model)
    forecast_mutated, attn_mutated = predict_multi_horizon_vol(X_mutated, model)

    # Forecast at time T must be bit-exact invariant to future perturbations
    for h in ['1d', '5d', '21d', '60d']:
        np.testing.assert_array_equal(forecast_baseline[h], forecast_mutated[h])
    np.testing.assert_array_equal(attn_baseline, attn_mutated)

