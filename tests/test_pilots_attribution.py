"""Unit tests for ``pilots/attribution.py`` — the pure factor-exposure /
correlation-cluster attribution math behind ``GET /portfolio/attribution``.

All fixtures are offline: hand-built snapshot dicts and small synthetic pandas
DataFrames for the correlation-cluster cases. No network, no heavy engines,
no FastAPI app (see ``tests/test_pilots_api.py::TestPortfolioAttribution`` for
the endpoint-level integration tests).
"""
from __future__ import annotations

import random

import pandas as pd
import pytest

from pilots.attribution import (
    portfolio_correlation_clusters,
    portfolio_factor_exposure,
)


def _snapshot(signals):
    return {"timestamp": "2026-07-11T21:05:00+00:00", "signals": signals}


def _sig(symbol, **kw):
    row = {"symbol": symbol}
    row.update(kw)
    return row


# ---------------------------------------------------------------------------
# portfolio_factor_exposure
# ---------------------------------------------------------------------------


class TestPortfolioFactorExposure:
    def test_no_held_positions(self):
        result = portfolio_factor_exposure(_snapshot([]), {})
        assert result["reason"] == "no held positions"
        assert all(v is None for v in result["exposures"].values())
        assert result["coverage"] == {
            "held_count": 0, "matched_count": 0,
            "matched_value_pct": None, "unmatched_symbols": [],
        }

    def test_no_snapshot_yet(self):
        result = portfolio_factor_exposure(None, {"AAPL": 1000.0})
        assert result["reason"] == "no pipeline snapshot yet"
        assert result["as_of"] is None
        assert all(v is None for v in result["exposures"].values())
        assert result["coverage"]["held_count"] == 1
        assert result["coverage"]["matched_count"] == 0
        assert result["coverage"]["unmatched_symbols"] == ["AAPL"]

    def test_weights_by_market_value(self):
        snap = _snapshot([
            _sig("AAPL", value_z=1.0, quality_z=2.0, lowvol_z=0.0, size_z=0.0,
                 multifactor_composite=1.5),
            _sig("MSFT", value_z=-1.0, quality_z=0.0, lowvol_z=0.0, size_z=0.0,
                 multifactor_composite=-0.5),
        ])
        # AAPL is 3x MSFT's market value -> exposure should skew toward AAPL.
        held = {"AAPL": 3000.0, "MSFT": 1000.0}
        result = portfolio_factor_exposure(snap, held)
        assert result["reason"] is None
        assert result["as_of"] == "2026-07-11T21:05:00+00:00"
        expected_value_z = (1.0 * 3000.0 + -1.0 * 1000.0) / 4000.0
        assert result["exposures"]["value_z"] == pytest.approx(expected_value_z)
        expected_composite = (1.5 * 3000.0 + -0.5 * 1000.0) / 4000.0
        assert result["exposures"]["multifactor_composite"] == pytest.approx(expected_composite)
        assert result["coverage"]["matched_value_pct"] == pytest.approx(1.0)

    def test_unmatched_symbol_never_zero_filled(self):
        snap = _snapshot([_sig("AAPL", value_z=1.0, quality_z=1.0, lowvol_z=1.0,
                                size_z=1.0, multifactor_composite=1.0)])
        held = {"AAPL": 1000.0, "GHOST": 500.0}
        result = portfolio_factor_exposure(snap, held)
        # GHOST has no snapshot entry -> excluded from the average entirely,
        # not treated as a 0.0 exposure (which would drag the average down).
        assert result["exposures"]["value_z"] == pytest.approx(1.0)
        assert result["coverage"]["held_count"] == 2
        assert result["coverage"]["matched_count"] == 1
        assert result["coverage"]["unmatched_symbols"] == ["GHOST"]
        assert result["coverage"]["matched_value_pct"] == pytest.approx(1000.0 / 1500.0)

    def test_nan_or_non_positive_market_value_excluded(self):
        snap = _snapshot([
            _sig("AAPL", value_z=1.0, quality_z=1.0, lowvol_z=1.0, size_z=1.0,
                 multifactor_composite=1.0),
            _sig("MSFT", value_z=5.0, quality_z=5.0, lowvol_z=5.0, size_z=5.0,
                 multifactor_composite=5.0),
        ])
        held = {"AAPL": 1000.0, "MSFT": float("nan")}
        result = portfolio_factor_exposure(snap, held)
        # MSFT's NaN market value means it can't be weighted -> excluded, not
        # treated as a real zero-weight contributor.
        assert result["exposures"]["value_z"] == pytest.approx(1.0)
        assert result["coverage"]["matched_count"] == 1
        assert result["coverage"]["unmatched_symbols"] == ["MSFT"]

    def test_per_factor_independence_missing_one_factor(self):
        """A symbol missing just `size_z` (e.g. an older snapshot) must not
        drag down or fabricate that ONE factor while others compute fine."""
        snap = _snapshot([
            _sig("AAPL", value_z=1.0, quality_z=1.0, lowvol_z=1.0,
                 multifactor_composite=1.0),  # size_z absent
            _sig("MSFT", value_z=3.0, quality_z=3.0, lowvol_z=3.0, size_z=3.0,
                 multifactor_composite=3.0),
        ])
        held = {"AAPL": 1000.0, "MSFT": 1000.0}
        result = portfolio_factor_exposure(snap, held)
        # value_z/quality_z/lowvol_z/multifactor_composite average both names.
        assert result["exposures"]["value_z"] == pytest.approx(2.0)
        # size_z only has MSFT's contribution (AAPL's is missing, not 0).
        assert result["exposures"]["size_z"] == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# portfolio_correlation_clusters
