"""Tests for sector_selection_engine.py -- the semantic Related Sector
Selection daily orchestration: sector membership -> descriptions ->
embeddings -> cosine similarity -> Sector Heat Factor ->
correlation_coefficient -> rank -> top-N selection -> persist.

Heavy dependencies (SBERT, HistoricalStore, SectorCorrelationStore) are
mocked at their source modules throughout -- sentence-transformers is not
installed in this environment, and these tests are about the ORCHESTRATION
logic, not embedding numerics (covered separately in
tests/test_sector_embeddings.py)."""
from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from sector_selection_engine import run_sector_selection


def _heat_entry(shf=0.5, news_volume=10.0, review_volume=2.0, degraded_reason=None):
    return {
        "shf": shf, "news_volume": news_volume,
        "review_volume": review_volume, "degraded_reason": degraded_reason,
    }


def _fake_store():
    store = MagicMock()
    store.resolve_trading_day.return_value = "2026-07-21"
    return store


class TestGatingAndEmptyInputs:
    def test_disabled_returns_empty_and_never_touches_heavy_deps(self):
        heat_mock = MagicMock()
        descriptions_mock = MagicMock()
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", False), \
             patch("data.sector_selection_heat.compute_spec_sector_heat", heat_mock), \
             patch("data.sector_embeddings.load_sector_descriptions", descriptions_mock):
            result = run_sector_selection(["NIO"], historical_store=_fake_store())
        assert result == {}
        heat_mock.assert_not_called()
        descriptions_mock.assert_not_called()

    def test_empty_targets_returns_empty(self):
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True):
            result = run_sector_selection([], historical_store=_fake_store())
        assert result == {}


class TestFullRankingPipeline:
    def _run(self, targets=("NIO",), top_n=None, correlation_store=None, target_description="NIO makes EVs."):
        vectors = {
            "NIO makes EVs.": [1.0, 0.0],
            "desc-tech": [1.0, 0.0],   # cos=1.0 vs target
            "desc-health": [0.0, 1.0],  # cos=0.0 vs target
            "desc-energy": [-1.0, 0.0],  # cos=-1.0 vs target
        }

        def fake_embed(text, pooling=None):
            v = vectors.get(text)
            return np.array(v, dtype=float) if v is not None else None

        heat = {
            "Technology": _heat_entry(shf=0.5),
            "Healthcare": _heat_entry(shf=0.5),
            "Energy": _heat_entry(shf=0.5),
        }
        descriptions = {"Technology": "desc-tech", "Healthcare": "desc-health", "Energy": "desc-energy"}
        sector_map = {"AAPL": "Technology", "JNJ": "Healthcare", "XOM": "Energy"}

        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True), \
             patch("settings.settings.SECTOR_SELECTION_TOP_N", 3), \
             patch("settings.settings.SECTOR_SIMILARITY_EMBEDDER", "sbert"), \
             patch("settings.settings.SECTOR_SIMILARITY_POOLING", "max"), \
             patch("data.sector_embeddings.SBERT_AVAILABLE", True), \
             patch("data.sector_selection_heat.compute_spec_sector_heat", return_value=heat), \
             patch("data.sector_embeddings.load_sector_descriptions", return_value=descriptions), \
             patch("engine.portfolio_exposure._load_sector_map", return_value=sector_map), \
             patch("data.sector_embeddings.resolve_target_description", return_value=target_description), \
             patch("data.sector_embeddings.embed_text", side_effect=fake_embed):
            return run_sector_selection(
                list(targets), top_n=top_n, historical_store=_fake_store(),
                correlation_store=correlation_store or MagicMock(),
            )

    def test_ranks_descending_by_correlation_coefficient(self):
        result = self._run()
        rows = result["NIO"]
        assert [r["sector"] for r in rows] == ["Technology", "Healthcare", "Energy"]
        assert rows[0]["correlation_coefficient"] == pytest.approx(0.5)   # 1.0 * 0.5
        assert rows[1]["correlation_coefficient"] == pytest.approx(0.0)   # 0.0 * 0.5
        assert rows[2]["correlation_coefficient"] == pytest.approx(-0.5)  # -1.0 * 0.5

    def test_rank_and_selected_respect_top_n(self):
        result = self._run(top_n=2)
        rows = {r["sector"]: r for r in result["NIO"]}
        assert rows["Technology"]["rank"] == 1 and rows["Technology"]["selected"] is True
        assert rows["Healthcare"]["rank"] == 2 and rows["Healthcare"]["selected"] is True
        assert rows["Energy"]["rank"] == 3 and rows["Energy"]["selected"] is False

    def test_top_n_parameter_overrides_settings_default(self):
        result = self._run(top_n=1)
        rows = {r["sector"]: r for r in result["NIO"]}
        assert rows["Technology"]["selected"] is True
        assert rows["Healthcare"]["selected"] is False

    def test_no_degraded_reason_when_everything_available(self):
        result = self._run()
        assert all(r["degraded_reason"] is None for r in result["NIO"])

    def test_embedder_and_pooling_recorded_as_provenance(self):
        result = self._run()
        for row in result["NIO"]:
            assert row["embedder"] == "sbert"
            assert row["pooling"] == "max"

    def test_persistence_called_with_full_ranking(self):
        store = MagicMock()
        self._run(correlation_store=store)
        store.record_correlations.assert_called_once()
        _, kwargs = store.record_correlations.call_args
        assert kwargs["as_of"] == "2026-07-21"
        assert kwargs["target_symbol"] == "NIO"

    def test_persistence_failure_does_not_lose_computed_ranking(self):
        store = MagicMock()
        store.record_correlations.side_effect = RuntimeError("db down")
        result = self._run(correlation_store=store)
        assert len(result["NIO"]) == 3  # ranking survives even though persistence failed


