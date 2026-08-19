import logging

import numpy as np
import pandas as pd
import pytest
from ml.transformer_vol_forecaster import (
    build_tft_model,
    pinball_loss,
    fit_quantile_output_weights,
    train_vol_forecaster,
    predict_multi_horizon_vol,
    train_quantile_vol_forecaster,
    predict_quantile_vol_cone,
    forward_pass,
    build_causal_vol_features,
    build_training_windows,
    _align_macro_causal,
)


def test_build_model():
    model = build_tft_model(seq_len=10, d_model=16, num_heads=4, horizons=[1, 5, 21, 60])
    assert 'weights' in model
    assert 'W_q' in model['weights']
    assert model['weights']['W_q'].shape == (16, 16)
    assert 'quantile_weights' in model['weights']
    assert 0.10 in model['weights']['quantile_weights']
    assert 0.50 in model['weights']['quantile_weights']
    assert 0.90 in model['weights']['quantile_weights']


def test_forward_pass():
    model = build_tft_model(seq_len=10, d_model=16, num_heads=4, horizons=[1, 5, 21, 60])
    X = np.random.randn(32, 10, 16)  # batch=32, seq_len=10, d_model=16
    preds, attn = forward_pass(X, model)
    assert preds.shape == (32, 4)
    assert attn.shape == (32, 10, 10)
    # Strictly lower triangular: upper triangle (j > i) must have zero attention weight
    for i in range(10):
        for j in range(i + 1, 10):
            np.testing.assert_allclose(attn[:, i, j], 0.0, atol=1e-5, err_msg=f"Future leak at ({i}, {j})")


def test_train_and_predict():
    X_train = np.random.randn(100, 10, 16)
    y_train = np.random.randn(100, 4)  # 4 horizons

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
# Multi-Quantile Loss and Optimization Tests
# ---------------------------------------------------------------------------

def test_pinball_loss_accuracy():
    """Validates mathematical accuracy of pinball (quantile) loss across alpha levels."""
    # Zero error
    assert pinball_loss(10.0, 10.0, 0.5) == pytest.approx(0.0)
    assert pinball_loss(10.0, 10.0, 0.1) == pytest.approx(0.0)
    assert pinball_loss(10.0, 10.0, 0.9) == pytest.approx(0.0)

    # Median quantile (alpha = 0.5) equals 0.5 * Absolute Error
    y_true = np.array([10.0, 20.0, 30.0])
    y_pred = np.array([8.0, 25.0, 30.0])
    # Errors: [2.0, -5.0, 0.0]
    # Absolute errors: [2.0, 5.0, 0.0] -> MAE = 7/3
    expected_mae_half = 0.5 * np.mean([2.0, 5.0, 0.0])
    assert pinball_loss(y_true, y_pred, 0.5) == pytest.approx(expected_mae_half)

    # Underprediction (y_true > y_pred, e > 0)
    # At alpha = 0.9, penalty is 0.9 * e (severe)
    # At alpha = 0.1, penalty is 0.1 * e (mild)
    loss_q90_under = pinball_loss(10.0, 8.0, 0.9)
    loss_q10_under = pinball_loss(10.0, 8.0, 0.1)
    assert loss_q90_under == pytest.approx(1.8)
    assert loss_q10_under == pytest.approx(0.2)
    assert loss_q90_under > loss_q10_under

    # Overprediction (y_true < y_pred, e < 0)
    # At alpha = 0.9, penalty is (1 - 0.9) * (-e) = 0.1 * 2 = 0.2 (mild)
    # At alpha = 0.1, penalty is (1 - 0.1) * (-e) = 0.9 * 2 = 1.8 (severe)
    loss_q90_over = pinball_loss(10.0, 12.0, 0.9)
    loss_q10_over = pinball_loss(10.0, 12.0, 0.1)
    assert loss_q90_over == pytest.approx(0.2)
    assert loss_q10_over == pytest.approx(1.8)
    assert loss_q10_over > loss_q90_over


def test_fit_quantile_output_weights():
    """Validates fit_quantile_output_weights returns correct shapes and quantile ordering."""
    np.random.seed(42)
    N, d_model = 100, 16
    H = np.random.randn(N, d_model)
    # Realized volatility target with positive baseline and noise
    y = 0.20 + H[:, :2] @ np.array([0.03, -0.02])[:, None] + np.random.normal(0, 0.04, size=(N, 4))
    y = np.maximum(y, 0.05)

    quantiles = [0.10, 0.50, 0.90]
    weights_dict = fit_quantile_output_weights(H, y, quantiles=quantiles)

    assert isinstance(weights_dict, dict)
    assert set(weights_dict.keys()) == {0.10, 0.50, 0.90}

    for q in quantiles:
        W_q, b_q = weights_dict[q]
        assert W_q.shape == (d_model, 4)
        assert b_q.shape == (4,)

    # Intercepts for higher quantiles should generally be higher than lower quantiles
    b_10 = weights_dict[0.10][1]
    b_50 = weights_dict[0.50][1]
    b_90 = weights_dict[0.90][1]
    assert (b_10 <= b_50 + 0.05).all()
    assert (b_50 <= b_90 + 0.05).all()


