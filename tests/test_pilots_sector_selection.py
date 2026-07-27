"""Tests for pilots/sector_selection.py -- the Sector Selection ranking
reader powering GET /sector/selection."""
from __future__ import annotations

from unittest.mock import MagicMock

from data.sector_correlation_store import SectorCorrelationStore
from pilots.sector_selection import sector_selection_view


def _row(sector, rank, coefficient, **overrides):
    defaults = dict(
        sector=sector,
        cosine_similarity=0.6,
        ingestion_volume=10.0,
        sector_heat_factor=0.7,
        correlation_coefficient=coefficient,
        rank=rank,
        selected=rank is not None and rank <= 3,
        degraded_reason=None,
        embedder="sbert",
        pooling="max",
    )
    defaults.update(overrides)
    return defaults


class TestEmptyAndColdStart:
    def test_blank_target_short_circuits(self):
        result = sector_selection_view("", 3)
        assert result["rows"] == []
        assert result["reason"] is not None

    def test_no_history_returns_reason_not_error(self):
        store = SectorCorrelationStore(db_url="sqlite:///:memory:")
        result = sector_selection_view("NIO", 3, store=store)
        assert result["target_symbol"] == "NIO"
        assert result["rows"] == []
        assert result["as_of"] is None
        assert result["reason"] is not None

    def test_store_construction_failure_degrades_to_empty(self, monkeypatch):
        """CONSTRAINT #6: a DB-unreachable failure must never raise -- and
        must not silently fabricate a 'no data' answer that hides a real
        outage from the reason field's absence... it still gets an honest
        reason, just the generic 'nothing computed' one at this layer."""
        import data.sector_correlation_store as store_module

        def _boom(*args, **kwargs):
            raise RuntimeError("db unreachable")

        monkeypatch.setattr(store_module, "SectorCorrelationStore", _boom)
        result = sector_selection_view("NIO", 3)
        assert result["rows"] == []
        assert result["reason"] is not None


class TestRankingView:
    def test_round_trip_full_shape(self):
        store = SectorCorrelationStore(db_url="sqlite:///:memory:")
        store.record_correlations(
            [_row("Technology", 1, 0.5), _row("Healthcare", 2, 0.3)],
            as_of="2026-07-21", target_symbol="NIO",
        )
        result = sector_selection_view("NIO", 3, store=store)
        assert result["target_symbol"] == "NIO"
        assert result["as_of"] == "2026-07-21"
        assert result["top_n"] == 3
        assert result["reason"] is None
        assert result["embedder"] == "sbert"
        assert result["pooling"] == "max"
        assert [r["sector"] for r in result["rows"]] == ["Technology", "Healthcare"]

    def test_lowercase_target_uppercased(self):
        store = SectorCorrelationStore(db_url="sqlite:///:memory:")
        store.record_correlations([_row("Technology", 1, 0.5)], as_of="2026-07-21", target_symbol="NIO")
        result = sector_selection_view("nio", 3, store=store)
        assert result["target_symbol"] == "NIO"
        assert result["rows"] != []

    def test_null_fields_pass_through_as_none_not_zero(self):
        """CONSTRAINT #4: a degraded row's None fields must render as null
        in the view, never coerced to a fabricated 0.0."""
        store = SectorCorrelationStore(db_url="sqlite:///:memory:")
        store.record_correlations(
            [_row("Technology", None, None, cosine_similarity=None, selected=False,
                  degraded_reason="no_target_description")],
            as_of="2026-07-21", target_symbol="NIO",
        )
        row = sector_selection_view("NIO", 3, store=store)["rows"][0]
        assert row["cosine_similarity"] is None
        assert row["correlation_coefficient"] is None
        assert row["rank"] is None
        assert row["selected"] is False
        assert row["degraded_reason"] == "no_target_description"


class TestNSliderReRanking:
    """The whole point of the view layer: changing n re-derives `selected`
    from the already-persisted rank ordering, without touching similarity
    or heat computation at all."""

    def _seeded_store(self):
        store = SectorCorrelationStore(db_url="sqlite:///:memory:")
        store.record_correlations(
            [
                _row("First", 1, 0.9),
                _row("Second", 2, 0.7),
                _row("Third", 3, 0.5),
                _row("Fourth", 4, 0.3),
            ],
            as_of="2026-07-21", target_symbol="NIO",
        )
        return store

    def test_n_equals_2_selects_only_top_two(self):
        result = sector_selection_view("NIO", 2, store=self._seeded_store())
        selected = {r["sector"]: r["selected"] for r in result["rows"]}
        assert selected == {"First": True, "Second": True, "Third": False, "Fourth": False}

    def test_n_equals_4_selects_all(self):
        result = sector_selection_view("NIO", 4, store=self._seeded_store())
        assert all(r["selected"] for r in result["rows"])

    def test_n_equals_1_selects_only_top_one(self):
        result = sector_selection_view("NIO", 1, store=self._seeded_store())
        selected = {r["sector"]: r["selected"] for r in result["rows"]}
        assert selected["First"] is True
        assert selected["Second"] is False

    def test_unranked_row_never_selected_regardless_of_n(self):
        store = SectorCorrelationStore(db_url="sqlite:///:memory:")
        store.record_correlations(
            [_row("Unranked", None, None, selected=False, degraded_reason="no_volume_observed")],
            as_of="2026-07-21", target_symbol="NIO",
        )
        result = sector_selection_view("NIO", 5, store=store)
        assert result["rows"][0]["selected"] is False

    def test_reported_top_n_reflects_the_request_not_the_stored_value(self):
        result = sector_selection_view("NIO", 2, store=self._seeded_store())
        assert result["top_n"] == 2


class TestReadFailureResilience:
    def test_get_latest_exception_degrades_to_empty(self):
        store = MagicMock()
        store.get_latest.side_effect = RuntimeError("simulated failure")
        result = sector_selection_view("NIO", 3, store=store)
        assert result["rows"] == []
        assert result["reason"] is not None
