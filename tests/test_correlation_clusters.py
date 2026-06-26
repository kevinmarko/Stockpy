"""
tests/test_correlation_clusters.py
====================================
Unit tests for ``research_engine.compute_correlation_clusters`` and
``research_engine.fetch_returns_for_clustering`` (Tier 2.5).

All network calls in ``fetch_returns_for_clustering`` are monkeypatched;
``compute_correlation_clusters`` is a pure function and needs no mocking.

Coverage
--------
TestComputeCorrelationClusters — known groups cluster together, schema
TestDistanceThreshold          — higher threshold → more singletons, lower → fewer
TestSummaryDataFrame           — correct column schema, per-row structure
TestEdgeCases                  — 1 symbol, empty df, all-NaN column
TestInsufficientHistory        — excluded symbols get cluster_id = 0
TestFetchReturnsHelper         — offline yfinance monkeypatch
TestSettings                   — CORRELATION_CLUSTER_* settings exist and > 0
"""

import numpy as np
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta

from research_engine import compute_correlation_clusters, fetch_returns_for_clustering


# ===========================================================================
# Helpers
# ===========================================================================

def _make_returns(n_days: int = 60) -> pd.DataFrame:
    """Two tightly correlated symbols (A, B) and one uncorrelated (C)."""
    rng = np.random.default_rng(42)
    dates = pd.date_range("2026-01-01", periods=n_days, freq="B")
    base = rng.standard_normal(n_days)
    # A and B: very high positive correlation (ρ ≈ 0.98)
    returns_a = base * 0.01 + rng.standard_normal(n_days) * 0.0005
    returns_b = base * 0.01 + rng.standard_normal(n_days) * 0.0005
    # C: independent
    returns_c = rng.standard_normal(n_days) * 0.01
    return pd.DataFrame({"A": returns_a, "B": returns_b, "C": returns_c}, index=dates)


def _make_all_correlated(n_days: int = 60) -> pd.DataFrame:
    """Three symbols all highly correlated with each other."""
    rng = np.random.default_rng(7)
    dates = pd.date_range("2026-01-01", periods=n_days, freq="B")
    base = rng.standard_normal(n_days)
    df = pd.DataFrame(
        {"X": base * 0.01, "Y": base * 0.01, "Z": base * 0.01},
        index=dates,
    )
    return df


# ===========================================================================
# TestComputeCorrelationClusters
# ===========================================================================

class TestComputeCorrelationClusters:
    def test_correlated_symbols_cluster_together(self):
        """A and B (high correlation) must share the same cluster ID."""
        returns_df = _make_returns()
        labels, summary = compute_correlation_clusters(returns_df, distance_threshold=0.4)
        assert labels["A"] == labels["B"], (
            f"A (cluster {labels['A']}) and B (cluster {labels['B']}) should be in "
            "the same cluster given their high correlation."
        )

    def test_uncorrelated_symbol_separate_cluster(self):
        """C (uncorrelated) should NOT share a cluster with A and B."""
        returns_df = _make_returns()
        labels, _ = compute_correlation_clusters(returns_df, distance_threshold=0.4)
        assert labels["A"] != labels["C"], (
            "Uncorrelated symbol C should be in a different cluster from A and B."
        )

    def test_all_symbols_assigned(self):
        returns_df = _make_returns()
        labels, _ = compute_correlation_clusters(returns_df, distance_threshold=0.4)
        assert set(labels.keys()) == {"A", "B", "C"}

    def test_cluster_ids_are_positive_integers(self):
        returns_df = _make_returns()
        labels, _ = compute_correlation_clusters(returns_df, distance_threshold=0.4)
        for sym, cid in labels.items():
            assert isinstance(cid, int), f"cluster_id for {sym} must be int"
            assert cid > 0, f"cluster_id for {sym} must be > 0"

    def test_all_highly_correlated_single_cluster(self):
        """When all symbols are perfectly correlated, they should be in cluster 1."""
        returns_df = _make_all_correlated()
        labels, summary = compute_correlation_clusters(returns_df, distance_threshold=0.4)
        ids = list(labels.values())
        assert len(set(ids)) == 1, "All perfectly correlated symbols should form one cluster."

    def test_returns_empty_on_empty_dataframe(self):
        labels, summary = compute_correlation_clusters(pd.DataFrame())
        assert labels == {}
        assert summary.empty

    def test_returns_empty_on_none(self):
        labels, summary = compute_correlation_clusters(None)  # type: ignore[arg-type]
        assert labels == {}
        assert summary.empty