class TestHonestDegradation:
    def test_missing_target_description_degrades_all_rows(self):
        result = self._helper(target_description=None)
        for row in result["NIO"]:
            assert row["cosine_similarity"] is None
            assert row["correlation_coefficient"] is None
            assert row["degraded_reason"] == "no_target_description"

    def test_heat_degraded_reason_takes_priority_over_similarity(self):
        """When BOTH heat and similarity have something to report,
        heat's degraded_reason (a real, measured fact about the volume
        data) wins over a similarity-side reason -- but here similarity
        succeeds fine, so heat's reason is simply the only one present."""
        heat = {
            "Technology": _heat_entry(shf=0.3, degraded_reason="review_unavailable"),
        }
        descriptions = {"Technology": "desc-tech"}
        sector_map = {"AAPL": "Technology"}
        vectors = {"NIO makes EVs.": [1.0, 0.0], "desc-tech": [1.0, 0.0]}

        def fake_embed(text, pooling=None):
            v = vectors.get(text)
            return np.array(v, dtype=float) if v is not None else None

        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True), \
             patch("settings.settings.SECTOR_SELECTION_TOP_N", 3), \
             patch("settings.settings.SECTOR_SIMILARITY_EMBEDDER", "sbert"), \
             patch("settings.settings.SECTOR_SIMILARITY_POOLING", "max"), \
             patch("data.sector_embeddings.SBERT_AVAILABLE", True), \
             patch("data.sector_selection_heat.compute_spec_sector_heat", return_value=heat), \
             patch("data.sector_embeddings.load_sector_descriptions", return_value=descriptions), \
             patch("engine.portfolio_exposure._load_sector_map", return_value=sector_map), \
             patch("data.sector_embeddings.resolve_target_description", return_value="NIO makes EVs."), \
             patch("data.sector_embeddings.embed_text", side_effect=fake_embed):
            result = run_sector_selection(
                ["NIO"], historical_store=_fake_store(), correlation_store=MagicMock(),
            )
        row = result["NIO"][0]
        assert row["cosine_similarity"] == pytest.approx(1.0)  # similarity itself succeeded
        # SHF is still a REAL, computed value in degraded mode (news-only
        # volume) -- degradation is a provenance flag, not a NaN-forcing
        # event, so the coefficient computes normally from real inputs.
        assert row["correlation_coefficient"] == pytest.approx(0.3)  # 1.0 * 0.3
        assert row["degraded_reason"] == "review_unavailable"

    def test_blocking_similarity_reason_not_masked_by_informational_heat_reason(self):
        """Regression (secondary audit, 2026-08-24): when similarity ITSELF
        fails (correlation_coefficient is None because of it, not merely
        because of heat) at the same time heat's own degraded_reason happens
        to be set too, the ACTUAL blocking cause (similarity_reason) must be
        reported -- not silently masked by heat's routine, non-blocking
        provenance flag. Before the fix, `heat_degraded_reason or
        similarity_reason` always reported "review_unavailable" here even
        though similarity (no_target_description) is what actually made the
        row un-rankable.
        """
        heat = {
            "Technology": _heat_entry(shf=0.3, degraded_reason="review_unavailable"),
        }
        descriptions = {"Technology": "desc-tech"}
        sector_map = {"AAPL": "Technology"}

        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True), \
             patch("settings.settings.SECTOR_SELECTION_TOP_N", 3), \
             patch("settings.settings.SECTOR_SIMILARITY_EMBEDDER", "sbert"), \
             patch("settings.settings.SECTOR_SIMILARITY_POOLING", "max"), \
             patch("data.sector_embeddings.SBERT_AVAILABLE", True), \
             patch("data.sector_selection_heat.compute_spec_sector_heat", return_value=heat), \
             patch("data.sector_embeddings.load_sector_descriptions", return_value=descriptions), \
             patch("engine.portfolio_exposure._load_sector_map", return_value=sector_map), \
             patch("data.sector_embeddings.resolve_target_description", return_value=None), \
             patch("data.sector_embeddings.embed_text", return_value=None):
            result = run_sector_selection(
                ["NIO"], historical_store=_fake_store(), correlation_store=MagicMock(),
            )
        row = result["NIO"][0]
        assert row["cosine_similarity"] is None
        assert row["correlation_coefficient"] is None
        # The real, blocking reason -- not heat's merely-informational one.
        assert row["degraded_reason"] == "no_target_description"

    def test_embedder_none_marks_no_embedder(self):
        heat = {"Technology": _heat_entry(shf=0.5)}
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True), \
             patch("settings.settings.SECTOR_SELECTION_TOP_N", 3), \
             patch("settings.settings.SECTOR_SIMILARITY_EMBEDDER", "none"), \
             patch("settings.settings.SECTOR_SIMILARITY_POOLING", "max"), \
             patch("data.sector_selection_heat.compute_spec_sector_heat", return_value=heat), \
             patch("data.sector_embeddings.load_sector_descriptions", return_value={"Technology": "desc-tech"}), \
             patch("engine.portfolio_exposure._load_sector_map", return_value={"AAPL": "Technology"}), \
             patch("data.sector_embeddings.resolve_target_description", return_value="NIO makes EVs."):
            result = run_sector_selection(
                ["NIO"], historical_store=_fake_store(), correlation_store=MagicMock(),
            )
        row = result["NIO"][0]
        assert row["cosine_similarity"] is None
        assert row["degraded_reason"] == "no_embedder"

    def _helper(self, **kwargs):
        return TestFullRankingPipeline._run(self, **kwargs)


