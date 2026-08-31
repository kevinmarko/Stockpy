"""tests/test_trends_stitcher.py
==============================
Unit tests for data/trends_stitcher.py:
- GoogleTrendsStitcher (overlapping window stitching & scaling factor alignment)
- ASVICalculator (Abnormal Search Volume Index calculation & no-lookahead verification)
- FMPDataLoader (OHLCV ingestion & technical indicators EMA/MACD/RSI-14)
"""

import numpy as np
import pandas as pd
import pytest

from data.trends_stitcher import (
    GoogleTrendsStitcher,
    ASVICalculator,
    FMPDataLoader,
)


class TestGoogleTrendsStitcher:
    """Tests for the overlapping window stitching algorithm."""

    def test_stitch_intervals_exact_scaling(self):
        """Verify that when period B is scaled down by factor 0.5,
        the stitcher computes f=2.0 and restores period B to period A's scale.
        """
        dates_a = pd.date_range("2026-01-01", "2026-03-31", freq="D")
        dates_b = pd.date_range("2026-03-15", "2026-06-30", freq="D")

        svi_a = pd.Series(50.0, index=dates_a)
        # Period B in its own frame has values = 25.0 (scale 0.5 of A)
        svi_b = pd.Series(25.0, index=dates_b)

        stitched = GoogleTrendsStitcher.stitch_intervals(svi_a, svi_b)

        # Expected scaling factor: 50.0 / 25.0 = 2.0
        # For non-overlapping dates in period B (after March 31): value should be 25.0 * 2.0 = 50.0
        non_overlap_b = stitched.loc["2026-04-01":"2026-06-30"]
        np.testing.assert_allclose(non_overlap_b.values, 50.0, rtol=1e-5)

        # Overlapping dates (March 15 - March 31): average of 50.0 and 25.0*2.0 = 50.0
        overlap = stitched.loc["2026-03-15":"2026-03-31"]
        np.testing.assert_allclose(overlap.values, 50.0, rtol=1e-5)

    def test_stitch_intervals_no_overlap_raises_value_error(self):
        """Verify that disjoint intervals without overlap raise ValueError."""
        dates_a = pd.date_range("2026-01-01", "2026-02-28", freq="D")
        dates_b = pd.date_range("2026-03-15", "2026-04-30", freq="D")

        svi_a = pd.Series(50.0, index=dates_a)
        svi_b = pd.Series(50.0, index=dates_b)

        with pytest.raises(ValueError, match="No overlapping dates found"):
            GoogleTrendsStitcher.stitch_intervals(svi_a, svi_b)

    def test_stitch_multiple_intervals(self):
        """Verify that multi-interval stitching correctly chains 4 overlapping periods."""
        p1 = pd.Series(100.0, index=pd.date_range("2026-01-01", "2026-03-15", freq="D"))
        p2 = pd.Series(50.0, index=pd.date_range("2026-03-01", "2026-05-15", freq="D"))  # 0.5x
        p3 = pd.Series(25.0, index=pd.date_range("2026-05-01", "2026-07-15", freq="D"))  # 0.25x
        p4 = pd.Series(12.5, index=pd.date_range("2026-07-01", "2026-09-15", freq="D"))  # 0.125x

        stitched = GoogleTrendsStitcher.stitch_multiple_intervals([p1, p2, p3, p4])

        # All parts should be scaled to the 100.0 baseline
        assert len(stitched) == len(pd.date_range("2026-01-01", "2026-09-15", freq="D"))
        np.testing.assert_allclose(stitched.loc["2026-09-01":"2026-09-15"].values, 100.0, rtol=1e-5)

    def test_stitch_empty_inputs(self):
        """Verify edge cases where one or both intervals are empty."""
        s = pd.Series(10.0, index=pd.date_range("2026-01-01", "2026-01-10", freq="D"))
        empty = pd.Series(dtype=float)

        res_a = GoogleTrendsStitcher.stitch_intervals(s, empty)
        assert len(res_a) == len(s)

        res_b = GoogleTrendsStitcher.stitch_intervals(empty, s)
        assert len(res_b) == len(s)

        res_empty = GoogleTrendsStitcher.stitch_multiple_intervals([])
        assert res_empty.empty

    def test_stitch_both_zero_overlap(self):
        """Verify that when both overlap regions sum to zero, factor=1.0 is used."""
        dates_a = pd.date_range("2026-01-01", "2026-01-10", freq="D")
        dates_b = pd.date_range("2026-01-05", "2026-01-15", freq="D")
        
        svi_a = pd.Series(0.0, index=dates_a)
        svi_b = pd.Series(0.0, index=dates_b)
        
        stitched = GoogleTrendsStitcher.stitch_intervals(svi_a, svi_b)
        
        assert (stitched == 0.0).all()

    def test_stitch_sparse_low_volume(self):
        """Verify that when one overlap region is zero, it substitutes 0.1 for the sum to compute ratio."""
        dates_a = pd.date_range("2026-01-01", "2026-01-10", freq="D")
        dates_b = pd.date_range("2026-01-05", "2026-01-15", freq="D")
        
        svi_a = pd.Series(0.0, index=dates_a)
        svi_b = pd.Series(50.0, index=dates_b) # 6 overlap days, sum = 300.0
        
        stitched = GoogleTrendsStitcher.stitch_intervals(svi_a, svi_b)
        
        expected_f = 0.1 / 300.0
        np.testing.assert_allclose(stitched.loc["2026-01-11":"2026-01-15"].values, 50.0 * expected_f, rtol=1e-5)