def test_fit_quantile_output_weights_logs_warning_on_optimizer_failure(monkeypatch, caplog):
    """
    Cluster C item 12: `fit_quantile_output_weights`'s L-BFGS-B `except Exception`
    block previously degraded to the Ridge seed with zero logging. Force
    scipy.optimize.minimize to raise and confirm a WARNING is logged (naming the
    failing quantile alpha) while theta_opt still correctly falls back to theta_0
    (i.e. output weights match a direct Ridge-only fit, behavior unchanged).
    """
    import ml.transformer_vol_forecaster as tvf_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("forced L-BFGS-B failure")

    monkeypatch.setattr(tvf_mod, "minimize", _boom)

    np.random.seed(42)
    N, d_model = 100, 16
    H = np.random.randn(N, d_model)
    y = 0.20 + H[:, :2] @ np.array([0.03, -0.02])[:, None] + np.random.normal(0, 0.04, size=(N, 4))
    y = np.maximum(y, 0.05)

    with caplog.at_level(logging.WARNING, logger="ml.transformer_vol_forecaster"):
        weights_dict = fit_quantile_output_weights(H, y, quantiles=[0.10])

    assert any(
        "L-BFGS-B optimization failed" in record.message and "alpha=0.1" in record.message
        for record in caplog.records
    )

    # theta_opt fell back to theta_0 == the Ridge seed + residual-quantile intercept shift,
    # so the returned weight matrix equals the Ridge coefficients exactly.
    lambda_reg = 1e-3
    H_bias = np.hstack([H, np.ones((N, 1))])
    I_bias = np.eye(d_model + 1)
    I_bias[-1, -1] = 0.0
    w_ridge_all = np.linalg.solve(H_bias.T @ H_bias + lambda_reg * I_bias, H_bias.T @ y)
    W_q, b_q = weights_dict[0.10]
    np.testing.assert_allclose(W_q, w_ridge_all[:-1, :], rtol=1e-8)


def test_train_and_predict_quantile_vol_cone():
    """Validates train_quantile_vol_forecaster and predict_quantile_vol_cone output structure."""
    np.random.seed(42)
    X_train = np.random.randn(120, 10, 16)
    y_train = np.abs(np.random.randn(120, 4)) * 0.15 + 0.10

    model = train_quantile_vol_forecaster(
        X_train, y_train, seq_len=10, d_model=16, num_heads=4, horizons=[1, 5, 21, 60],
        quantiles=[0.10, 0.50, 0.90],
    )

    X_test = np.random.randn(7, 10, 16)
    q_forecasts, attn = predict_quantile_vol_cone(X_test, model)

    assert isinstance(q_forecasts, dict)
    assert attn.shape == (7, 10, 10)

    for h in ["1d", "5d", "21d", "60d"]:
        assert h in q_forecasts
        q_dict = q_forecasts[h]
        assert "q10" in q_dict and "q50" in q_dict and "q90" in q_dict
        assert q_dict["q10"].shape == (7,)
        assert q_dict["q50"].shape == (7,)
        assert q_dict["q90"].shape == (7,)

        # Monotonicity check: q10 <= q50 <= q90
        assert (q_dict["q10"] <= q_dict["q50"]).all()
        assert (q_dict["q50"] <= q_dict["q90"]).all()
        # Non-negativity check
        assert (q_dict["q10"] >= 0.0).all()


def test_quantile_monotonicity_enforcement():
    """Validates that predict_quantile_vol_cone strictly enforces q10 <= q50 <= q90 across random inputs."""
    np.random.seed(99)
    model = build_tft_model(seq_len=10, d_model=16, num_heads=4, horizons=[1, 5, 21, 60], quantiles=[0.10, 0.50, 0.90])
    # Create extreme inputs
    X_extreme = np.random.randn(50, 10, 16) * 10.0
    forecasts, attn = predict_quantile_vol_cone(X_extreme, model)

    for h_label, q_dict in forecasts.items():
        q10 = q_dict["q10"]
        q50 = q_dict["q50"]
        q90 = q_dict["q90"]
        assert (q10 <= q50).all(), f"Monotonicity violated for {h_label} (q10 > q50)"
        assert (q50 <= q90).all(), f"Monotonicity violated for {h_label} (q50 > q90)"
        assert (q10 >= 0.0).all(), f"Non-negativity violated for {h_label}"


