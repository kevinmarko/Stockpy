"""tests/test_trends_stitcher.py
==============================
Unit tests for data/trends_stitcher.py:
- GoogleTrendsStitcher (overlapping window stitching & scaling factor alignment)
- ASVICalculator (Abnormal Search Volume Index calculation & no-lookahead verification)
- FMPDataLoader (OHLCV ingestion & technical indicators EMA/MACD/RSI-14)
"""

from datetime import date
from types import SimpleNamespace
from unittest import mock

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

    def test_stitch_sparse_low_volume_a_zero_b_real(self):
        """Verify that when period A's overlap sum is near-zero but period B's is real,
        the symmetric epsilon guard passes B through unchanged (f=1.0) rather than
        scaling it down via a floored-denominator ratio (0.1/sum_b), which would have
        crushed a genuine B reading toward zero for no principled reason.
        """
        dates_a = pd.date_range("2026-01-01", "2026-01-10", freq="D")
        dates_b = pd.date_range("2026-01-05", "2026-01-15", freq="D")

        svi_a = pd.Series(0.0, index=dates_a)
        svi_b = pd.Series(50.0, index=dates_b)  # 6 overlap days, sum = 300.0

        stitched = GoogleTrendsStitcher.stitch_intervals(svi_a, svi_b)

        # f=1.0 passthrough: B's non-overlap tail is untouched.
        np.testing.assert_allclose(stitched.loc["2026-01-11":"2026-01-15"].values, 50.0, rtol=1e-5)

    def test_stitch_sparse_low_volume_a_real_b_zero(self):
        """CONFIRMED-BUG REGRESSION: mirror of the A-zero/B-real case above. Period A's
        overlap sum is real (300.0) while period B's overlap sum is near-zero (0.0), but
        period B carries a REAL, later, non-overlap reading (80.0). The old asymmetric
        formula `f = max(sum_a, 0.1) / max(sum_b, 0.1)` computed f = 300/0.1 = 3000 here,
        fabricating a ~3000x-inflated value (240000.0) for that real 80.0 reading. The
        symmetric guard must instead detect sum_b's near-zero overlap and fall back to
        f=1.0 (passthrough), leaving the real 80.0 reading untouched.
        """
        dates_a = pd.date_range("2026-01-01", "2026-01-10", freq="D")
        dates_b = pd.date_range("2026-01-05", "2026-01-15", freq="D")

        svi_a = pd.Series(50.0, index=dates_a)  # 6 overlap days, sum = 300.0
        svi_b = pd.Series(0.0, index=dates_b)
        svi_b.loc["2026-01-11":"2026-01-15"] = 80.0  # real, later, non-overlap reading

        stitched = GoogleTrendsStitcher.stitch_intervals(svi_a, svi_b)

        # f=1.0 passthrough: B's real non-overlap tail must be UNCHANGED, not blown up.
        np.testing.assert_allclose(stitched.loc["2026-01-11":"2026-01-15"].values, 80.0, rtol=1e-5)

    def test_stitch_scaling_factor_no_lookahead_bias(self):
        """CRITICAL NO-LOOKAHEAD TEST for the scaling-factor computation itself:
        the factor f is derived ONLY from the overlap-window sums. Perturbing
        values strictly OUTSIDE the overlap window (period A's pre-overlap
        history, or period B's post-overlap tail) must never change:
        (a) f itself -- observable via the overlap-blended output values, which
            are a direct function of f, and via B's f-scaled non-overlap tail;
        (b) period A's own pre-overlap values, which pass through untouched
            regardless of anything in period B.
        """
        dates_a = pd.date_range("2026-01-01", "2026-01-20", freq="D")  # A: Jan 1-20
        dates_b = pd.date_range("2026-01-15", "2026-02-10", freq="D")  # B: Jan 15 - Feb 10
        # Overlap: Jan 15-20

        rng = np.random.default_rng(7)
        svi_a = pd.Series(rng.uniform(20, 80, size=len(dates_a)), index=dates_a)
        svi_b = pd.Series(rng.uniform(20, 80, size=len(dates_b)), index=dates_b)

        stitched_clean = GoogleTrendsStitcher.stitch_intervals(svi_a, svi_b)

        # --- Perturb B's post-overlap tail (Jan 21 - Feb 10) drastically ---
        svi_b_tail_perturbed = svi_b.copy()
        svi_b_tail_perturbed.loc["2026-01-21":] = svi_b_tail_perturbed.loc["2026-01-21":] * 1000.0 + 5000.0
        stitched_tail_perturbed = GoogleTrendsStitcher.stitch_intervals(svi_a, svi_b_tail_perturbed)

        # A's own pre-overlap dates (Jan 1-14) must be byte-identical.
        np.testing.assert_allclose(
            stitched_clean.loc["2026-01-01":"2026-01-14"].values,
            stitched_tail_perturbed.loc["2026-01-01":"2026-01-14"].values,
            rtol=1e-12,
            atol=1e-12,
            err_msg="Perturbing B's post-overlap tail changed A's own pre-overlap values.",
        )
        # The overlap-blended values (Jan 15-20) are a direct function of f and
        # must also be byte-identical -- proving f was not affected by the
        # post-overlap tail perturbation.
        np.testing.assert_allclose(
            stitched_clean.loc["2026-01-15":"2026-01-20"].values,
            stitched_tail_perturbed.loc["2026-01-15":"2026-01-20"].values,
            rtol=1e-12,
            atol=1e-12,
            err_msg="Perturbing B's post-overlap tail changed the overlap-blended (f-dependent) values.",
        )

        # --- Perturb A's pre-overlap history (Jan 1-14) drastically ---
        svi_a_head_perturbed = svi_a.copy()
        svi_a_head_perturbed.loc[:"2026-01-14"] = svi_a_head_perturbed.loc[:"2026-01-14"] * 1000.0 + 5000.0
        stitched_head_perturbed = GoogleTrendsStitcher.stitch_intervals(svi_a_head_perturbed, svi_b)

        # The overlap-blended values (Jan 15-20) must be unaffected: f is computed
        # purely from the overlap-window sums, never A's pre-overlap history.
        np.testing.assert_allclose(
            stitched_clean.loc["2026-01-15":"2026-01-20"].values,
            stitched_head_perturbed.loc["2026-01-15":"2026-01-20"].values,
            rtol=1e-12,
            atol=1e-12,
            err_msg="Perturbing A's pre-overlap history changed the overlap-blended (f-dependent) values.",
        )
        # B's post-overlap tail (Jan 21 - Feb 10), scaled purely by f, must also
        # be unaffected by A's pre-overlap history.
        np.testing.assert_allclose(
            stitched_clean.loc["2026-01-21":"2026-02-10"].values,
            stitched_head_perturbed.loc["2026-01-21":"2026-02-10"].values,
            rtol=1e-12,
            atol=1e-12,
            err_msg="Perturbing A's pre-overlap history changed B's f-scaled post-overlap tail.",
        )