class TestASVICalculator:
    """Tests for Abnormal Search Volume Index computation & no-lookahead causality."""

    def test_asvi_exact_reference_spike(self):
        """Verify ASVI calculation on a known baseline and spike.
        Baseline: 100 days of constant 10.0 SVI.
        Spike day: SVI = 50.0.
        Expected ASVI = ln(50) - ln(10) = ln(5) ≈ 1.609438.
        """
        dates = pd.date_range("2026-01-01", "2026-05-01", freq="D")
        svi = pd.Series(10.0, index=dates)
        spike_date = "2026-04-15"
        svi.loc[spike_date] = 50.0

        asvi = ASVICalculator.compute_asvi(svi, lookback_weeks=8)

        expected_asvi_spike = np.log(50.0) - np.log(10.0)
        assert pytest.approx(asvi.loc[spike_date], rel=1e-4) == expected_asvi_spike

        # Baseline days before the spike should have ASVI ≈ ln(10) - ln(10) = 0.0
        assert pytest.approx(asvi.loc["2026-03-01"], abs=1e-4) == 0.0

    def test_asvi_zero_clipping_epsilon_safety(self):
        """Verify that zero SVI values do not produce inf or NaN."""
        dates = pd.date_range("2026-01-01", "2026-03-31", freq="D")
        svi = pd.Series(0.0, index=dates)
        svi.iloc[10:20] = 20.0

        asvi = ASVICalculator.compute_asvi(svi, lookback_weeks=4, epsilon=0.1)
        assert not asvi.isna().any()
        assert not np.isinf(asvi.values).any()

    def test_asvi_no_lookahead_bias_perturbation(self):
        """CRITICAL NO-LOOKAHEAD TEST:
        Mutating future SVI values at t > T MUST NOT alter past or present ASVI values at t <= T.
        """
        dates = pd.date_range("2026-01-01", "2026-06-30", freq="D")
        rng = np.random.default_rng(42)
        svi_base = pd.Series(rng.uniform(10, 80, size=len(dates)), index=dates)

        # Baseline ASVI
        asvi_clean = ASVICalculator.compute_asvi(svi_base, lookback_weeks=8)

        cutoff_date = "2026-04-15"

        # Perturb future data (t > cutoff_date) drastically
        svi_perturbed = svi_base.copy()
        svi_perturbed.loc["2026-04-16":] = svi_perturbed.loc["2026-04-16":] * 100.0 + 500.0

        asvi_perturbed = ASVICalculator.compute_asvi(svi_perturbed, lookback_weeks=8)

        # The ASVI up to and including cutoff_date MUST be identical to machine precision
        clean_prefix = asvi_clean.loc[:cutoff_date]
        perturbed_prefix = asvi_perturbed.loc[:cutoff_date]

        np.testing.assert_allclose(
            clean_prefix.values,
            perturbed_prefix.values,
            rtol=1e-12,
            atol=1e-12,
            err_msg="Lookahead bias detected! Modifying future SVI changed historical ASVI.",
        )


class TestFMPDataLoader:
    """Tests for FMP data loader and technical indicators calculation."""

    def test_fetch_historical_ohlcv(self):
        loader = FMPDataLoader()
        df = loader.fetch_historical_ohlcv("AAPL", start_date="2026-01-01", end_date="2026-03-31")

        assert not df.empty
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert (df["high"] >= df["low"]).all()
        assert (df["close"] > 0).all()
        assert (df["volume"] > 0).all()

    def test_compute_technical_indicators(self):
        loader = FMPDataLoader()
        df_raw = loader.fetch_historical_ohlcv("NVDA", start_date="2026-01-01", end_date="2026-06-30")
        df_tech = loader.compute_technical_indicators(df_raw)

        expected_cols = ["ema_12", "ema_26", "macd", "macd_signal", "macd_hist", "rsi_14"]
        for col in expected_cols:
            assert col in df_tech.columns, f"Missing indicator column: {col}"
            assert not df_tech[col].isna().any(), f"NaN values found in {col}"

        # RSI must be strictly bounded in [0, 100]
        assert (df_tech["rsi_14"] >= 0.0).all()
        assert (df_tech["rsi_14"] <= 100.0).all()

        # MACD math definition: macd = ema_12 - ema_26
        np.testing.assert_allclose(
            df_tech["macd"].values,
            (df_tech["ema_12"] - df_tech["ema_26"]).values,
            rtol=1e-6,
        )