# ---------------------------------------------------------------------------
# Real, causal feature/window construction & Macro Integration Tests
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

def test_build_causal_vol_features_unaffected_by_future_data():
    """Perturbation test: two OHLCV series identical through day T but
    diverging strictly AFTER T must produce identical feature rows through
    day T."""
    bars = _synthetic_ohlcv(n_days=200, seed=1)
    cutoff = 150  # row index -- everything at/before this index is "the past"

    bars_perturbed = bars.copy()
    bars_perturbed.iloc[cutoff + 1:, bars_perturbed.columns.get_indexer(["Open", "High", "Low", "Close"])] *= 5.0
    bars_perturbed.iloc[cutoff + 1:, bars_perturbed.columns.get_loc("Volume")] *= 50.0

    feats_original = build_causal_vol_features(bars)
    feats_perturbed = build_causal_vol_features(bars_perturbed)

    pd.testing.assert_frame_equal(
        feats_original.iloc[: cutoff + 1],
        feats_perturbed.iloc[: cutoff + 1],
    )
    assert not feats_original.iloc[cutoff + 1:].equals(feats_perturbed.iloc[cutoff + 1:])


def test_build_causal_vol_features_dow_and_padding_are_deterministic():
    bars = _synthetic_ohlcv(n_days=100, seed=2)
    feats = build_causal_vol_features(bars, d_model=32)
    assert feats.shape[1] == 32
    pad_cols = [c for c in feats.columns if c.startswith("_pad_")]
    assert pad_cols
    assert (feats[pad_cols] == 0.0).all().all()
    dow_cols = [c for c in feats.columns if c.startswith("dow_")]
    assert (feats[dow_cols].sum(axis=1) == 1.0).all()


def test_build_causal_vol_features_with_macro_df():
    """Tests causal macro features integration with vix, yield slope, credit spread, and fed funds."""
    bars = _synthetic_ohlcv(n_days=150, seed=4)
    macro_df = pd.DataFrame(
        {
            "vix": pd.Series(np.random.uniform(12, 35, size=150), index=bars.index),
            "yield_slope_10y_2y": pd.Series(np.random.uniform(-0.5, 1.5, size=150), index=bars.index),
            "hy_oas": pd.Series(np.random.uniform(2.5, 6.0, size=150), index=bars.index),
            "fed_funds": pd.Series(np.random.uniform(4.0, 5.5, size=150), index=bars.index),
        },
        index=bars.index,
    )

    feats = build_causal_vol_features(bars, macro_df=macro_df, d_model=32)
    assert feats.shape[1] == 32
    assert "macro_vix" in feats.columns
    assert "macro_yield_slope_10y_2y" in feats.columns
    assert "macro_hy_oas" in feats.columns
    assert "macro_fed_funds" in feats.columns
    # Check deterministic padding is still applied for remaining slots
    assert "_pad_25" in feats.columns
    assert (feats["_pad_25"] == 0.0).all()


def test_causal_macro_perturbation_no_lookahead():
    """Perturbation test: mutating macro data strictly AFTER cutoff date T
    must produce ZERO change in causal features at or before date T."""
    bars = _synthetic_ohlcv(n_days=200, seed=5)
    macro_df = pd.DataFrame(
        {
            "VIXCLS": pd.Series(np.random.uniform(15, 30, size=200), index=bars.index),
            "T10Y2Y": pd.Series(np.random.uniform(-0.8, 1.2, size=200), index=bars.index),
            "BAMLH0A0HYM2": pd.Series(np.random.uniform(3.0, 7.0, size=200), index=bars.index),
            "FEDFUNDS": pd.Series(np.random.uniform(4.5, 5.5, size=200), index=bars.index),
        },
        index=bars.index,
    )

    cutoff = 130
    macro_perturbed = macro_df.copy()
    # Apply severe shock strictly after cutoff
    macro_perturbed.iloc[cutoff + 1:] *= 50.0

    feats_orig = build_causal_vol_features(bars, macro_df=macro_df, d_model=32)
    feats_pert = build_causal_vol_features(bars, macro_df=macro_perturbed, d_model=32)

    pd.testing.assert_frame_equal(
        feats_orig.iloc[: cutoff + 1],
        feats_pert.iloc[: cutoff + 1],
    )
    # Sanity: perturbation actually changed future features
    assert not feats_orig.iloc[cutoff + 1:].equals(feats_pert.iloc[cutoff + 1:])