class TestPerTargetResilience:
    def test_one_bad_target_does_not_abort_others(self):
        heat = {"Technology": _heat_entry(shf=0.5)}
        descriptions = {"Technology": "desc-tech"}
        sector_map = {"AAPL": "Technology"}

        def fake_resolve(symbol, historical_store=None, as_of=None):
            if symbol == "BAD":
                raise RuntimeError("simulated failure resolving BAD's description")
            return "Good target description."

        def fake_embed(text, pooling=None):
            return np.array([1.0, 0.0])

        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True), \
             patch("settings.settings.SECTOR_SELECTION_TOP_N", 3), \
             patch("settings.settings.SECTOR_SIMILARITY_EMBEDDER", "sbert"), \
             patch("settings.settings.SECTOR_SIMILARITY_POOLING", "max"), \
             patch("data.sector_embeddings.SBERT_AVAILABLE", True), \
             patch("data.sector_selection_heat.compute_spec_sector_heat", return_value=heat), \
             patch("data.sector_embeddings.load_sector_descriptions", return_value=descriptions), \
             patch("engine.portfolio_exposure._load_sector_map", return_value=sector_map), \
             patch("data.sector_embeddings.resolve_target_description", side_effect=fake_resolve), \
             patch("data.sector_embeddings.embed_text", side_effect=fake_embed):
            result = run_sector_selection(
                ["BAD", "GOOD"], historical_store=_fake_store(), correlation_store=MagicMock(),
            )
        assert result["BAD"] == []
        assert len(result["GOOD"]) == 1


class TestBuildCorrelationStoreFallback:
    def test_construction_failure_degrades_to_offline_store(self):
        from sector_selection_engine import _build_correlation_store
        with patch(
            "data.sector_correlation_store.SectorCorrelationStore",
            side_effect=RuntimeError("db unreachable"),
        ):
            store = _build_correlation_store()
        from data.sector_correlation_store import _OfflineSectorCorrelationStore
        assert isinstance(store, _OfflineSectorCorrelationStore)