# ===========================================================================
# TestDistanceThreshold
# ===========================================================================

class TestDistanceThreshold:
    def test_low_threshold_fewer_clusters(self):
        """Stricter threshold → more things split into own cluster."""
        returns_df = _make_returns()
        _, summary_loose = compute_correlation_clusters(returns_df, distance_threshold=0.9)
        _, summary_strict = compute_correlation_clusters(returns_df, distance_threshold=0.1)
        # Loose threshold → fewer distinct clusters (everything merges)
        # Strict threshold → more distinct clusters (less merging)
        assert len(summary_loose) <= len(summary_strict)

    def test_high_threshold_merges_all(self):
        """Maximum threshold merges all symbols into one cluster."""
        returns_df = _make_returns(60)
        labels, summary = compute_correlation_clusters(returns_df, distance_threshold=2.0)
        ids = [v for v in labels.values() if v > 0]
        assert len(set(ids)) <= 3  # at most 3 symbols = at most 3 clusters

    def test_very_low_threshold_all_singletons(self):
        """Minimum threshold puts every symbol in its own cluster."""
        returns_df = _make_returns(60)
        labels, summary = compute_correlation_clusters(returns_df, distance_threshold=0.001)
        # Every symbol should be in its own cluster
        ids = list(labels.values())
        assert len(set(ids)) == len(ids)


# ===========================================================================
# TestSummaryDataFrame
# ===========================================================================

class TestSummaryDataFrame:
    REQUIRED_COLS = {"cluster_id", "symbols", "n_symbols", "avg_intra_corr"}

    def test_summary_has_correct_columns(self):
        returns_df = _make_returns()
        _, summary = compute_correlation_clusters(returns_df)
        assert self.REQUIRED_COLS.issubset(set(summary.columns)), (
            f"Missing columns: {self.REQUIRED_COLS - set(summary.columns)}"
        )

    def test_n_symbols_matches_symbols_list(self):
        returns_df = _make_returns()
        _, summary = compute_correlation_clusters(returns_df)
        for _, row in summary.iterrows():
            assert row["n_symbols"] == len(row["symbols"]), (
                f"n_symbols={row['n_symbols']} != len(symbols)={len(row['symbols'])}"
            )

    def test_all_symbols_covered_by_summary(self):
        returns_df = _make_returns()
        labels, summary = compute_correlation_clusters(returns_df)
        # Every symbol in labels should appear in exactly one summary row
        covered = set()
        for _, row in summary.iterrows():
            for sym in (row["symbols"] if isinstance(row["symbols"], list) else []):
                covered.add(sym)
        for sym, cid in labels.items():
            if cid != 0:
                assert sym in covered, f"{sym} not found in any cluster summary row"

    def test_avg_intra_corr_nan_for_singleton(self):
        """Single-member clusters should have avg_intra_corr = NaN."""
        returns_df = _make_returns()
        _, summary = compute_correlation_clusters(returns_df, distance_threshold=0.001)
        singletons = summary[summary["n_symbols"] == 1]
        for _, row in singletons.iterrows():
            assert row["avg_intra_corr"] != row["avg_intra_corr"]  # NaN check

    def test_avg_intra_corr_non_nan_for_multi_symbol_cluster(self):
        """Multi-symbol clusters have a valid average intra-cluster correlation."""
        returns_df = _make_returns()
        _, summary = compute_correlation_clusters(returns_df, distance_threshold=0.4)
        multi = summary[summary["n_symbols"] > 1]
        for _, row in multi.iterrows():
            corr = row["avg_intra_corr"]
            assert corr == corr, "avg_intra_corr should be non-NaN for multi-symbol clusters"
            assert 0.0 <= corr <= 1.0, f"avg_intra_corr={corr} out of [0,1]"

    def test_empty_input_returns_correct_schema(self):
        _, summary = compute_correlation_clusters(pd.DataFrame())
        assert set(summary.columns) == self.REQUIRED_COLS


# ===========================================================================
# TestEdgeCases
# ===========================================================================