class TestGoogleTrendsStitcherScalingMetadata:
    """Direct unit tests for GoogleTrendsStitcher.get_scaling_metadata -- the
    single source of truth for BOTH the scaling factor `f` AND the overlap
    window (`overlap_dates`) that stitch_intervals delegates to."""

    def test_get_scaling_metadata_returns_expected_keys(self):
        dates_a = pd.date_range("2026-01-01", "2026-03-31", freq="D")
        dates_b = pd.date_range("2026-03-15", "2026-06-30", freq="D")
        svi_a = pd.Series(50.0, index=dates_a)
        svi_b = pd.Series(25.0, index=dates_b)

        meta = GoogleTrendsStitcher.get_scaling_metadata(svi_a, svi_b)

        # overlapStart/overlapEnd are deliberately NOT separate keys -- they're
        # trivially derivable from overlap_dates (see get_scaling_metadata's docstring).
        assert set(meta.keys()) == {"overlap_dates", "f"}
        assert meta["overlap_dates"][0] == pd.Timestamp("2026-03-15")
        assert meta["overlap_dates"][-1] == pd.Timestamp("2026-03-31")

    def test_get_scaling_metadata_f_matches_exact_ratio(self):
        """Hand-constructed overlap with known, unequal per-day values so `f`
        is an exact, non-trivial sum_a/sum_b ratio, not a coincidental 1.0/2.0
        from a constant series."""
        overlap_dates = pd.date_range("2026-02-01", "2026-02-05", freq="D")
        dates_a = pd.date_range("2026-01-01", "2026-02-05", freq="D")
        dates_b = pd.date_range("2026-02-01", "2026-03-01", freq="D")

        svi_a = pd.Series(10.0, index=dates_a)
        svi_b = pd.Series(10.0, index=dates_b)

        # Overlap values: A sums to 30, B sums to 60 -> f = 30/60 = 0.5
        overlap_a_vals = [2.0, 4.0, 6.0, 8.0, 10.0]  # sum = 30
        overlap_b_vals = [4.0, 8.0, 12.0, 16.0, 20.0]  # sum = 60
        svi_a.loc[overlap_dates] = overlap_a_vals
        svi_b.loc[overlap_dates] = overlap_b_vals

        meta = GoogleTrendsStitcher.get_scaling_metadata(svi_a, svi_b)

        assert pytest.approx(meta["f"], rel=1e-9) == 30.0 / 60.0
        assert pytest.approx(meta["f"], rel=1e-9) == 0.5

    def test_get_scaling_metadata_degenerate_sum_b_returns_f_one(self):
        """sum_b <= 1e-9 (e.g. period B is all-zero over the overlap window)
        must degrade to f=1.0 rather than dividing by (near-)zero."""
        dates_a = pd.date_range("2026-01-01", "2026-01-20", freq="D")
        dates_b = pd.date_range("2026-01-10", "2026-01-31", freq="D")
        svi_a = pd.Series(50.0, index=dates_a)
        svi_b = pd.Series(0.0, index=dates_b)

        meta = GoogleTrendsStitcher.get_scaling_metadata(svi_a, svi_b)

        assert meta["f"] == 1.0

    def test_get_scaling_metadata_no_overlap_raises_value_error(self):
        dates_a = pd.date_range("2026-01-01", "2026-02-28", freq="D")
        dates_b = pd.date_range("2026-03-15", "2026-04-30", freq="D")
        svi_a = pd.Series(50.0, index=dates_a)
        svi_b = pd.Series(50.0, index=dates_b)

        with pytest.raises(
            ValueError,
            match="No overlapping dates found between Period A and Period B for scaling.",
        ):
            GoogleTrendsStitcher.get_scaling_metadata(svi_a, svi_b)

    def test_get_scaling_metadata_overlap_dates_equals_index_intersection(self):
        dates_a = pd.date_range("2026-01-01", "2026-03-15", freq="D")
        dates_b = pd.date_range("2026-03-01", "2026-05-15", freq="D")
        rng = np.random.default_rng(3)
        svi_a = pd.Series(rng.uniform(5, 95, size=len(dates_a)), index=dates_a)
        svi_b = pd.Series(rng.uniform(5, 95, size=len(dates_b)), index=dates_b)

        meta = GoogleTrendsStitcher.get_scaling_metadata(svi_a, svi_b)

        expected_overlap = svi_a.index.intersection(svi_b.index)
        pd.testing.assert_index_equal(meta["overlap_dates"], expected_overlap)

    def test_stitch_intervals_and_get_scaling_metadata_agree_on_overlap_window(self):
        """Regression guard for the refactor that made get_scaling_metadata the
        single source of truth for the overlap window: stitch_intervals's own
        boundary-averaged dates must exactly match get_scaling_metadata's
        overlap_dates for the identical pair of series -- no independent
        re-derivation that could silently drift out of sync."""
        dates_a = pd.date_range("2026-01-01", "2026-03-15", freq="D")
        dates_b = pd.date_range("2026-03-01", "2026-05-15", freq="D")
        rng = np.random.default_rng(11)
        svi_a = pd.Series(rng.uniform(5, 95, size=len(dates_a)), index=dates_a)
        svi_b = pd.Series(rng.uniform(5, 95, size=len(dates_b)), index=dates_b)

        meta = GoogleTrendsStitcher.get_scaling_metadata(svi_a, svi_b)
        overlap_dates = meta["overlap_dates"]
        f = meta["f"]

        stitched = GoogleTrendsStitcher.stitch_intervals(svi_a, svi_b)

        # Every date get_scaling_metadata reports as overlapping must be an
        # exact boundary-average in the stitched output ...
        expected_overlap_values = (svi_a.loc[overlap_dates] + svi_b.loc[overlap_dates] * f) / 2.0
        np.testing.assert_allclose(
            stitched.loc[overlap_dates].values,
            expected_overlap_values.values,
            rtol=1e-12,
            atol=1e-12,
        )

        # ... and no date OUTSIDE that window is boundary-averaged: a
        # non-overlapping date unique to A must equal A's raw (unscaled)
        # value untouched.
        only_a_dates = dates_a.difference(overlap_dates)
        assert len(only_a_dates) > 0
        np.testing.assert_allclose(
            stitched.loc[only_a_dates].values,
            svi_a.loc[only_a_dates].values,
            rtol=1e-12,
            atol=1e-12,
        )

    def test_stitch_intervals_structurally_delegates_to_get_scaling_metadata(self):
        """Structural regression guard, distinct from the behavioral-equivalence test
        above: proves stitch_intervals actually CALLS get_scaling_metadata rather than
        independently reimplementing an overlap computation that merely happens to
        agree today. The behavioral test above cannot catch a reintroduced duplicate
        computation when the duplicate formula is still mathematically identical to
        get_scaling_metadata's own -- only a call-spy can."""
        dates_a = pd.date_range("2026-01-01", "2026-03-15", freq="D")
        dates_b = pd.date_range("2026-03-01", "2026-05-15", freq="D")
        rng = np.random.default_rng(13)
        svi_a = pd.Series(rng.uniform(5, 95, size=len(dates_a)), index=dates_a)
        svi_b = pd.Series(rng.uniform(5, 95, size=len(dates_b)), index=dates_b)

        with mock.patch.object(
            GoogleTrendsStitcher,
            "get_scaling_metadata",
            wraps=GoogleTrendsStitcher.get_scaling_metadata,
        ) as spy:
            GoogleTrendsStitcher.stitch_intervals(svi_a, svi_b)

        spy.assert_called_once_with(svi_a, svi_b)


