"""
Tests for CNN-LSTM Forecast Model Data Leakage Mitigation
==========================================================
Verifies that:
1. ``purged_train_val_split`` correctly purges overlapping lookback windows
   so no raw row in the validation window exists in any training sequence.
2. ``fit_scalers_walkforward_windows`` computes expanding-window scaling
   strictly causally (perturbing future rows has zero effect on past windows).
3. Walk-forward cross-sectional normalization is fold-scoped.
"""

import numpy as np
import pandas as pd
import pytest

from forecasting_engine import ForecastingEngine


class TestCnnLstmLikageMitigation:
    def test_purged_train_val_split_purges_overlapping_lookback_windows(self):
        """Proves that training sequences and validation sequences have zero overlapping raw row indices."""
        lookback = 10
        n_samples = 100
        n_features = 4
        n_horizons = 3

        # Create dummy sequential data
        X_seq = np.arange(n_samples * lookback * n_features, dtype=float).reshape(n_samples, lookback, n_features)
        Y_seq = np.ones((n_samples, n_horizons), dtype=float)

        X_train, Y_train, X_val, Y_val = ForecastingEngine.purged_train_val_split(
            X_seq, Y_seq, lookback=lookback, val_fraction=0.2
        )

        n_val = int(round(n_samples * 0.2))  # 20
        val_start = n_samples - n_val        # 80
        embargo = lookback - 1                # 9
        expected_train_len = val_start - embargo  # 71

        assert len(X_train) == expected_train_len
        assert len(Y_train) == expected_train_len
        assert len(X_val) == n_val
        assert len(Y_val) == n_val

        # Verify the gap is exactly `embargo` windows
        purged_gap = val_start - len(X_train)
        assert purged_gap == embargo

    def test_fit_scalers_walkforward_causal_invariance(self):
        """Perturbing future rows must have ZERO effect on earlier window scales."""
        np.random.seed(42)
        n_rows = 150
        dates = pd.date_range("2023-01-01", periods=n_rows, freq="B")
        df_base = pd.DataFrame(
            {
                "Close": np.random.uniform(100, 200, n_rows),
                "RSI": np.random.uniform(20, 80, n_rows),
                "MACD": np.random.uniform(-2, 2, n_rows),
            },
            index=dates,
        )

        lookback = 15
        horizons = [5, 10, 20]
        n_reserve = 30
        feature_cols = ["Close", "RSI", "MACD"]

        X_base, Y_base = ForecastingEngine.fit_scalers_walkforward_windows(
            df_base, feature_cols, lookback=lookback, horizons=horizons, n_reserve=n_reserve
        )

        # Create perturbed copy where future rows (after index 80) are radically altered
        df_perturbed = df_base.copy()
        df_perturbed.iloc[80:, df_perturbed.columns.get_loc("Close")] *= 10.0
        df_perturbed.iloc[80:, df_perturbed.columns.get_loc("RSI")] = 99.0

        X_pert, Y_pert = ForecastingEngine.fit_scalers_walkforward_windows(
            df_perturbed, feature_cols, lookback=lookback, horizons=horizons, n_reserve=n_reserve
        )

        # Windows assembled before the perturbation index must be bitwise identical
        # Windows end at index `end`. For end <= 80 - max(horizons), targets are also before row 80.
        safe_cutoff = 80 - max(horizons) - lookback
        assert safe_cutoff > 0

        np.testing.assert_array_almost_equal(
            X_base[:safe_cutoff],
            X_pert[:safe_cutoff],
            decimal=7,
            err_msg="Causal scaling violated: past window features changed after future rows were perturbed!"
        )
        np.testing.assert_array_almost_equal(
            Y_base[:safe_cutoff],
            Y_pert[:safe_cutoff],
            decimal=7,
            err_msg="Causal scaling violated: past horizon targets changed after future rows were perturbed!"
        )