# ---------------------------------------------------------------------------


def _price_frame(closes):
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    return pd.Series(closes, index=idx)


class TestPortfolioCorrelationClusters:
    def test_no_held_positions(self):
        result = portfolio_correlation_clusters(pd.DataFrame(), {})
        assert result == {"clusters": [], "reason": "no held positions"}

    def test_empty_returns_df(self):
        result = portfolio_correlation_clusters(pd.DataFrame(), {"AAPL": 1000.0})
        assert result["clusters"] == []
        assert result["reason"] == "no return history available for held positions"

    def test_none_returns_df(self):
        result = portfolio_correlation_clusters(None, {"AAPL": 1000.0})
        assert result["clusters"] == []
        assert result["reason"] == "no return history available for held positions"

    def test_correlated_symbols_share_a_cluster_with_honest_weights(self):
        n = 40
        rng_a = random.Random(42)
        aapl = [100.0]
        for _ in range(n - 1):
            aapl.append(aapl[-1] * (1.0 + rng_a.uniform(-0.015, 0.02)))
        msft = [c * 2.0 for c in aapl]  # identical returns -> perfectly correlated
        rng_b = random.Random(7)
        nvda = [200.0]
        for _ in range(n - 1):
            nvda.append(nvda[-1] * (1.0 + rng_b.uniform(-0.02, 0.02)))

        prices = pd.DataFrame({
            "AAPL": _price_frame(aapl),
            "MSFT": _price_frame(msft),
            "NVDA": _price_frame(nvda),
        })
        returns = prices.pct_change().dropna(how="all")

        held = {"AAPL": 1000.0, "MSFT": 1000.0, "NVDA": 2000.0}
        result = portfolio_correlation_clusters(returns, held, min_obs=20)
        assert result["reason"] is None
        clusters = result["clusters"]
        assert clusters

        aapl_cluster = next(c for c in clusters if "AAPL" in c["symbols"])
        assert "MSFT" in aapl_cluster["symbols"]
        # weight_pct = (1000+1000)/4000 for the AAPL/MSFT cluster.
        assert aapl_cluster["weight_pct"] == pytest.approx(2000.0 / 4000.0)
        assert aapl_cluster["insufficient_history"] is False

        total_weight = sum(c["weight_pct"] or 0.0 for c in clusters)
        assert total_weight == pytest.approx(1.0, abs=1e-6)

    def test_cluster_zero_flagged_as_insufficient_history(self):
        """A symbol with too few observations (< min_obs) lands in
        research_engine's cluster 0 ('insufficient history') bucket, which this
        module must flag rather than presenting as a real correlation group."""
        n = 40
        aapl = [100.0 + i for i in range(n)]
        # SHORT is a real column but has far fewer non-NaN rows than min_obs.
        short_vals = [None] * (n - 5) + [50.0, 51.0, 49.0, 52.0, 53.0]
        prices = pd.DataFrame({"AAPL": _price_frame(aapl), "SHORT": _price_frame(short_vals)})
        returns = prices.pct_change().dropna(how="all")

        held = {"AAPL": 1000.0, "SHORT": 500.0}
        result = portfolio_correlation_clusters(returns, held, min_obs=20)
        assert result["reason"] is None
        short_cluster = next(c for c in result["clusters"] if "SHORT" in c["symbols"])
        assert short_cluster["cluster_id"] == 0
        assert short_cluster["insufficient_history"] is True
        assert short_cluster["avg_intra_corr"] is None

    def test_research_engine_import_failure_degrades_honestly(self, monkeypatch):
        import pilots.attribution as attribution_mod

        def _boom():
            raise ImportError("scipy not installed")

        # Simulate research_engine itself being unimportable by making the
        # lazy `from research_engine import compute_correlation_clusters`
        # raise inside the function body.
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "research_engine":
                raise ImportError("simulated missing research_engine")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        prices = pd.DataFrame({"AAPL": _price_frame([100.0 + i for i in range(30)])})
        returns = prices.pct_change().dropna(how="all")
        result = attribution_mod.portfolio_correlation_clusters(returns, {"AAPL": 1000.0})
        assert result["clusters"] == []
        assert result["reason"] == "clustering engine unavailable"

    def test_never_raises_on_malformed_summary(self, monkeypatch):
        """A compute_correlation_clusters that returns a malformed summary
        (missing cluster_id) must not crash the endpoint."""
        import pilots.attribution as attribution_mod

        def fake_compute(returns_df, distance_threshold=0.4, min_obs=20):
            return {"AAPL": 1, "MSFT": 1}, pd.DataFrame([{"not_cluster_id": 1}])

        monkeypatch.setattr(
            "research_engine.compute_correlation_clusters", fake_compute, raising=False
        )
        prices = pd.DataFrame({
            "AAPL": _price_frame([100.0 + i for i in range(30)]),
            "MSFT": _price_frame([200.0 + i for i in range(30)]),
        })
        returns = prices.pct_change().dropna(how="all")
        result = attribution_mod.portfolio_correlation_clusters(
            returns, {"AAPL": 1000.0, "MSFT": 1000.0}
        )
        assert result["reason"] is None
        assert len(result["clusters"]) == 1
        assert result["clusters"][0]["avg_intra_corr"] is None