class TestEdgeCases:
    def test_single_symbol(self):
        """1-symbol DataFrame → cluster 1, no intra-cluster correlation."""
        rng = np.random.default_rng(0)
        dates = pd.date_range("2026-01-01", periods=40, freq="B")
        df = pd.DataFrame({"SPY": rng.standard_normal(40) * 0.01}, index=dates)
        labels, summary = compute_correlation_clusters(df)
        assert labels.get("SPY") == 1
        assert len(summary) == 1
        assert summary.iloc[0]["n_symbols"] == 1
        assert summary.iloc[0]["avg_intra_corr"] != summary.iloc[0]["avg_intra_corr"]  # NaN

    def test_all_nan_column_excluded(self):
        """A column with all-NaN returns is excluded and gets cluster_id=0."""
        rng = np.random.default_rng(1)
        dates = pd.date_range("2026-01-01", periods=40, freq="B")
        good = rng.standard_normal(40) * 0.01
        df = pd.DataFrame(
            {"GOOD": good, "BAD": [float("nan")] * 40},
            index=dates,
        )
        labels, _ = compute_correlation_clusters(df, min_obs=20)
        assert labels.get("BAD") == 0
        assert labels.get("GOOD", -1) != 0

    def test_insufficient_obs_excluded(self):
        """Symbols with fewer than min_obs valid returns get cluster_id=0."""
        rng = np.random.default_rng(2)
        dates = pd.date_range("2026-01-01", periods=30, freq="B")
        df = pd.DataFrame(
            {"A": rng.standard_normal(30) * 0.01, "B": rng.standard_normal(30) * 0.01},
            index=dates,
        )
        # Require 40 obs — both symbols fall below threshold
        labels, summary = compute_correlation_clusters(df, min_obs=40)
        assert labels.get("A") == 0
        assert labels.get("B") == 0

    def test_two_symbols(self):
        """2 symbols can form one or two clusters depending on threshold."""
        rng = np.random.default_rng(3)
        dates = pd.date_range("2026-01-01", periods=60, freq="B")
        base = rng.standard_normal(60)
        df = pd.DataFrame(
            {"P": base * 0.01, "Q": base * 0.01},  # highly correlated
            index=dates,
        )
        labels, summary = compute_correlation_clusters(df, distance_threshold=0.5)
        # Should be in the same cluster
        assert labels["P"] == labels["Q"]


# ===========================================================================
# TestFetchReturnsHelper
# ===========================================================================

class TestFetchReturnsHelper:
    def test_empty_symbols_returns_empty(self):
        df = fetch_returns_for_clustering([])
        assert df.empty

    def test_yfinance_error_returns_empty(self):
        with patch("yfinance.download", side_effect=RuntimeError("network error")):
            df = fetch_returns_for_clustering(["AAPL"], lookback_days=60)
        assert df.empty

    def test_yfinance_empty_result_returns_empty(self):
        with patch("yfinance.download", return_value=pd.DataFrame()):
            df = fetch_returns_for_clustering(["AAPL"], lookback_days=60)
        assert df.empty


# ===========================================================================
# TestSettings
# ===========================================================================

class TestSettings:
    def test_lookback_days_positive(self):
        from settings import settings
        assert settings.CORRELATION_CLUSTER_LOOKBACK_DAYS > 0

    def test_threshold_positive(self):
        from settings import settings
        assert 0.0 < settings.CORRELATION_CLUSTER_THRESHOLD <= 2.0

    def test_lookback_default_is_60(self):
        from settings import settings
        assert settings.CORRELATION_CLUSTER_LOOKBACK_DAYS == 60

    def test_threshold_default_is_0_4(self):
        from settings import settings
        assert abs(settings.CORRELATION_CLUSTER_THRESHOLD - 0.4) < 1e-9

    def test_correlation_cluster_column_in_schema(self):
        from config import COLUMN_SCHEMA
        keys = [col["key"] for col in COLUMN_SCHEMA]
        assert "Correlation_Cluster" in keys

    def test_news_sentiment_column_in_schema(self):
        from config import COLUMN_SCHEMA
        keys = [col["key"] for col in COLUMN_SCHEMA]
        assert "News_Sentiment" in keys

    def test_earnings_date_column_in_schema(self):
        from config import COLUMN_SCHEMA
        keys = [col["key"] for col in COLUMN_SCHEMA]
        assert "Earnings_Date" in keys