class TestGoogleTrendsStitcherRawWindowReconstruction:
    """Direct unit tests for the two reusable reconstruction helpers extracted
    out of api/data_api.py::get_trends_stitch_demo -- group_raw_windows_into_series
    (TrendsStore.load_raw_windows() rows -> ordered per-window pd.Series) and
    rows_to_series (TrendsStore.get_stitched_series()'s {date, value} dicts ->
    pd.Series)."""

    def test_group_raw_windows_into_series_orders_by_earliest_date_not_window_id(self):
        """window_id is an opaque UUID, not a chronological identifier -- windows
        must be ordered by their own earliest date, not by sorting the id
        strings, and a window's rows must retain the caller-supplied order
        (TrendsStore.load_raw_windows already returns date-ascending rows)."""
        Row = SimpleNamespace
        # "zzz" would sort AFTER "aaa" alphabetically but starts earlier in time --
        # proves ordering is date-driven, not id-string-driven.
        raw_rows = [
            Row(window_id="zzz", date=date(2026, 1, 1), value=10.0),
            Row(window_id="zzz", date=date(2026, 1, 2), value=20.0),
            Row(window_id="aaa", date=date(2026, 2, 1), value=30.0),
            Row(window_id="aaa", date=date(2026, 2, 2), value=40.0),
        ]

        result = GoogleTrendsStitcher.group_raw_windows_into_series(raw_rows)

        assert [window_id for window_id, _ in result] == ["zzz", "aaa"]
        zzz_series = result[0][1]
        assert list(zzz_series.values) == [10.0, 20.0]
        assert list(zzz_series.index) == [pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-02")]
        aaa_series = result[1][1]
        assert list(aaa_series.values) == [30.0, 40.0]

    def test_group_raw_windows_into_series_empty_input(self):
        assert GoogleTrendsStitcher.group_raw_windows_into_series([]) == []

    def test_rows_to_series_preserves_order_and_values(self):
        rows = [
            {"date": date(2026, 1, 1), "value": 10.0},
            {"date": date(2026, 1, 2), "value": 22.5},
            {"date": date(2026, 1, 3), "value": 30.0},
        ]

        series = GoogleTrendsStitcher.rows_to_series(rows)

        assert list(series.values) == [10.0, 22.5, 30.0]
        assert list(series.index) == [
            pd.Timestamp("2026-01-01"),
            pd.Timestamp("2026-01-02"),
            pd.Timestamp("2026-01-03"),
        ]

    def test_rows_to_series_empty_input(self):
        series = GoogleTrendsStitcher.rows_to_series([])
        assert series.empty


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