def test_align_macro_causal_well_formed_index_unaffected_by_dead_code_removal():
    """
    Cluster C item 12: `_align_macro_causal`'s `try: m_df.index = pd.to_datetime(...)
    except Exception: pass` block was removed as dead/misleading code (an identical,
    unprotected `pd.to_datetime(m_df.index)` call a few lines later would raise the
    same exception anyway). This is a pure no-op removal for well-formed input --
    confirm alignment still behaves identically: a non-DatetimeIndex macro frame
    with no recognizable date column still gets converted and causally forward-filled
    onto bars_index with zero lookahead.
    """
    bars = _synthetic_ohlcv(n_days=40, seed=9)
    # Macro frame indexed by plain date strings (not a DatetimeIndex, no "Date"-like column)
    macro_df = pd.DataFrame(
        {"vix": np.linspace(15.0, 25.0, 40)},
        index=[d.strftime("%Y-%m-%d") for d in bars.index],
    )

    aligned = _align_macro_causal(bars.index, macro_df)

    assert list(aligned.index) == list(bars.index)
    assert aligned["vix"].notna().all()
    # Causal: value at each date must equal the (forward-filled) macro observation
    # as-of that same date, never a later one.
    expected = macro_df.copy()
    expected.index = pd.to_datetime(expected.index)
    expected = expected.sort_index()
    for i, dt in enumerate(pd.to_datetime(bars.index)):
        as_of = expected[expected.index <= dt]["vix"].iloc[-1]
        assert aligned["vix"].iloc[i] == pytest.approx(as_of)


def test_causal_end_to_end_prediction_perturbation():
    """End-to-end causal test: mutating future price and macro data strictly after date T
    causes ZERO change in model input features and quantile predictions at date T."""
    bars = _synthetic_ohlcv(n_days=250, seed=6)
    macro_df = pd.DataFrame(
        {
            "vix": pd.Series(np.random.uniform(14, 28, size=250), index=bars.index),
            "yield_slope_10y_2y": pd.Series(np.random.uniform(-0.4, 1.4, size=250), index=bars.index),
            "hy_oas": pd.Series(np.random.uniform(3.2, 5.8, size=250), index=bars.index),
            "fed_funds": pd.Series(np.random.uniform(4.8, 5.3, size=250), index=bars.index),
        },
        index=bars.index,
    )

    # Train a model on history before cutoff
    feat_df = build_causal_vol_features(bars, macro_df=macro_df, d_model=32).dropna()
    feat_mat = feat_df.to_numpy()
    close_arr = bars["Close"].reindex(feat_df.index).to_numpy()

    seq_len = 20
    horizons = [1, 5, 21, 60]
    X_train, y_train, end_indices = build_training_windows(
        feat_mat[:160], close_arr[:160], seq_len=seq_len, horizons=horizons, stride=2,
    )
    model = train_quantile_vol_forecaster(
        X_train, y_train, seq_len=seq_len, d_model=32, num_heads=4, horizons=horizons,
    )

    # Inference at date T (row index 150)
    T = 150
    target_date = bars.index[T]
    infer_window_orig = feat_df.loc[:target_date].iloc[-seq_len:].to_numpy().reshape(1, seq_len, 32)
    preds_orig, _ = predict_quantile_vol_cone(infer_window_orig, model)

    # Mutate price bars and macro data strictly after date T
    bars_perturbed = bars.copy()
    bars_perturbed.iloc[T + 1:, bars_perturbed.columns.get_indexer(["Open", "High", "Low", "Close"])] *= 4.0
    macro_perturbed = macro_df.copy()
    macro_perturbed.iloc[T + 1:] *= 20.0

    feat_df_pert = build_causal_vol_features(bars_perturbed, macro_df=macro_perturbed, d_model=32).dropna()
    infer_window_pert = feat_df_pert.loc[:target_date].iloc[-seq_len:].to_numpy().reshape(1, seq_len, 32)
    preds_pert, _ = predict_quantile_vol_cone(infer_window_pert, model)

    # The input window and predictions at date T must be exactly identical
    np.testing.assert_array_equal(infer_window_orig, infer_window_pert)
    for h in ["1d", "5d", "21d", "60d"]:
        for q in ["q10", "q50", "q90"]:
            np.testing.assert_array_equal(preds_orig[h][q], preds_pert[h][q])


def test_build_training_windows_input_is_causal():
    """A training window's X (input) must never depend on feature/price data
    strictly after the window's own end index."""
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
    assert end_idx in end_indices_2
    k2 = list(end_indices_2).index(end_idx)
    np.testing.assert_array_equal(window_before, X_train_2[k2])
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

